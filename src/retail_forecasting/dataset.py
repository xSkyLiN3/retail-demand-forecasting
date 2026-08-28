from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from retail_forecasting.config import (
    COHORT_MAX_SKUS,
    COHORT_MIN_ACTIVE_DAYS,
    COHORT_RECENCY_DAYS,
    COHORT_TRAINING_DAYS,
)
from retail_forecasting.source import sha256_file

REQUIRED_COLUMNS = {
    "invoice_no",
    "stock_code",
    "description",
    "quantity",
    "invoice_date",
    "unit_price",
    "customer_id",
    "country",
}
PRODUCT_CODE_PATTERN = re.compile(r"^[0-9]{5}[A-Z]{0,2}$", re.IGNORECASE)

COLUMN_ALIASES = {
    "invoice": "invoice_no",
    "invoiceno": "invoice_no",
    "stockcode": "stock_code",
    "description": "description",
    "quantity": "quantity",
    "invoicedate": "invoice_date",
    "price": "unit_price",
    "unitprice": "unit_price",
    "customerid": "customer_id",
    "country": "country",
    "sourcesheet": "source_sheet",
}


class DataContractError(ValueError):
    """Raised when source or derived data violates the documented contract."""


@dataclass(frozen=True)
class PreparedDataset:
    panel: pd.DataFrame
    cohort: tuple[str, ...]
    cohort_cutoff: pd.Timestamp
    quality: dict[str, Any]
    selection: dict[str, int]
    source: dict[str, Any]
    target: dict[str, str]


@dataclass(frozen=True)
class LoadedWorkbook:
    transactions: pd.DataFrame
    audit: dict[str, Any]


def _positive_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer.")
    result = int(value)
    if result < 1:
        raise ValueError(f"{name} must be at least 1.")
    return result


def target_contract() -> dict[str, str]:
    return {
        "calendar_policy": "complete_calendar_with_source_observed_day_flag",
        "cancellation_invoice_prefix": "C",
        "name": "gross_positive_invoiced_units",
        "product_code_pattern": PRODUCT_CODE_PATTERN.pattern,
        "quantity_rule": "quantity > 0",
        "unit_price_rule": "unit_price > 0",
    }


def _canonical_column(value: object) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value).strip().lower())
    return COLUMN_ALIASES.get(key, key)


