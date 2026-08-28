from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from retail_forecasting.backtesting import BacktestFold
from retail_forecasting.dataset import DataContractError
from retail_forecasting.features import FEATURE_COLUMNS
from retail_forecasting.modeling import (
    MODEL_CONFIG_GRID,
    ModelConfig,
    fit_predict_folds,
    model_grid_contract,
    partition_development_folds,
)


def _fold(index: int, cutoff: pd.Timestamp) -> BacktestFold:
    return BacktestFold(
        index=index,
        cutoff=cutoff,
        test_start=cutoff + timedelta(days=1),
        test_end=cutoff + timedelta(days=14),
    )


def _table(cutoff: pd.Timestamp, *, skus: tuple[str, ...] = ("A", "B")) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for origin_offset in range(-70, 1):
        origin = cutoff + timedelta(days=origin_offset)
        for sku_index, sku in enumerate(skus):
            for horizon in range(1, 15):
                target = origin + timedelta(days=horizon)
                base = float(5 + sku_index + (origin.dayofyear % 7))
                row: dict[str, object] = {
                    "origin_date": origin,
                    "target_date": target,
                    "sku": sku,
                    "actual": float((int(base) + horizon) % 11),
                }
                for feature_index, column in enumerate(FEATURE_COLUMNS):
                    row[column] = base + feature_index / 100.0
                row["sku_index"] = sku_index
                row["horizon"] = horizon
                row["target_day_of_week"] = target.dayofweek
                row["target_month"] = target.month
                row["target_is_weekend"] = int(target.dayofweek >= 5)
                records.append(row)
    return pd.DataFrame.from_records(records)


def test_model_grid_is_exact_and_config_is_immutable() -> None:
    assert [
        (config.name, config.loss, config.max_leaf_nodes, config.min_samples_leaf)
        for config in MODEL_CONFIG_GRID
    ] == [
        ("poisson_conservative", "poisson", 7, 80),
        ("poisson_medium", "poisson", 15, 40),
        ("squared_error_control", "squared_error", 15, 40),
    ]
    assert all(
        (
            config.learning_rate,
            config.max_iter,
            config.l2_regularization,
            config.early_stopping,
            config.random_state,
        )
        == (0.05, 250, 1.0, False, 42)
        for config in MODEL_CONFIG_GRID
    )
    with pytest.raises(FrozenInstanceError):
        MODEL_CONFIG_GRID[0].max_iter = 1  # type: ignore[misc]

    contract = model_grid_contract()
    assert json.loads(json.dumps(contract))["strategy"] == "global_direct_long_format"
    assert all(
        config["categorical_features"]
        == [
            "sku_index",
            "horizon",
            "target_day_of_week",
            "target_month",
        ]
        for config in contract["configs"]  # type: ignore[index]
    )


def test_partition_development_folds_is_fixed_to_twenty() -> None:
    start = pd.Timestamp("2021-01-01")
    folds = tuple(_fold(index, start + timedelta(days=14 * index)) for index in range(20))

    tuning, validation = partition_development_folds(folds)

    assert [fold.index for fold in tuning] == list(range(14))
    assert [fold.index for fold in validation] == list(range(14, 20))
    with pytest.raises(DataContractError, match="exactly 20"):
        partition_development_folds(folds[:-1])


def test_fit_predict_is_deterministic_causal_and_metric_compatible() -> None:
    cutoff = pd.Timestamp("2021-04-01")
    fold = _fold(14, cutoff)
    table = _table(cutoff)
    config = MODEL_CONFIG_GRID[0]

    first = fit_predict_folds(table, (fold,), config)
    second = fit_predict_folds(table, (fold,), config)

    pd.testing.assert_frame_equal(first.predictions, second.predictions)
    assert set(first.predictions.columns) == {
        "fold",
        "cutoff",
        "date",
        "sku",
        "horizon",
        "actual",
        "prediction",
        "model",
    }
    assert len(first.predictions) == 2 * 14
    assert first.predictions["prediction"].ge(0).all()
    assert first.audit.loc[0, "train_max_target_date"] == cutoff
    assert first.audit.loc[0, "evaluation_rows"] == 28
    assert first.audit.loc[0, "raw_negative_predictions"] == 0


def test_fit_predict_clips_and_audits_negative_raw_predictions(monkeypatch) -> None:
    cutoff = pd.Timestamp("2021-04-01")
    table = _table(cutoff, skus=("A",))

    class FakeRegressor:
        def __init__(self, **parameters: object) -> None:
            self.parameters = parameters

        def fit(self, features: pd.DataFrame, target: np.ndarray) -> FakeRegressor:
            assert len(features) == len(target)
            return self

        def predict(self, features: pd.DataFrame) -> np.ndarray:
            return np.linspace(-2.0, 2.0, len(features))

    monkeypatch.setattr("retail_forecasting.modeling.HistGradientBoostingRegressor", FakeRegressor)
    result = fit_predict_folds(table, (_fold(14, cutoff),), MODEL_CONFIG_GRID[2])

    assert result.predictions["prediction"].ge(0).all()
    assert result.audit.loc[0, "raw_negative_predictions"] == 7
    assert result.audit.loc[0, "raw_prediction_min"] == -2.0
    assert result.audit.loc[0, "prediction_min"] == 0.0


def test_future_labels_cannot_change_fold_predictions() -> None:
    cutoff = pd.Timestamp("2021-04-01")
    table = _table(cutoff, skus=("A",))
    fold = _fold(14, cutoff)
    original = fit_predict_folds(table, (fold,), MODEL_CONFIG_GRID[0])

    mutated = table.copy()
    future_labels = mutated["target_date"].gt(cutoff)
    mutated.loc[future_labels, "actual"] = mutated.loc[future_labels, "actual"] + 10_000.0
    repeated = fit_predict_folds(mutated, (fold,), MODEL_CONFIG_GRID[0])

    np.testing.assert_array_equal(
        original.predictions["prediction"].to_numpy(),
        repeated.predictions["prediction"].to_numpy(),
    )


def test_fit_predict_rejects_an_incomplete_forecast_grid() -> None:
    cutoff = pd.Timestamp("2021-04-01")
    table = _table(cutoff)
    missing = table.loc[
        ~(table["origin_date"].eq(cutoff) & table["sku"].eq("A") & table["horizon"].eq(14))
    ]

    with pytest.raises(DataContractError, match="complete 14-horizon"):
        fit_predict_folds(missing, (_fold(14, cutoff),), MODEL_CONFIG_GRID[1])


def test_model_config_rejects_unsupported_loss() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ModelConfig(
            name="invalid",
            loss="absolute_error",  # type: ignore[arg-type]
            max_leaf_nodes=7,
            min_samples_leaf=80,
        )
