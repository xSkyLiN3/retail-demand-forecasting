from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from retail_forecasting.backtesting import make_folds, seasonal_naive_predictions
from retail_forecasting.comparison import compare_candidate_to_baseline
from retail_forecasting.dataset import DataContractError


def _evidence(*, zero_confirmation_actuals: bool = False):
    records: list[dict[str, object]] = []
    dates = pd.date_range("2020-01-01", periods=112, freq="D")
    for day_index, day in enumerate(dates):
        for sku_index in range(20):
            units = (day_index % 11) + sku_index + 1
            if zero_confirmation_actuals and day_index >= 28:
                units = 0
            records.append({"date": day, "sku": f"SKU-{sku_index:02d}", "units": units})
    panel = pd.DataFrame(records)
    generated = make_folds(
        panel,
        initial_train_days=28,
        horizon_days=14,
        step_days=14,
        min_folds=6,
        final_holdout_days=0,
    )
    folds = tuple(replace(fold, index=14 + position) for position, fold in enumerate(generated))
    baseline = seasonal_naive_predictions(panel, folds)
    baseline["model"] = "baseline"
    baseline["prediction"] = baseline["actual"] + 5.0
    candidate = baseline.copy()
    candidate["model"] = "candidate"
    candidate["prediction"] = candidate["actual"]
    return panel, candidate, baseline


def test_comparison_promotes_a_candidate_that_passes_every_gate() -> None:
    panel, candidate, baseline = _evidence()

    result = compare_candidate_to_baseline(candidate, baseline, panel)

    assert result["promoted"] is True
    assert result["paired_rows"] == 6 * 14 * 20
    assert result["criteria"]["relative_wape_improvement"]["value"] == 1.0
    assert result["criteria"]["fold_wins"]["value"] == 6
    assert result["criteria"]["sku_wins"]["value"] == 20
    assert [row["fold"] for row in result["by_fold"]] == list(range(14, 20))
    assert [row["horizon_range"] for row in result["by_horizon_range"]] == ["1-7", "8-14"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "actual"])
def test_comparison_rejects_unpaired_or_inconsistent_evidence(mutation) -> None:
    panel, candidate, baseline = _evidence()
    if mutation == "missing":
        candidate = candidate.iloc[1:].copy()
        message = "keys differ"
    elif mutation == "duplicate":
        candidate = pd.concat([candidate, candidate.iloc[[0]]], ignore_index=True)
        message = "duplicate comparison keys"
    else:
        candidate.loc[0, "actual"] += 1
        message = "actual values do not match"

    with pytest.raises(DataContractError, match=message):
        compare_candidate_to_baseline(candidate, baseline, panel)


def test_null_metrics_remain_null_and_fail_the_gate() -> None:
    panel, candidate, baseline = _evidence(zero_confirmation_actuals=True)
    candidate["prediction"] = 0.0
    baseline["prediction"] = 0.0

    result = compare_candidate_to_baseline(candidate, baseline, panel)

    assert result["candidate"]["overall"]["wape"] is None
    assert result["baseline"]["overall"]["normalized_bias"] is None
    assert result["criteria"]["relative_wape_improvement"]["value"] is None
    assert result["criteria"]["relative_wape_improvement"]["passed"] is False
    assert result["by_horizon_range"][0]["candidate"]["wape"] is None
    assert result["promoted"] is False