def _identifier(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and np.isfinite(value) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    canonical_columns = [_canonical_column(column) for column in frame.columns]
    duplicate_mask = pd.Index(canonical_columns).duplicated(keep=False)
    if duplicate_mask.any():
        duplicates = sorted(set(pd.Index(canonical_columns)[duplicate_mask]))
        raise DataContractError(f"Columns collide after normalization: {duplicates}")
    normalized = frame.copy()
    normalized.columns = canonical_columns
    missing = REQUIRED_COLUMNS.difference(normalized.columns)
    if missing:
        raise DataContractError(f"Missing required columns: {sorted(missing)}")

    selected_columns = sorted(REQUIRED_COLUMNS)
    if "source_sheet" in normalized.columns:
        selected_columns.append("source_sheet")
    result = normalized.loc[:, selected_columns].copy()
    result["invoice_no"] = result["invoice_no"].map(_identifier)
    result["stock_code"] = result["stock_code"].map(_identifier).str.upper()
    result["country"] = result["country"].fillna("").astype(str).str.strip()

    quantity = pd.to_numeric(result["quantity"], errors="coerce")
    invalid_quantity = quantity.isna() | ~np.isfinite(quantity) | (quantity % 1 != 0)
    if invalid_quantity.any():
        raise DataContractError(f"Invalid quantity rows: {int(invalid_quantity.sum())}")
    result["quantity"] = quantity.astype("int64")

    price = pd.to_numeric(result["unit_price"], errors="coerce")
    invalid_price = price.isna() | ~np.isfinite(price)
    if invalid_price.any():
        raise DataContractError(f"Invalid unit-price rows: {int(invalid_price.sum())}")
    result["unit_price"] = price.astype("float64")

    timestamp = pd.to_datetime(result["invoice_date"], errors="coerce", format="mixed")
    if timestamp.isna().any():
        raise DataContractError(f"Invalid invoice-date rows: {int(timestamp.isna().sum())}")
    result["invoice_date"] = timestamp

    empty_invoice = result["invoice_no"].eq("")
    empty_stock = result["stock_code"].eq("")
    empty_country = result["country"].eq("")
    if empty_invoice.any() or empty_stock.any() or empty_country.any():
        raise DataContractError(
            "Required identifiers are empty: "
            f"invoice={int(empty_invoice.sum())}, "
            f"stock={int(empty_stock.sum())}, country={int(empty_country.sum())}"
        )

    if "source_sheet" in result.columns:
        result["source_sheet"] = result["source_sheet"].fillna("").astype(str).str.strip()
        if result["source_sheet"].eq("").any():
            raise DataContractError("Source-sheet identifiers cannot be empty when provided.")

    return result


def load_workbook(path: Path) -> LoadedWorkbook:
    if not path.is_file():
        raise FileNotFoundError(f"Workbook not found: {path}")

    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    if not sheets:
        raise DataContractError("Workbook contains no worksheets.")

    normalized_sheets: list[pd.DataFrame] = []
    sheet_summaries: list[dict[str, Any]] = []
    fingerprint_columns = sorted(REQUIRED_COLUMNS)
    within_sheet_duplicates = 0
    for sheet_order, (sheet_name, frame) in enumerate(sheets.items()):
        normalized = normalize_transactions(frame)
        normalized.insert(0, "source_sheet", str(sheet_name))
        normalized.insert(1, "_source_sheet_order", sheet_order)
        normalized["_within_sheet_occurrence"] = normalized.groupby(
            fingerprint_columns,
            dropna=False,
            sort=False,
        ).cumcount()
        within_sheet_duplicates += int(
            normalized.duplicated(fingerprint_columns, keep="first").sum()
        )
        sheet_summaries.append(
            {
                "end": normalized["invoice_date"].max().isoformat(),
                "name": str(sheet_name),
                "rows": int(len(normalized)),
                "start": normalized["invoice_date"].min().isoformat(),
            }
        )
        normalized_sheets.append(normalized)

    combined = pd.concat(normalized_sheets, ignore_index=True)
    occurrence_columns = [*fingerprint_columns, "_within_sheet_occurrence"]
    cross_sheet_mask = combined.duplicated(occurrence_columns, keep=False)
    repeated = combined.loc[cross_sheet_mask]
    rows_to_remove = combined.duplicated(occurrence_columns, keep="first")

    unequal_multiplicity_groups = 0
    cross_sheet_value_groups = 0
    if not repeated.empty:
        sheet_multiplicity = (
            combined.groupby(
                [*fingerprint_columns, "source_sheet"],
                dropna=False,
                sort=False,
            )
            .size()
            .unstack(fill_value=0)
        )
        sheet_multiplicity = sheet_multiplicity.loc[sheet_multiplicity.gt(0).sum(axis=1).gt(1)]
        cross_sheet_value_groups = int(len(sheet_multiplicity))
        unequal_multiplicity_groups = int(sheet_multiplicity.nunique(axis=1).gt(1).sum())

    deduplicated = combined.loc[~rows_to_remove].copy()
    deduplicated = deduplicated.sort_values(
        ["_source_sheet_order", "invoice_date"],
        kind="stable",
        ignore_index=True,
    ).drop(columns=["_source_sheet_order", "_within_sheet_occurrence"])
    audit = {
        "cross_sheet_exact_occurrence_groups": int(
            repeated[occurrence_columns].drop_duplicates().shape[0]
        ),
        "cross_sheet_exact_rows_affected": int(cross_sheet_mask.sum()),
        "cross_sheet_exact_rows_removed": int(rows_to_remove.sum()),
        "cross_sheet_exact_value_groups": cross_sheet_value_groups,
        "cross_sheet_multiplicity_mismatch_groups": unequal_multiplicity_groups,
        "cross_sheet_overlap_end": (
            repeated["invoice_date"].max().isoformat() if not repeated.empty else None
        ),
        "cross_sheet_overlap_start": (
            repeated["invoice_date"].min().isoformat() if not repeated.empty else None
        ),
        "cross_sheet_policy": "multiset_union_keep_earliest_sheet",
        "rows_after_cross_sheet_union": int(len(deduplicated)),
        "rows_before_cross_sheet_union": int(len(combined)),
        "sheets": sheet_summaries,
        "within_sheet_exact_duplicates_beyond_first": within_sheet_duplicates,
    }
    return LoadedWorkbook(transactions=deduplicated, audit=audit)


def prepare_daily_panel(
    transactions: pd.DataFrame,
    *,
    cohort_training_days: int = COHORT_TRAINING_DAYS,
    min_active_days: int = COHORT_MIN_ACTIVE_DAYS,
    max_skus: int = COHORT_MAX_SKUS,
    recency_days: int = COHORT_RECENCY_DAYS,
    source_metadata: Mapping[str, Any] | None = None,
) -> PreparedDataset:
    cohort_training_days = _positive_integer("cohort_training_days", cohort_training_days)
    min_active_days = _positive_integer("min_active_days", min_active_days)
    max_skus = _positive_integer("max_skus", max_skus)
    recency_days = _positive_integer("recency_days", recency_days)

    transactions = normalize_transactions(transactions)

    transactions["date"] = pd.to_datetime(transactions["invoice_date"]).dt.normalize()
    start = transactions["date"].min()
    end = transactions["date"].max()
    if pd.isna(start) or pd.isna(end) or start >= end:
        raise DataContractError("The source must contain at least two distinct calendar dates.")

    is_cancellation = transactions["invoice_no"].str.upper().str.startswith("C")
    is_non_positive_quantity = transactions["quantity"] <= 0
    is_non_positive_price = transactions["unit_price"] <= 0
    is_product_code = transactions["stock_code"].map(
        lambda value: bool(PRODUCT_CODE_PATTERN.fullmatch(value))
    )
    eligible = ~(
        is_cancellation | is_non_positive_quantity | is_non_positive_price | ~is_product_code
    )
    non_product_audit = (
        transactions.loc[~is_product_code, ["stock_code", "quantity"]]
        .assign(positive_units=lambda frame: frame["quantity"].clip(lower=0))
        .groupby("stock_code", as_index=False)
        .agg(
            net_quantity=("quantity", "sum"),
            positive_units=("positive_units", "sum"),
            rows=("stock_code", "size"),
        )
        .sort_values(
            ["positive_units", "rows", "stock_code"],
            ascending=[False, False, True],
            kind="stable",
        )
        .head(20)
    )

    quality: dict[str, Any] = {
        "contract_validation": {
            "empty_country_rows": 0,
            "empty_invoice_rows": 0,
            "empty_stock_code_rows": 0,
            "invalid_invoice_date_rows": 0,
            "invalid_quantity_rows": 0,
            "invalid_unit_price_rows": 0,
        },
        "customer_id_missing_rows": int(transactions["customer_id"].isna().sum()),
        "description_missing_rows": int(transactions["description"].isna().sum()),
        "source_rows": int(len(transactions)),
        "source_start": start.date().isoformat(),
        "source_end": end.date().isoformat(),
        "cancellation_rows": int(is_cancellation.sum()),
        "cancellation_quantity_sum": int(transactions.loc[is_cancellation, "quantity"].sum()),
        "non_positive_quantity_rows": int(is_non_positive_quantity.sum()),
        "return_units_abs": int(-transactions.loc[transactions["quantity"] < 0, "quantity"].sum()),
        "non_positive_price_rows": int(is_non_positive_price.sum()),
        "non_positive_price_positive_units": int(
            transactions.loc[is_non_positive_price, "quantity"].clip(lower=0).sum()
        ),
        "non_product_code_rows": int((~is_product_code).sum()),
        "non_product_code_positive_units": int(
            transactions.loc[~is_product_code, "quantity"].clip(lower=0).sum()
        ),
        "eligible_rows": int(eligible.sum()),
        "eligible_positive_units_all_skus": int(transactions.loc[eligible, "quantity"].sum()),
        "excluded_rows_union": int((~eligible).sum()),
        "exclusion_rule_overlap_count": int(
            is_cancellation.sum()
            + is_non_positive_quantity.sum()
            + is_non_positive_price.sum()
            + (~is_product_code).sum()
            - (~eligible).sum()
        ),
        "exact_duplicate_rows_beyond_first": int(
            transactions.duplicated(sorted(REQUIRED_COLUMNS), keep="first").sum()
        ),
        "non_product_code_audit_top_20": [
            {
                "net_quantity": int(row.net_quantity),
                "positive_units": int(row.positive_units),
                "rows": int(row.rows),
                "stock_code": str(row.stock_code),
            }
            for row in non_product_audit.itertuples(index=False)
        ],
        "target": target_contract(),
    }

    positive = transactions.loc[eligible, ["date", "stock_code", "quantity"]].copy()
    if positive.empty:
        raise DataContractError("No eligible positive-demand rows remain after validation.")

    cohort_cutoff = start + timedelta(days=int(cohort_training_days))
    cohort_source = positive.loc[positive["date"] < cohort_cutoff]
    cohort_stats = (
        cohort_source.groupby("stock_code", as_index=False)
        .agg(
            active_days=("date", "nunique"),
            training_units=("quantity", "sum"),
            last_active_date=("date", "max"),
        )
        .loc[
            lambda frame: (
                (frame["active_days"] >= min_active_days)
                & (frame["last_active_date"] >= cohort_cutoff - timedelta(days=recency_days))
            )
        ]
        .sort_values(
            ["training_units", "active_days", "stock_code"],
            ascending=[False, False, True],
            kind="stable",
        )
    )
    selected_cohort_stats = cohort_stats.head(max_skus).copy()
    cohort = tuple(selected_cohort_stats["stock_code"].astype(str))
    if not cohort:
        raise DataContractError(
            "No SKU satisfies the training-only cohort rules; adjust documented thresholds."
        )

    selected = positive.loc[positive["stock_code"].isin(cohort)]
    daily = (
        selected.groupby(["date", "stock_code"], as_index=False)["quantity"]
        .sum()
        .rename(columns={"stock_code": "sku", "quantity": "units"})
    )

    dates = pd.date_range(start=start, end=end, freq="D")
    index = pd.MultiIndex.from_product([dates, cohort], names=["date", "sku"])
    panel = (
        daily.set_index(["date", "sku"])
        .reindex(index, fill_value=0)
        .reset_index()
        .sort_values(["date", "sku"], kind="stable", ignore_index=True)
    )
    panel["units"] = panel["units"].astype("int64")
    observed_source_dates = pd.DatetimeIndex(transactions["date"].unique())
    panel["source_observed_day"] = panel["date"].isin(observed_source_dates)

    if panel.duplicated(["date", "sku"]).any() or panel["units"].isna().any():
        raise DataContractError("The prepared daily panel is not unique and complete.")
    if (panel["units"] < 0).any():
        raise DataContractError("The demand target must be non-negative.")

    quality.update(
        {
            "cohort_cutoff_exclusive": cohort_cutoff.date().isoformat(),
            "cohort_training_days": cohort_training_days,
            "cohort_min_active_days": min_active_days,
            "cohort_recency_days": recency_days,
            "cohort_size": len(cohort),
            "cohort_training_profile": [
                {
                    "active_days": int(row.active_days),
                    "last_active_date": pd.Timestamp(row.last_active_date).date().isoformat(),
                    "sku": str(row.stock_code),
                    "training_units": int(row.training_units),
                }
                for row in selected_cohort_stats.itertuples(index=False)
            ],
            "eligible_observed_days": int(positive["date"].nunique()),
            "panel_calendar_days": int(len(dates)),
            "panel_rows": int(len(panel)),
            "panel_target_units": int(panel["units"].sum()),
            "panel_zero_rows": int(panel["units"].eq(0).sum()),
            "source_observed_days": int(transactions["date"].nunique()),
            "calendar_days_without_any_source_transaction": int(
                (~pd.Series(dates.isin(observed_source_dates))).sum()
            ),
        }
    )

    return PreparedDataset(
        panel=panel,
        cohort=cohort,
        cohort_cutoff=cohort_cutoff,
        quality=quality,
        selection={
            "max_skus": max_skus,
            "min_active_days": min_active_days,
            "recency_days": recency_days,
            "training_days": cohort_training_days,
        },
        source=dict(source_metadata or {}),
        target=target_contract(),
    )


def write_prepared_dataset(
    prepared: PreparedDataset,
    processed_dir: Path,
) -> tuple[Path, Path, Path]:
    processed_dir.mkdir(parents=True, exist_ok=True)
    panel_path = processed_dir / "daily_demand.csv"
    cohort_path = processed_dir / "cohort.json"
    quality_path = processed_dir / "data_quality.json"

    temporary_panel = panel_path.with_suffix(".csv.tmp")
    prepared.panel.to_csv(
        temporary_panel,
        index=False,
        date_format="%Y-%m-%d",
        lineterminator="\n",
    )
    os.replace(temporary_panel, panel_path)
    panel_sha256 = sha256_file(panel_path)

    _write_json(
        cohort_path,
        {
            "cutoff_exclusive": prepared.cohort_cutoff.date().isoformat(),
            "panel_sha256": panel_sha256,
            "selection": prepared.selection,
            "skus": list(prepared.cohort),
            "source": prepared.source,
            "target": prepared.target,
        },
    )
    quality = dict(prepared.quality)
    quality["panel_sha256"] = panel_sha256
    quality["source"] = prepared.source
    quality["target"] = prepared.target
    _write_json(quality_path, quality)
    return panel_path, cohort_path, quality_path


def validate_cohort_manifest(
    panel: pd.DataFrame,
    panel_path: Path,
    cohort_path: Path,
) -> dict[str, Any]:
    if not cohort_path.is_file():
        raise FileNotFoundError(f"Cohort manifest not found: {cohort_path}")
    manifest = json.loads(cohort_path.read_text(encoding="utf-8"))
    expected_hash = manifest.get("panel_sha256")
    if not isinstance(expected_hash, str) or sha256_file(panel_path) != expected_hash:
        raise DataContractError("Panel SHA-256 hash does not match the cohort manifest.")

    manifest_skus = manifest.get("skus")
    if (
        not isinstance(manifest_skus, list)
        or not manifest_skus
        or len(manifest_skus) != len(set(manifest_skus))
    ):
        raise DataContractError("Cohort manifest contains an invalid SKU list.")
    panel_skus = set(panel["sku"].astype(str).unique())
    if panel_skus != set(manifest_skus):
        raise DataContractError("Panel SKU set does not match the frozen cohort manifest.")
    if manifest.get("target") != target_contract():
        raise DataContractError("Cohort manifest target contract does not match the current code.")

    selection = manifest.get("selection")
    required_selection = {"max_skus", "min_active_days", "recency_days", "training_days"}
    if not isinstance(selection, dict) or set(selection) != required_selection:
        raise DataContractError("Cohort manifest contains invalid selection parameters.")
    try:
        training_days = _positive_integer("training_days", selection["training_days"])
        _positive_integer("max_skus", selection["max_skus"])
        _positive_integer("min_active_days", selection["min_active_days"])
        _positive_integer("recency_days", selection["recency_days"])
    except (TypeError, ValueError) as exc:
        raise DataContractError(
            "Cohort manifest selection parameters must be positive integers."
        ) from exc

    panel_dates = pd.to_datetime(panel["date"], errors="coerce", format="mixed")
    if panel_dates.isna().any():
        raise DataContractError("Panel contains invalid dates.")
    expected_cutoff = panel_dates.min().normalize() + timedelta(days=training_days)
    parsed_cutoff = pd.to_datetime(manifest.get("cutoff_exclusive"), errors="coerce")
    if pd.isna(parsed_cutoff) or pd.Timestamp(parsed_cutoff).normalize() != expected_cutoff:
        raise DataContractError("Cohort cutoff is inconsistent with the panel and training window.")
    return manifest


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def iso_date(value: pd.Timestamp | date) -> str:
    return pd.Timestamp(value).date().isoformat()
