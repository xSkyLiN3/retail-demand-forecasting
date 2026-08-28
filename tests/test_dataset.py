from __future__ import annotations

import json

import pandas as pd
import pytest

from retail_forecasting.dataset import (
    DataContractError,
    load_workbook,
    normalize_transactions,
    prepare_daily_panel,
    write_prepared_dataset,
)


def _row(
    day: str,
    *,
    invoice: str,
    stock_code: str,
    quantity: int,
    price: float = 1.0,
) -> dict[str, object]:
    return {
        "Invoice": invoice,
        "StockCode": stock_code,
        "Description": "Example",
        "Quantity": quantity,
        "InvoiceDate": day,
        "Price": price,
        "Customer ID": 12345,
        "Country": "United Kingdom",
    }


def test_normalize_transactions_accepts_historical_workbook_headers() -> None:
    frame = pd.DataFrame([_row("2020-01-01", invoice="100001", stock_code="10000", quantity=2)])

    normalized = normalize_transactions(frame)

    assert normalized.loc[0, "invoice_no"] == "100001"
    assert normalized.loc[0, "stock_code"] == "10000"
    assert normalized.loc[0, "quantity"] == 2
    assert normalized.loc[0, "unit_price"] == 1.0


def test_cohort_uses_training_window_and_target_excludes_non_demand_rows() -> None:
    rows: list[dict[str, object]] = []
    dates = pd.date_range("2020-01-01", periods=20, freq="D")
    for index, day in enumerate(dates):
        rows.append(
            _row(
                day.isoformat(),
                invoice=f"10{index:04d}",
                stock_code="10000",
                quantity=1,
            )
        )
        if index >= 10:
            rows.append(
                _row(
                    day.isoformat(),
                    invoice=f"20{index:04d}",
                    stock_code="10001",
                    quantity=100,
                )
            )

    rows.extend(
        [
            _row(
                "2020-01-05",
                invoice="C99999",
                stock_code="10000",
                quantity=50,
            ),
            _row(
                "2020-01-06",
                invoice="999998",
                stock_code="10000",
                quantity=-10,
            ),
            _row(
                "2020-01-07",
                invoice="999997",
                stock_code="POST",
                quantity=20,
            ),
        ]
    )
    transactions = normalize_transactions(pd.DataFrame(rows))

    prepared = prepare_daily_panel(
        transactions,
        cohort_training_days=10,
        min_active_days=3,
        max_skus=1,
    )

    assert prepared.cohort == ("10000",)
    assert prepared.panel["units"].sum() == 20
    assert prepared.quality["cancellation_rows"] == 1
    assert prepared.quality["non_positive_quantity_rows"] == 1
    assert prepared.quality["non_product_code_rows"] == 1
    assert "source_observed_day" in prepared.panel


def test_prepare_revalidates_already_canonical_columns() -> None:
    canonical = normalize_transactions(
        pd.DataFrame([_row("2020-01-01", invoice="100001", stock_code="10000", quantity=2)])
    )
    canonical["quantity"] = canonical["quantity"].astype("float64")
    canonical.loc[0, "quantity"] = 1.5

    with pytest.raises(DataContractError, match="quantity"):
        prepare_daily_panel(
            canonical,
            cohort_training_days=1,
            min_active_days=1,
            max_skus=1,
        )


def test_normalization_rejects_colliding_headers() -> None:
    frame = pd.DataFrame(
        [["100001", "100001"]],
        columns=["Invoice", "InvoiceNo"],
    )

    with pytest.raises(DataContractError, match="collide"):
        normalize_transactions(frame)


def test_two_letter_product_variant_is_eligible() -> None:
    rows = [
        _row(
            day.isoformat(),
            invoice=f"10{index:04d}",
            stock_code="15056BL",
            quantity=2,
        )
        for index, day in enumerate(pd.date_range("2020-01-01", periods=3, freq="D"))
    ]

    prepared = prepare_daily_panel(
        pd.DataFrame(rows),
        cohort_training_days=2,
        min_active_days=1,
        max_skus=1,
        recency_days=1,
    )

    assert prepared.cohort == ("15056BL",)
    assert prepared.panel["units"].sum() == 6


