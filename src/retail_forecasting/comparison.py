from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from retail_forecasting.dataset import DataContractError
from retail_forecasting.metrics import metric_values, summarize_predictions

PAIR_KEYS = ("fold", "cutoff", "date", "sku", "horizon")
CONFIRMATION_FOLDS = 6
CONFIRMATION_SKUS = 20
MINIMUM_RELATIVE_WAPE_IMPROVEMENT = 0.05
MINIMUM_FOLD_WINS = 4
MINIMUM_SKU_WINS = 11
MAXIMUM_ABSOLUTE_BIAS = 0.10
MAXIMUM_BIAS_DETERIORATION = 0.02


def _paired_frame(predictions: pd.DataFrame, *, role: str) -> pd.DataFrame:
    required = {*PAIR_KEYS, "actual", "prediction"}
    missing = required.difference(predictions.columns)
    if missing:
        raise DataContractError(f"{role} predictions are missing columns: {sorted(missing)}")

    result = predictions.loc[:, [*PAIR_KEYS, "actual", "prediction"]].copy()
    result["cutoff"] = pd.to_datetime(
        result["cutoff"], errors="coerce", format="mixed"
    ).dt.normalize()
    result["date"] = pd.to_datetime(result["date"], errors="coerce", format="mixed").dt.normalize()
    result["sku"] = result["sku"].astype("string").str.strip()
    for column in ("fold", "horizon", "actual", "prediction"):
        result[column] = pd.to_numeric(result[column], errors="coerce")

    if result.isna().any().any():
        raise DataContractError(f"{role} predictions contain missing or invalid pairing values.")
    if not np.isfinite(result[["fold", "horizon", "actual", "prediction"]]).all().all():
        raise DataContractError(f"{role} predictions contain non-finite values.")
    if (result["fold"] % 1 != 0).any() or (result["horizon"] % 1 != 0).any():
        raise DataContractError(f"{role} fold and horizon identifiers must be integers.")

    result["fold"] = result["fold"].astype("int64")
    result["horizon"] = result["horizon"].astype("int64")
    if result.duplicated(list(PAIR_KEYS)).any():
        raise DataContractError(f"{role} predictions contain duplicate comparison keys.")
    return result


