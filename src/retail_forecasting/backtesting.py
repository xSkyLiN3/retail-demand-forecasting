from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from retail_forecasting.config import (
    BACKTEST_STEP_DAYS,
    COHORT_TRAINING_DAYS,
    FINAL_HOLDOUT_DAYS,
    FORECAST_HORIZON_DAYS,
    FORECAST_SEASONALITY_DAYS,
    MIN_BACKTEST_FOLDS,
)
from retail_forecasting.dataset import DataContractError


@dataclass(frozen=True)
class BacktestFold:
    index: int
    cutoff: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def _integer_parameter(name: str, value: int, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer.")
    result = int(value)
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return result


def validate_panel(panel: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "sku", "units"}
    missing = required.difference(panel.columns)
    if missing:
        raise DataContractError(f"Panel columns missing: {sorted(missing)}")

    result = panel.loc[:, ["date", "sku", "units"]].copy()
    if result.empty:
        raise DataContractError("Panel is empty.")
    if result["sku"].isna().any():
        raise DataContractError("Panel contains missing SKU identifiers.")

    result["date"] = pd.to_datetime(result["date"], errors="coerce", format="mixed").dt.normalize()
    result["sku"] = result["sku"].astype(str).str.strip()
    result["units"] = pd.to_numeric(result["units"], errors="coerce")

    if result.isna().any().any():
        raise DataContractError("Panel contains missing or invalid values.")
    if result["sku"].eq("").any():
        raise DataContractError("Panel contains empty SKU identifiers.")
    if not np.isfinite(result["units"]).all():
        raise DataContractError("Panel target contains non-finite values.")
    if (result["units"] % 1 != 0).any():
        raise DataContractError("Panel target must contain whole units.")
    if (result["units"] < 0).any():
        raise DataContractError("Panel target contains negative values.")
    if result.duplicated(["date", "sku"]).any():
        raise DataContractError("Panel contains duplicate date/SKU rows.")

    dates = pd.DatetimeIndex(sorted(result["date"].unique()))
    expected = pd.date_range(dates.min(), dates.max(), freq="D")
    if not dates.equals(expected):
        raise DataContractError("Panel calendar contains missing dates.")

    expected_skus = frozenset(result["sku"].unique())
    sku_sets = result.groupby("date")["sku"].agg(frozenset)
    if not sku_sets.map(lambda values: values == expected_skus).all():
        raise DataContractError("Panel does not contain every SKU on every date.")

    return result.sort_values(["date", "sku"], kind="stable", ignore_index=True)


def make_folds(
    panel: pd.DataFrame,
    *,
    initial_train_days: int = COHORT_TRAINING_DAYS,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    step_days: int = BACKTEST_STEP_DAYS,
    min_folds: int = MIN_BACKTEST_FOLDS,
    final_holdout_days: int = FINAL_HOLDOUT_DAYS,
) -> tuple[BacktestFold, ...]:
    initial_train_days = _integer_parameter("initial_train_days", initial_train_days, minimum=1)
    horizon_days = _integer_parameter("horizon_days", horizon_days, minimum=1)
    step_days = _integer_parameter("step_days", step_days, minimum=1)
    min_folds = _integer_parameter("min_folds", min_folds, minimum=1)
    final_holdout_days = _integer_parameter("final_holdout_days", final_holdout_days, minimum=0)
    if step_days < horizon_days:
        raise ValueError("step_days must be at least horizon_days to avoid overlapping tests.")

    validated = validate_panel(panel)
    first_date = pd.Timestamp(validated["date"].min())
    last_date = pd.Timestamp(validated["date"].max())
    development_end = last_date - timedelta(days=final_holdout_days)
    cutoff = first_date + timedelta(days=initial_train_days - 1)
    folds: list[BacktestFold] = []

    while cutoff + timedelta(days=horizon_days) <= development_end:
        test_start = cutoff + timedelta(days=1)
        test_end = cutoff + timedelta(days=horizon_days)
        folds.append(
            BacktestFold(
                index=len(folds),
                cutoff=cutoff,
                test_start=test_start,
                test_end=test_end,
            )
        )
        cutoff += timedelta(days=step_days)

    if len(folds) < min_folds:
        raise DataContractError(
            f"Only {len(folds)} complete folds are available; at least {min_folds} are required."
        )
    return tuple(folds)


def seasonal_naive_predictions(
    panel: pd.DataFrame,
    folds: tuple[BacktestFold, ...],
    *,
    seasonality_days: int = FORECAST_SEASONALITY_DAYS,
) -> pd.DataFrame:
    seasonality_days = _integer_parameter("seasonality_days", seasonality_days, minimum=1)
    if not folds:
        raise DataContractError("At least one backtesting fold is required.")
    fold_indices = [fold.index for fold in folds]
    if any(
        isinstance(index, bool) or not isinstance(index, (int, np.integer))
        for index in fold_indices
    ):
        raise DataContractError("Fold identifiers must be integers.")
    expected_indices = list(range(int(fold_indices[0]), int(fold_indices[0]) + len(folds)))
    if fold_indices != expected_indices:
        raise DataContractError("Fold identifiers must be ordered, unique and contiguous.")

    validated = validate_panel(panel)
    records: list[dict[str, object]] = []
    previous_fold: BacktestFold | None = None

    for fold in folds:
        if fold.test_start != fold.cutoff + timedelta(days=1) or fold.test_end < fold.test_start:
            raise DataContractError(f"Fold {fold.index} has an invalid temporal boundary.")
        if previous_fold is not None and (
            fold.cutoff <= previous_fold.cutoff or fold.test_start <= previous_fold.test_end
        ):
            raise DataContractError("Backtesting folds must be chronological and non-overlapping.")
        train = validated.loc[validated["date"] <= fold.cutoff]
        test = validated.loc[
            validated["date"].between(fold.test_start, fold.test_end, inclusive="both")
        ]
        expected_days = (fold.test_end - fold.test_start).days + 1
        expected_rows = expected_days * validated["sku"].nunique()
        if len(test) != expected_rows:
            raise DataContractError(f"Fold {fold.index} does not contain a complete test grid.")

        for sku, actual_rows in test.groupby("sku", sort=True):
            history = train.loc[train["sku"] == sku].sort_values("date")
            if len(history) < seasonality_days:
                raise DataContractError(
                    f"SKU {sku} has fewer than {seasonality_days} training observations."
                )
            pattern = history.tail(seasonality_days)["units"].to_numpy(dtype="float64")
            actual_rows = actual_rows.sort_values("date")

            for horizon, row in enumerate(actual_rows.itertuples(index=False), start=1):
                prediction = float(pattern[(horizon - 1) % seasonality_days])
                records.append(
                    {
                        "fold": fold.index,
                        "cutoff": fold.cutoff,
                        "date": row.date,
                        "sku": str(sku),
                        "horizon": horizon,
                        "actual": float(row.units),
                        "prediction": max(0.0, prediction),
                        "model": f"seasonal_naive_{seasonality_days}d",
                    }
                )

        previous_fold = fold

    result = pd.DataFrame.from_records(records)
    if result.empty or not np.isfinite(result[["actual", "prediction"]]).all().all():
        raise DataContractError("Baseline predictions are empty or non-finite.")
    return result.sort_values(["fold", "date", "sku"], kind="stable", ignore_index=True)