def test_workbook_loader_uses_multiset_union_across_sheets(tmp_path) -> None:
    repeated = _row(
        "2020-01-01",
        invoice="100001",
        stock_code="10000",
        quantity=2,
    )
    first = pd.DataFrame(
        [
            repeated,
            repeated,
            _row("2020-01-02", invoice="100002", stock_code="10001", quantity=1),
        ]
    )
    second = pd.DataFrame(
        [
            repeated,
            repeated,
            _row("2020-01-03", invoice="100003", stock_code="10002", quantity=1),
        ]
    )
    workbook = tmp_path / "source.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        first.to_excel(writer, sheet_name="first", index=False)
        second.to_excel(writer, sheet_name="second", index=False)

    loaded = load_workbook(workbook)

    assert len(loaded.transactions) == 4
    assert loaded.audit["cross_sheet_exact_rows_affected"] == 4
    assert loaded.audit["cross_sheet_exact_rows_removed"] == 2
    assert loaded.audit["cross_sheet_exact_value_groups"] == 1
    assert loaded.audit["within_sheet_exact_duplicates_beyond_first"] == 2


def test_workbook_audit_reports_unequal_cross_sheet_multiplicity(tmp_path) -> None:
    repeated = _row(
        "2020-01-01",
        invoice="100001",
        stock_code="10000",
        quantity=2,
    )
    first = pd.DataFrame([repeated, repeated])
    second = pd.DataFrame([repeated])
    workbook = tmp_path / "source.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        first.to_excel(writer, sheet_name="first", index=False)
        second.to_excel(writer, sheet_name="second", index=False)

    loaded = load_workbook(workbook)

    assert len(loaded.transactions) == 2
    assert loaded.audit["cross_sheet_exact_rows_removed"] == 1
    assert loaded.audit["cross_sheet_multiplicity_mismatch_groups"] == 1


def test_manifest_records_runtime_cohort_parameters(tmp_path) -> None:
    rows = [
        _row(
            day.isoformat(),
            invoice=f"10{index:04d}",
            stock_code="10000",
            quantity=1,
        )
        for index, day in enumerate(pd.date_range("2020-01-01", periods=20, freq="D"))
    ]
    prepared = prepare_daily_panel(
        pd.DataFrame(rows),
        cohort_training_days=10,
        min_active_days=3,
        max_skus=1,
        recency_days=4,
    )

    _, cohort_path, _ = write_prepared_dataset(prepared, tmp_path)
    manifest = json.loads(cohort_path.read_text(encoding="utf-8"))

    assert b"\r\n" not in cohort_path.read_bytes()
    assert manifest["selection"] == {
        "max_skus": 1,
        "min_active_days": 3,
        "recency_days": 4,
        "training_days": 10,
    }
    assert manifest["target"]["product_code_pattern"] == "^[0-9]{5}[A-Z]{0,2}$"


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("cohort_training_days", 3.8),
        ("min_active_days", 1.5),
        ("max_skus", True),
        ("recency_days", 1.5),
    ],
)
def test_cohort_parameters_require_integers(parameter, value) -> None:
    rows = [
        _row(
            day.isoformat(),
            invoice=f"10{index:04d}",
            stock_code="10000",
            quantity=1,
        )
        for index, day in enumerate(pd.date_range("2020-01-01", periods=5, freq="D"))
    ]
    parameters = {
        "cohort_training_days": 3,
        "min_active_days": 1,
        "max_skus": 1,
        "recency_days": 1,
    }
    parameters[parameter] = value

    with pytest.raises(TypeError, match="integer"):
        prepare_daily_panel(pd.DataFrame(rows), **parameters)