def _paired_evidence(candidate: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    candidate_pair = _paired_frame(candidate, role="Candidate").rename(
        columns={"actual": "candidate_actual", "prediction": "candidate_prediction"}
    )
    baseline_pair = _paired_frame(baseline, role="Baseline").rename(
        columns={"actual": "baseline_actual", "prediction": "baseline_prediction"}
    )
    paired = candidate_pair.merge(
        baseline_pair,
        on=list(PAIR_KEYS),
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    if not paired["_merge"].eq("both").all():
        candidate_only = int(paired["_merge"].eq("left_only").sum())
        baseline_only = int(paired["_merge"].eq("right_only").sum())
        raise DataContractError(
            "Candidate and baseline comparison keys differ: "
            f"candidate_only={candidate_only}, baseline_only={baseline_only}."
        )
    if not np.array_equal(
        paired["candidate_actual"].to_numpy(dtype="float64"),
        paired["baseline_actual"].to_numpy(dtype="float64"),
    ):
        raise DataContractError("Candidate and baseline actual values do not match exactly.")

    return paired.drop(columns="_merge").sort_values(
        list(PAIR_KEYS), kind="stable", ignore_index=True
    )


def _relative_wape_improvement(candidate: object, baseline: object) -> float | None:
    if candidate is None or baseline is None:
        return None
    candidate_value = float(candidate)
    baseline_value = float(baseline)
    if not np.isfinite(candidate_value) or not np.isfinite(baseline_value) or baseline_value <= 0:
        return None
    return float((baseline_value - candidate_value) / baseline_value)


def _paired_views(
    candidate_summary: dict[str, object],
    baseline_summary: dict[str, object],
    *,
    view: str,
    identifier: str,
) -> list[dict[str, object]]:
    candidate_rows = {row[identifier]: row for row in candidate_summary[view]}
    baseline_rows = {row[identifier]: row for row in baseline_summary[view]}
    if candidate_rows.keys() != baseline_rows.keys():
        raise DataContractError(f"Candidate and baseline {view} identifiers do not match.")

    result: list[dict[str, object]] = []
    for value in sorted(candidate_rows):
        candidate_row = candidate_rows[value]
        baseline_row = baseline_rows[value]
        candidate_wape = candidate_row["wape"]
        baseline_wape = baseline_row["wape"]
        result.append(
            {
                identifier: value,
                "baseline": baseline_row,
                "candidate": candidate_row,
                "candidate_wins_wape": (
                    candidate_wape is not None
                    and baseline_wape is not None
                    and float(candidate_wape) < float(baseline_wape)
                ),
                "relative_wape_improvement": _relative_wape_improvement(
                    candidate_wape, baseline_wape
                ),
            }
        )
    return result


def _range_metrics(
    predictions: pd.DataFrame,
    summary: dict[str, object],
    *,
    first_horizon: int,
    last_horizon: int,
) -> dict[str, float | int | None]:
    horizon = pd.to_numeric(predictions["horizon"], errors="coerce")
    selected = predictions.loc[horizon.between(first_horizon, last_horizon, inclusive="both")]
    values = metric_values(selected["actual"], selected["prediction"])

    horizon_rows = {
        int(row["horizon"]): row
        for row in summary["by_horizon"]
        if first_horizon <= int(row["horizon"]) <= last_horizon
    }
    expected = set(range(first_horizon, last_horizon + 1))
    if set(horizon_rows) != expected:
        raise DataContractError("A horizon-range view is missing required horizons.")
    mase_rows = [row for row in horizon_rows.values() if row["mase"] is not None]
    evaluable_rows = sum(int(row["mase_evaluable_rows"]) for row in mase_rows)
    values["mase"] = (
        float(
            sum(float(row["mase"]) * int(row["mase_evaluable_rows"]) for row in mase_rows)
            / evaluable_rows
        )
        if evaluable_rows > 0
        else None
    )
    values["mase_evaluable_rows"] = evaluable_rows
    return values


def _criterion(value: object, *, threshold: object, passed: bool) -> dict[str, object]:
    return {"passed": bool(passed), "threshold": threshold, "value": value}


def compare_candidate_to_baseline(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    panel: pd.DataFrame,
) -> dict[str, Any]:
    """Compare paired six-fold confirmation evidence and apply the frozen M1 gate."""

    paired = _paired_evidence(candidate, baseline)
    candidate_summary = summarize_predictions(candidate, panel)
    baseline_summary = summarize_predictions(baseline, panel)
    candidate_overall = candidate_summary["overall"]
    baseline_overall = baseline_summary["overall"]

    if int(candidate_overall["folds"]) != CONFIRMATION_FOLDS:
        raise DataContractError(
            f"The confirmation gate requires exactly {CONFIRMATION_FOLDS} folds."
        )
    if len(candidate_summary["by_sku"]) != CONFIRMATION_SKUS:
        raise DataContractError(f"The confirmation gate requires exactly {CONFIRMATION_SKUS} SKUs.")

    by_fold = _paired_views(
        candidate_summary,
        baseline_summary,
        view="by_fold",
        identifier="fold",
    )
    by_sku = _paired_views(
        candidate_summary,
        baseline_summary,
        view="by_sku",
        identifier="sku",
    )
    by_horizon_range: list[dict[str, object]] = []
    for first_horizon, last_horizon in ((1, 7), (8, 14)):
        candidate_range = _range_metrics(
            candidate,
            candidate_summary,
            first_horizon=first_horizon,
            last_horizon=last_horizon,
        )
        baseline_range = _range_metrics(
            baseline,
            baseline_summary,
            first_horizon=first_horizon,
            last_horizon=last_horizon,
        )
        by_horizon_range.append(
            {
                "baseline": baseline_range,
                "candidate": candidate_range,
                "horizon_range": f"{first_horizon}-{last_horizon}",
                "relative_wape_improvement": _relative_wape_improvement(
                    candidate_range["wape"], baseline_range["wape"]
                ),
            }
        )

    relative_wape = _relative_wape_improvement(candidate_overall["wape"], baseline_overall["wape"])
    fold_wins = sum(bool(row["candidate_wins_wape"]) for row in by_fold)
    sku_wins = sum(bool(row["candidate_wins_wape"]) for row in by_sku)
    candidate_mase = candidate_overall["mase"]
    baseline_mase = baseline_overall["mase"]
    candidate_bias = candidate_overall["normalized_bias"]
    baseline_bias = baseline_overall["normalized_bias"]
    predictions_valid = bool(
        np.isfinite(paired[["candidate_prediction", "baseline_prediction"]]).all().all()
        and (paired[["candidate_prediction", "baseline_prediction"]] >= 0).all().all()
    )

    criteria = {
        "relative_wape_improvement": _criterion(
            relative_wape,
            threshold=MINIMUM_RELATIVE_WAPE_IMPROVEMENT,
            passed=(
                relative_wape is not None and relative_wape >= MINIMUM_RELATIVE_WAPE_IMPROVEMENT
            ),
        ),
        "fold_wins": _criterion(
            fold_wins,
            threshold=MINIMUM_FOLD_WINS,
            passed=fold_wins >= MINIMUM_FOLD_WINS,
        ),
        "sku_wins": _criterion(
            sku_wins,
            threshold=MINIMUM_SKU_WINS,
            passed=sku_wins >= MINIMUM_SKU_WINS,
        ),
        "mase_lower": _criterion(
            candidate_mase,
            threshold=baseline_mase,
            passed=(
                candidate_mase is not None
                and baseline_mase is not None
                and float(candidate_mase) < float(baseline_mase)
            ),
        ),
        "absolute_bias": _criterion(
            abs(float(candidate_bias)) if candidate_bias is not None else None,
            threshold=MAXIMUM_ABSOLUTE_BIAS,
            passed=(
                candidate_bias is not None and abs(float(candidate_bias)) <= MAXIMUM_ABSOLUTE_BIAS
            ),
        ),
        "bias_deterioration": _criterion(
            (
                abs(float(candidate_bias)) - abs(float(baseline_bias))
                if candidate_bias is not None and baseline_bias is not None
                else None
            ),
            threshold=MAXIMUM_BIAS_DETERIORATION,
            passed=(
                candidate_bias is not None
                and baseline_bias is not None
                and abs(float(candidate_bias))
                <= abs(float(baseline_bias)) + MAXIMUM_BIAS_DETERIORATION
            ),
        ),
        "predictions_finite_and_non_negative": _criterion(
            predictions_valid,
            threshold=True,
            passed=predictions_valid,
        ),
    }
    return {
        "baseline": baseline_summary,
        "by_fold": by_fold,
        "by_horizon_range": by_horizon_range,
        "by_sku": by_sku,
        "candidate": candidate_summary,
        "criteria": criteria,
        "paired_rows": int(len(paired)),
        "promoted": all(bool(criterion["passed"]) for criterion in criteria.values()),
    }
