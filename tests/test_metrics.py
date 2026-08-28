from __future__ import annotations

import pandas as pd
import pytest

from retail_forecasting.backtesting import make_folds, seasonal_naive_predictions
from retail_forecasting.dataset import DataContractError
from retail_forecasting.metrics import metric_values, seasonal_scales, summarize_predictions


def test_metric_values_are_directionally_explicit() -> None:
    result = metric_values(
        pd.Series([10.0, 20.0]),
        pd.Series([12.0, 16.0]),
    )

    assert result["mae"] == 3.0
    assert result["wape"] == 0.2
    assert result["normalized_bias"] == -2.0 / 30.0


def test_seasonal_scale_uses_training_history_only() -> None:
    training = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=6, freq="D"),
            "sku": ["A"] * 6,
            "units": [1, 2, 3, 3, 5, 7],
        }
    )

    assert seasonal_scales(training, seasonality_days=3) == {"A": 3.0}


def _evaluated_baseline() -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=35, freq="D"),
            "sku": ["A"] * 35,
            "units": list(range(35)),
        }
    )
    fold = make_folds(
        panel,
        initial_train_days=21,
        horizon_days=7,
        step_days=7,
        min_folds=1,
        final_holdout_days=0,
    )[0]
    return panel, seasonal_naive_predictions(panel, (fold,), seasonality_days=7)


def test_summary_reconciles_outcomes_and_reports_all_views() -> None:
    panel, predictions = _evaluated_baseline()

    summary = summarize_predictions(predictions, panel, horizon_days=7)

    assert summary["overall"]["mase"] is not None
    assert len(summary["by_fold"]) == 1
    assert len(summary["by_sku"]) == 1
    assert len(summary["by_horizon"]) == 7


@pytest.mark.parametrize("mutation", ["actual", "horizon", "prediction"])
def test_summary_rejects_invalid_evidence(mutation) -> None:
    panel, predictions = _evaluated_baseline()
    if mutation == "actual":
        predictions.loc[0, "actual"] += 1
    elif mutation == "horizon":
        predictions.loc[0, "horizon"] += 1
    else:
        predictions.loc[0, "prediction"] = -1

    with pytest.raises(DataContractError):
        summarize_predictions(predictions, panel, horizon_days=7)


def test_summary_rejects_the_same_window_under_two_fold_ids() -> None:
    panel, predictions = _evaluated_baseline()
    repeated = predictions.copy()
    repeated["fold"] = 1

    with pytest.raises(DataContractError, match="non-overlapping"):
        summarize_predictions(
            pd.concat([predictions, repeated], ignore_index=True),
            panel,
            horizon_days=7,
        )


def test_summary_accepts_a_contiguous_offset_fold_partition() -> None:
    panel, predictions = _evaluated_baseline()
    predictions["fold"] = 14

    summary = summarize_predictions(predictions, panel, horizon_days=7)

    assert summary["by_fold"][0]["fold"] == 14
