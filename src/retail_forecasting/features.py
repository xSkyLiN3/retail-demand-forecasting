from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from retail_forecasting.backtesting import validate_panel
from retail_forecasting.config import FORECAST_HORIZON_DAYS, M1_HISTORY_DAYS
from retail_forecasting.dataset import DataContractError

REFERENCE_LAGS = (14, 21, 28, 35)
ORIGIN_LAGS = (1, 7, 14, 28)
ROLLING_WINDOWS = (7, 14, 28, 56)

CATEGORICAL_FEATURES = (
    "sku_index",
    "horizon",
    "target_day_of_week",
    "target_month",
)

FEATURE_COLUMNS = (
    *CATEGORICAL_FEATURES,
    "target_is_weekend",
    "target_day_of_year_sin",
    "target_day_of_year_cos",
    "baseline_prediction",
    *(f"target_lag_{lag}" for lag in REFERENCE_LAGS),
    "last_units",
    *(f"origin_lag_{lag}" for lag in ORIGIN_LAGS),
    *(f"rolling_mean_{window}" for window in ROLLING_WINDOWS),
    "rolling_std_28",
    "rolling_max_28",
    "active_rate_28",
    "active_rate_56",
    "days_since_nonzero",
    "trend_mean_7_28",
    "trend_ratio_7_28",
)


def feature_contract() -> dict[str, Any]:
    return {
        "categorical_features": list(CATEGORICAL_FEATURES),
        "feature_columns": list(FEATURE_COLUMNS),
        "history_days": M1_HISTORY_DAYS,
        "origin_policy": "daily historical origins",
        "reference_lags_days": list(REFERENCE_LAGS),
        "row_contract": "one row per origin, SKU and horizon; target_date = origin + horizon",
        "schema_version": 1,
        "target_calendar": "known from origin and horizon",
        "training_label_rule": "target_date <= fold cutoff",
    }


def _positive_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer.")
    result = int(value)
    if result < 1:
        raise ValueError(f"{name} must be at least 1.")
    return result


def _origin_features(panel: pd.DataFrame, history_days: int) -> pd.DataFrame:
    result = panel.copy()
    sku_order = {sku: index for index, sku in enumerate(sorted(result["sku"].unique()))}
    result["sku_index"] = result["sku"].map(sku_order).astype("int64")
    result["last_units"] = result["units"].astype("float64")
    grouped = result.groupby("sku", sort=False)["units"]

    for lag in ORIGIN_LAGS:
        result[f"origin_lag_{lag}"] = grouped.shift(lag).astype("float64")
    for window in ROLLING_WINDOWS:
        result[f"rolling_mean_{window}"] = grouped.transform(
            lambda values, size=window: values.rolling(size, min_periods=size).mean()
        )

    result["rolling_std_28"] = grouped.transform(
        lambda values: values.rolling(28, min_periods=28).std(ddof=0)
    )
    result["rolling_max_28"] = grouped.transform(
        lambda values: values.rolling(28, min_periods=28).max()
    )
    active = result["units"].gt(0).astype("float64")
    result["active_rate_28"] = active.groupby(result["sku"], sort=False).transform(
        lambda values: values.rolling(28, min_periods=28).mean()
    )
    result["active_rate_56"] = active.groupby(result["sku"], sort=False).transform(
        lambda values: values.rolling(56, min_periods=56).mean()
    )

    positive_date = result["date"].where(result["units"].gt(0))
    last_positive_date = positive_date.groupby(result["sku"], sort=False).ffill()
    result["days_since_nonzero"] = (
        (result["date"] - last_positive_date)
        .dt.days.fillna(history_days)
        .clip(lower=0, upper=history_days)
        .astype("float64")
    )
    result["trend_mean_7_28"] = result["rolling_mean_7"] - result["rolling_mean_28"]
    result["trend_ratio_7_28"] = result["rolling_mean_7"] / (result["rolling_mean_28"] + 1.0)

    history_start = result["date"].min() + timedelta(days=history_days - 1)
    return result.loc[result["date"] >= history_start].rename(columns={"date": "origin_date"})


