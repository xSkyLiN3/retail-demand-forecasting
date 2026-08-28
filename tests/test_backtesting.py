from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from retail_forecasting.backtesting import (
    BacktestFold,
    make_folds,
    seasonal_naive_predictions,
    validate_panel,
)
from retail_forecasting.dataset import DataContractError


def _panel(days: int = 60, skus: tuple[str, ...] = ("A", "B")) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for day_index, day in enumerate(pd.date_range("2020-01-01", periods=days, freq="D")):
        for sku_index, sku in enumerate(skus):
            records.append({"date": day, "sku": sku, "units": day_index + sku_index})
    return pd.DataFrame(records)


def test_folds_are_strictly_forward_and_complete() -> None:
    folds = make_folds(
        _panel(),
        initial_train_days=30,
        horizon_days=7,
        step_days=7,
        min_folds=3,
        final_holdout_days=0,
    )

    assert len(folds) == 4
    assert all(fold.cutoff < fold.test_start <= fold.test_end for fold in folds)
    assert all((fold.test_end - fold.test_start).days == 6 for fold in folds)


def test_seasonal_naive_repeats_only_the_last_observed_week() -> None:
    panel = _panel(days=28, skus=("A",))
    fold = make_folds(
        panel,
        initial_train_days=14,
        horizon_days=7,
        step_days=7,
        min_folds=1,
        final_holdout_days=0,
    )[0]

    predictions = seasonal_naive_predictions(panel, (fold,), seasonality_days=7)

    assert predictions["prediction"].tolist() == [float(value) for value in range(7, 14)]
    assert predictions["actual"].tolist() == [float(value) for value in range(14, 21)]
    assert predictions["date"].min() > fold.cutoff


@pytest.mark.parametrize(
    "column,value",
    [
        ("sku", None),
        ("units", float("inf")),
        ("units", 1.5),
    ],
)
def test_panel_rejects_invalid_identifiers_and_targets(column, value) -> None:
    panel = _panel(days=10, skus=("A",))
    if column == "units":
        panel["units"] = panel["units"].astype("float64")
    panel.loc[0, column] = value

    with pytest.raises(DataContractError):
        validate_panel(panel)


def test_panel_rejects_a_rotating_sku_set() -> None:
    panel = _panel(days=10)
    panel.loc[(panel["date"] == panel["date"].max()) & (panel["sku"] == "B"), "sku"] = "C"

    with pytest.raises(DataContractError, match="every SKU"):
        validate_panel(panel)


def test_default_fold_generation_reserves_the_final_holdout() -> None:
    panel = _panel(days=600, skus=("A",))

    folds = make_folds(panel)

    holdout_start = panel["date"].max() - timedelta(days=83)
    assert folds[-1].test_end < holdout_start
    assert folds[-1].cutoff < folds[-1].test_start


def test_fold_parameters_reject_fractional_values_and_overlapping_tests() -> None:
    panel = _panel(days=60, skus=("A",))

    with pytest.raises(TypeError, match="integer"):
        make_folds(
            panel,
            initial_train_days=30.5,
            horizon_days=7,
            step_days=7,
            min_folds=1,
            final_holdout_days=0,
        )

    with pytest.raises(ValueError, match="overlapping"):
        make_folds(
            panel,
            initial_train_days=30,
            horizon_days=7,
            step_days=3,
            min_folds=1,
            final_holdout_days=0,
        )


def test_seasonal_naive_accepts_a_contiguous_offset_fold_partition() -> None:
    panel = _panel(days=60, skus=("A",))
    original = make_folds(
        panel,
        initial_train_days=30,
        horizon_days=7,
        step_days=7,
        min_folds=3,
        final_holdout_days=0,
    )
    validation = tuple(
        BacktestFold(
            index=14 + position,
            cutoff=fold.cutoff,
            test_start=fold.test_start,
            test_end=fold.test_end,
        )
        for position, fold in enumerate(original[-2:])
    )

    predictions = seasonal_naive_predictions(panel, validation, seasonality_days=7)

    assert predictions["fold"].unique().tolist() == [14, 15]
