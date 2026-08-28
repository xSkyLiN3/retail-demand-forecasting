from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from retail_forecasting.dataset import DataContractError

FORECAST_KEYS = ("fold", "cutoff", "date", "sku", "horizon")


@dataclass(frozen=True)
class AlertThresholds:
    minimum_coverage: float | None = 0.85
    maximum_coverage: float | None = 0.98
    maximum_absolute_bias: float | None = 0.10
    maximum_wape: float | None = None
    maximum_mean_width: float | None = None

    def __post_init__(self) -> None:
        for name in ("minimum_coverage", "maximum_coverage"):
            value = getattr(self, name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one.")
        if (
            self.minimum_coverage is not None
            and self.maximum_coverage is not None
            and self.minimum_coverage > self.maximum_coverage
        ):
            raise ValueError("minimum_coverage cannot exceed maximum_coverage.")
        for name in ("maximum_absolute_bias", "maximum_wape", "maximum_mean_width"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative.")


def _validated_forecasts(forecasts: pd.DataFrame) -> pd.DataFrame:
    required = {*FORECAST_KEYS, "prediction", "lower", "upper"}
    missing = required.difference(forecasts.columns)
    if missing:
        raise DataContractError(f"Forecast columns missing: {sorted(missing)}")
    if forecasts.empty:
        raise DataContractError("Forecasts are empty.")
    result = forecasts.loc[:, list(FORECAST_KEYS) + ["prediction", "lower", "upper"]].copy()
    result["cutoff"] = pd.to_datetime(
        result["cutoff"], errors="coerce", format="mixed"
    ).dt.normalize()
    result["date"] = pd.to_datetime(result["date"], errors="coerce", format="mixed").dt.normalize()
    result["sku"] = result["sku"].astype("string").str.strip()
    numeric = ["fold", "horizon", "prediction", "lower", "upper"]
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result.isna().any().any() or not np.isfinite(result[numeric]).all().all():
        raise DataContractError("Forecasts contain missing, invalid or non-finite values.")
    if (result["fold"] % 1 != 0).any() or (result["horizon"] % 1 != 0).any():
        raise DataContractError("Fold and horizon identifiers must be integers.")
    result["fold"] = result["fold"].astype("int64")
    result["horizon"] = result["horizon"].astype("int64")
    if result.duplicated(list(FORECAST_KEYS)).any():
        raise DataContractError("Forecasts contain duplicate forecast keys.")
    implied = (result["date"] - result["cutoff"]).dt.days
    if (result["horizon"] < 1).any() or not implied.equals(result["horizon"]):
        raise DataContractError("Forecast horizon does not match date minus cutoff.")
    if (
        (result[["prediction", "lower", "upper"]] < 0).any().any()
        or not result["lower"].le(result["prediction"]).all()
        or not result["prediction"].le(result["upper"]).all()
    ):
        raise DataContractError("Forecast interval bounds are inconsistent.")
    return result


def _validated_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    actual_column = "actual" if "actual" in outcomes.columns else "units"
    required = {"date", "sku", actual_column}
    missing = required.difference(outcomes.columns)
    if missing:
        raise DataContractError(f"Outcome columns missing: {sorted(missing)}")
    result = outcomes.loc[:, ["date", "sku", actual_column]].rename(
        columns={actual_column: "actual"}
    )
    result["date"] = pd.to_datetime(result["date"], errors="coerce", format="mixed").dt.normalize()
    result["sku"] = result["sku"].astype("string").str.strip()
    result["actual"] = pd.to_numeric(result["actual"], errors="coerce")
    if result.isna().any().any() or not np.isfinite(result["actual"]).all():
        raise DataContractError("Outcomes contain missing, invalid or non-finite values.")
    if (result["actual"] < 0).any() or result.duplicated(["date", "sku"]).any():
        raise DataContractError("Outcomes must be non-negative and unique by date/SKU.")
    return result


def _summary(rows: pd.DataFrame, *, alpha: float) -> dict[str, float | int | None]:
    actual = rows["actual"].to_numpy(dtype="float64")
    prediction = rows["prediction"].to_numpy(dtype="float64")
    lower = rows["lower"].to_numpy(dtype="float64")
    upper = rows["upper"].to_numpy(dtype="float64")
    width = upper - lower
    covered = (actual >= lower) & (actual <= upper)
    score = width.copy()
    below = actual < lower
    above = actual > upper
    score[below] += (2.0 / alpha) * (lower[below] - actual[below])
    score[above] += (2.0 / alpha) * (actual[above] - upper[above])
    absolute_error = np.abs(prediction - actual)
    error = prediction - actual
    denominator = float(actual.sum())
    return {
        "rows": int(len(rows)),
        "actual_units": denominator,
        "predicted_units": float(prediction.sum()),
        "coverage": float(covered.mean()),
        "mean_width": float(width.mean()),
        "median_width": float(np.median(width)),
        "winkler": float(score.mean()),
        "mae": float(absolute_error.mean()),
        "wape": float(absolute_error.sum() / denominator) if denominator > 0 else None,
        "normalized_bias": float(error.sum() / denominator) if denominator > 0 else None,
    }


def _alerts_for_summary(
    summary: dict[str, float | int | None],
    *,
    scope: str,
    thresholds: AlertThresholds,
) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []

    def add(metric: str, direction: str, threshold: float, observed: float) -> None:
        alerts.append(
            {
                "scope": scope,
                "metric": metric,
                "direction": direction,
                "threshold": threshold,
                "observed": observed,
            }
        )

    coverage = float(summary["coverage"])
    if thresholds.minimum_coverage is not None and coverage < thresholds.minimum_coverage:
        add("coverage", "below", thresholds.minimum_coverage, coverage)
    if thresholds.maximum_coverage is not None and coverage > thresholds.maximum_coverage:
        add("coverage", "above", thresholds.maximum_coverage, coverage)
    bias = summary["normalized_bias"]
    if (
        thresholds.maximum_absolute_bias is not None
        and bias is not None
        and abs(float(bias)) > thresholds.maximum_absolute_bias
    ):
        add("absolute_normalized_bias", "above", thresholds.maximum_absolute_bias, abs(float(bias)))
    wape = summary["wape"]
    if thresholds.maximum_wape is not None and wape is not None and wape > thresholds.maximum_wape:
        add("wape", "above", thresholds.maximum_wape, float(wape))
    width = float(summary["mean_width"])
    if thresholds.maximum_mean_width is not None and width > thresholds.maximum_mean_width:
        add("mean_width", "above", thresholds.maximum_mean_width, width)
    return alerts


def monitor_forecasts(
    forecasts: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    nominal_coverage: float = 0.90,
    thresholds: AlertThresholds | None = None,
) -> dict[str, object]:
    if not 0.0 < nominal_coverage < 1.0:
        raise ValueError("nominal_coverage must be strictly between zero and one.")
    thresholds = thresholds or AlertThresholds()
    validated_forecasts = _validated_forecasts(forecasts)
    validated_outcomes = _validated_outcomes(outcomes)
    joined = validated_forecasts.merge(
        validated_outcomes,
        on=["date", "sku"],
        how="left",
        validate="many_to_one",
    )
    if joined["actual"].isna().any():
        raise DataContractError("Every forecast must match exactly one observed outcome.")
    alpha = 1.0 - nominal_coverage
    overall = _summary(joined, alpha=alpha)
    by_horizon: list[dict[str, float | int | None]] = []
    for horizon, rows in joined.groupby("horizon", sort=True):
        values = _summary(rows, alpha=alpha)
        values["horizon"] = int(horizon)
        by_horizon.append(values)
    by_sku: list[dict[str, float | int | str | None]] = []
    for sku, rows in joined.groupby("sku", sort=True):
        values = _summary(rows, alpha=alpha)
        values["sku"] = str(sku)
        by_sku.append(values)

    alerts = _alerts_for_summary(overall, scope="overall", thresholds=thresholds)
    for values in by_horizon:
        alerts.extend(
            _alerts_for_summary(
                values,
                scope=f"horizon:{values['horizon']}",
                thresholds=thresholds,
            )
        )
    for values in by_sku:
        alerts.extend(
            _alerts_for_summary(
                values,
                scope=f"sku:{values['sku']}",
                thresholds=thresholds,
            )
        )
    return {
        "nominal_coverage": nominal_coverage,
        "overall": overall,
        "by_horizon": by_horizon,
        "by_sku": by_sku,
        "alerts": alerts,
    }