def _attach_reference(
    frame: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    column: str,
    reference_dates: pd.Series,
) -> pd.DataFrame:
    working = frame.copy()
    working["_reference_date"] = pd.to_datetime(reference_dates).dt.normalize()
    lookup = panel.rename(columns={"date": "_reference_date", "units": column})
    result = working.merge(
        lookup[["_reference_date", "sku", column]],
        on=["_reference_date", "sku"],
        how="left",
        validate="many_to_one",
    ).drop(columns="_reference_date")
    if result[column].isna().any():
        raise DataContractError(f"Feature {column} has no causal reference observation.")
    result[column] = result[column].astype("float64")
    return result


def build_supervised_table(
    panel: pd.DataFrame,
    *,
    max_target_date: pd.Timestamp,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    history_days: int = M1_HISTORY_DAYS,
) -> pd.DataFrame:
    horizon_days = _positive_integer("horizon_days", horizon_days)
    history_days = _positive_integer("history_days", history_days)
    if history_days < max(*ROLLING_WINDOWS, *ORIGIN_LAGS, *REFERENCE_LAGS):
        raise ValueError("history_days is shorter than the fixed feature lookback.")

    validated = validate_panel(panel)
    target_end = pd.to_datetime(max_target_date, errors="coerce")
    if pd.isna(target_end):
        raise ValueError("max_target_date must be a valid date inside the source panel.")
    target_end = pd.Timestamp(target_end).normalize()
    if target_end > validated["date"].max() or target_end <= validated["date"].min():
        raise ValueError("max_target_date must fall inside the source panel.")
    development = validated.loc[validated["date"] <= target_end].copy()
    origins = _origin_features(development, history_days)
    origins = origins.loc[origins["origin_date"] < target_end]
    if origins.empty:
        raise DataContractError(
            "The capped panel has no supervised origin after the required feature history."
        )
    horizons = pd.DataFrame({"horizon": range(1, horizon_days + 1)})
    result = origins.merge(horizons, how="cross")
    result["target_date"] = result["origin_date"] + pd.to_timedelta(result["horizon"], unit="D")
    result = result.loc[result["target_date"] <= target_end].copy()
    if result.empty:
        raise DataContractError("The capped panel has no labeled supervised training rows.")

    target_lookup = development.rename(columns={"date": "target_date", "units": "actual"})
    result = result.merge(
        target_lookup[["target_date", "sku", "actual"]],
        on=["target_date", "sku"],
        how="left",
        validate="many_to_one",
    )

    baseline_lag = np.where(result["horizon"].le(7), 7, 14)
    baseline_dates = result["target_date"] - pd.to_timedelta(baseline_lag, unit="D")
    result = _attach_reference(
        result,
        development,
        column="baseline_prediction",
        reference_dates=baseline_dates,
    )
    for lag in REFERENCE_LAGS:
        result = _attach_reference(
            result,
            development,
            column=f"target_lag_{lag}",
            reference_dates=result["target_date"] - timedelta(days=lag),
        )

    result["target_day_of_week"] = result["target_date"].dt.dayofweek.astype("int64")
    result["target_month"] = result["target_date"].dt.month.astype("int64")
    result["target_is_weekend"] = result["target_day_of_week"].ge(5).astype("int64")
    day_of_year = result["target_date"].dt.dayofyear.astype("float64")
    result["target_day_of_year_sin"] = np.sin(2.0 * np.pi * day_of_year / 365.25)
    result["target_day_of_year_cos"] = np.cos(2.0 * np.pi * day_of_year / 365.25)

    implied_horizon = (result["target_date"] - result["origin_date"]).dt.days
    if not implied_horizon.equals(result["horizon"]):
        raise DataContractError("Supervised rows do not align origin, horizon and target date.")
    if result[[*FEATURE_COLUMNS, "actual"]].isna().any().any():
        raise DataContractError("Supervised features or labels contain missing values.")
    if not np.isfinite(result[[*FEATURE_COLUMNS, "actual"]]).all().all():
        raise DataContractError("Supervised features or labels contain non-finite values.")
    if (result["actual"] < 0).any():
        raise DataContractError("Supervised labels must be non-negative.")

    columns = ["origin_date", "target_date", "sku", "actual", *FEATURE_COLUMNS]
    return result.loc[:, columns].sort_values(
        ["origin_date", "target_date", "sku"],
        kind="stable",
        ignore_index=True,
    )
