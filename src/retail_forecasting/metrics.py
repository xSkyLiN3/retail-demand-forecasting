from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from retail_forecasting.backtesting import validate_panel
from retail_forecasting.config import FORECAST_HORIZON_DAYS, FORECAST_SEASONALITY_DAYS
from retail_forecasting.dataset import DataContractError


def _finite_arrays(actual: pd.Series, prediction: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    y_true = actual.to_numpy(dtype="float64")
    y_pred = prediction.to_numpy(dtype="float64")
    if y_true.size == 0 or y_true.shape != y_pred.shape:
        raise ValueError("Actual and prediction arrays must be non-empty and equally sized.")
    if not np.isfinite(y_true).all() or not np.isfinite(y_pred).all():
        raise ValueError("Metrics require finite values.")
    return y_true, y_pred


def metric_values(actual: pd.Series, prediction: pd.Series) -> dict[str, float | int | None]:
    y_true, y_pred = _finite_arrays(actual, prediction)
    error = y_pred - y_true
    absolute_error = np.abs(error)
    denominator = float(np.abs(y_true).sum())
    return {
        "mae": float(absolute_error.mean()),
        "wape": float(absolute_error.sum() / denominator) if denominator > 0 else None,
        "normalized_bias": float(error.sum() / denominator) if denominator > 0 else None,
        "actual_units": float(y_true.sum()),
        "predicted_units": float(y_pred.sum()),
        "rows": int(y_true.size),
    }


def seasonal_scales(
    training: pd.DataFrame,
    *,
    seasonality_days: int = FORECAST_SEASONALITY_DAYS,
) -> dict[str, float | None]:
    scales: dict[str, float | None] = {}
    for sku, group in training.groupby("sku", sort=True):
        values = group.sort_values("date")["units"].to_numpy(dtype="float64")
        if len(values) <= seasonality_days:
            scales[str(sku)] = None
            continue
        scale = float(np.abs(values[seasonality_days:] - values[:-seasonality_days]).mean())
        scales[str(sku)] = scale if scale > 0 and math.isfinite(scale) else None
    return scales


def _summary(frame: pd.DataFrame) -> dict[str, float | int | None]:
    values = metric_values(frame["actual"], frame["prediction"])
    scaled = frame["scaled_absolute_error"].dropna()
    values["mase"] = float(scaled.mean()) if not scaled.empty else None
    values["mase_evaluable_rows"] = int(len(scaled))
    return values


def _validated_predictions(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    horizon_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {
        "fold",
        "cutoff",
        "date",
        "sku",
        "horizon",
        "actual",
        "prediction",
        "model",
    }
    missing = required.difference(predictions.columns)
    if missing:
        raise DataContractError(f"Prediction columns missing: {sorted(missing)}")
    if predictions.empty:
        raise DataContractError("Predictions are empty.")

    result = predictions.loc[:, sorted(required)].copy()
    if result[["sku", "model"]].isna().any().any():
        raise DataContractError("Predictions contain missing identifiers.")
    result["sku"] = result["sku"].astype(str).str.strip()
    result["model"] = result["model"].astype(str).str.strip()
    if result["sku"].eq("").any() or result["model"].eq("").any():
        raise DataContractError("Predictions contain empty identifiers.")
    if result["model"].nunique() != 1:
        raise DataContractError("A report must contain exactly one model.")

    result["cutoff"] = pd.to_datetime(
        result["cutoff"], errors="coerce", format="mixed"
    ).dt.normalize()
    result["date"] = pd.to_datetime(result["date"], errors="coerce", format="mixed").dt.normalize()
    for column in ("fold", "horizon", "actual", "prediction"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result.isna().any().any():
        raise DataContractError("Predictions contain missing or invalid values.")
    if not np.isfinite(result[["fold", "horizon", "actual", "prediction"]]).all().all():
        raise DataContractError("Predictions contain non-finite values.")
    if (result["fold"] % 1 != 0).any() or (result["horizon"] % 1 != 0).any():
        raise DataContractError("Fold and horizon identifiers must be integers.")
    result["fold"] = result["fold"].astype("int64")
    result["horizon"] = result["horizon"].astype("int64")
    fold_ids = sorted(result["fold"].unique().tolist())
    expected_fold_ids = list(range(fold_ids[0], fold_ids[0] + len(fold_ids)))
    if fold_ids != expected_fold_ids:
        raise DataContractError("Fold identifiers must be contiguous.")
    if (result["prediction"] < 0).any() or (result["actual"] < 0).any():
        raise DataContractError("Actuals and predictions must be non-negative.")
    if result.duplicated(["fold", "date", "sku"]).any():
        raise DataContractError("Predictions contain duplicate fold/date/SKU rows.")

    implied_horizon = (result["date"] - result["cutoff"]).dt.days
    if (implied_horizon <= 0).any() or not implied_horizon.equals(result["horizon"]):
        raise DataContractError("Prediction dates or horizons are not strictly after the cutoff.")

    source = validate_panel(panel)
    outcomes = source.rename(columns={"units": "panel_actual"})
    result = result.merge(outcomes, on=["date", "sku"], how="left", validate="many_to_one")
    if result["panel_actual"].isna().any():
        raise DataContractError("A prediction has no matching outcome in the source panel.")
    if not np.allclose(
        result["actual"].to_numpy(dtype="float64"),
        result["panel_actual"].to_numpy(dtype="float64"),
        rtol=0.0,
        atol=0.0,
    ):
        raise DataContractError("Prediction actuals do not match the source panel.")
    result["actual"] = result.pop("panel_actual")

    expected_skus = frozenset(source["sku"].unique())
    expected_horizons = set(range(1, horizon_days + 1))
    previous_cutoff: pd.Timestamp | None = None
    previous_test_end: pd.Timestamp | None = None
    for fold, fold_rows in result.groupby("fold", sort=True):
        cutoffs = fold_rows["cutoff"].unique()
        if len(cutoffs) != 1:
            raise DataContractError(f"Fold {fold} contains more than one cutoff.")
        cutoff = pd.Timestamp(cutoffs[0])
        if set(fold_rows["horizon"]) != expected_horizons:
            raise DataContractError(f"Fold {fold} does not contain the required horizons.")
        expected_dates = pd.date_range(cutoff + timedelta(days=1), periods=horizon_days, freq="D")
        if previous_cutoff is not None and (
            cutoff <= previous_cutoff or expected_dates.min() <= previous_test_end
        ):
            raise DataContractError("Prediction folds must be chronological and non-overlapping.")
        expected_grid = pd.MultiIndex.from_product([expected_dates, expected_skus])
        actual_grid = pd.MultiIndex.from_frame(fold_rows[["date", "sku"]])
        if len(actual_grid) != len(expected_grid) or set(actual_grid) != set(expected_grid):
            raise DataContractError(f"Fold {fold} does not contain a complete forecast grid.")
        previous_cutoff = cutoff
        previous_test_end = pd.Timestamp(expected_dates.max())

    return result.sort_values(["fold", "date", "sku"], kind="stable"), source


def summarize_predictions(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    horizon_days: int = FORECAST_HORIZON_DAYS,
) -> dict[str, object]:
    if horizon_days < 1:
        raise ValueError("Horizon must be a positive integer.")

    validated, source = _validated_predictions(
        predictions,
        panel,
        horizon_days=horizon_days,
    )
    validated["scaled_absolute_error"] = np.nan
    by_fold: list[dict[str, object]] = []

    for fold, fold_rows in validated.groupby("fold", sort=True):
        cutoff = pd.Timestamp(fold_rows["cutoff"].iloc[0])
        training = source.loc[source["date"] <= cutoff]
        scales = seasonal_scales(training)
        row_index = fold_rows.index
        denominators = fold_rows["sku"].map(scales).astype("float64")
        evaluable = denominators.notna() & denominators.gt(0)
        absolute_error = (fold_rows["prediction"] - fold_rows["actual"]).abs()
        validated.loc[row_index[evaluable], "scaled_absolute_error"] = (
            absolute_error.loc[evaluable] / denominators.loc[evaluable]
        )

        fold_summary = _summary(validated.loc[row_index])
        fold_summary.update({"fold": int(fold), "cutoff": cutoff.date().isoformat()})
        by_fold.append(fold_summary)

    by_sku: list[dict[str, object]] = []
    for sku, sku_rows in validated.groupby("sku", sort=True):
        values = _summary(sku_rows)
        values["sku"] = str(sku)
        by_sku.append(values)

    by_horizon: list[dict[str, object]] = []
    for horizon, horizon_rows in validated.groupby("horizon", sort=True):
        values = _summary(horizon_rows)
        values["horizon"] = int(horizon)
        by_horizon.append(values)

    overall = _summary(validated)
    overall["folds"] = len(by_fold)
    overall["model"] = str(validated["model"].iloc[0])
    return {
        "overall": overall,
        "by_fold": by_fold,
        "by_sku": by_sku,
        "by_horizon": by_horizon,
    }
