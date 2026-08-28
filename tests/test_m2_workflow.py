from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from retail_forecasting.dataset import DataContractError
from retail_forecasting.m2_workflow import (
    M2EvidenceHashes,
    build_m2_partition,
    freeze_m2_workflow,
    prepare_holdout_evaluation,
)


def _panel() -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=365 + 20 * 14 + 84 + 10, freq="D")
    return pd.DataFrame(
        [
            {"date": date, "sku": sku, "units": (date.dayofyear + offset) % 9}
            for date in dates
            for offset, sku in enumerate(("A", "B"))
        ]
    )


def _hashes() -> M2EvidenceHashes:
    return M2EvidenceHashes(*("a" * 64, "b" * 64, "c" * 64, "d" * 64))


def _calibrate(
    predictions: pd.DataFrame, _panel: pd.DataFrame, nominal: float
) -> dict[str, object]:
    return {
        "max_date": pd.Timestamp(predictions["date"].max()).date().isoformat(),
        "nominal_coverage": nominal,
        "rows": len(predictions),
    }


def _apply(predictions: pd.DataFrame, _panel: pd.DataFrame, calibration: object) -> pd.DataFrame:
    result = predictions.copy()
    result["lower"] = (result["prediction"] - 1).clip(lower=0)
    result["upper"] = result["prediction"] + 1
    return result


def _monitor(predictions: pd.DataFrame, outcomes: pd.DataFrame, thresholds) -> dict[str, object]:
    assert len(outcomes) == len(predictions)
    return {"rows": len(predictions), "thresholds": dict(thresholds)}


def test_partition_has_twenty_development_and_exact_final_84_days() -> None:
    panel = _panel()
    partition = build_m2_partition(panel, initial_train_days=365)

    assert len(partition.development) == 20
    assert len(partition.holdout) == 6
    assert partition.holdout[0].index == 20
    assert partition.holdout[-1].index == 25
    assert partition.holdout[0].test_start == panel["date"].max() - timedelta(days=83)
    assert partition.holdout[-1].test_end == panel["date"].max()
    assert partition.development[-1].test_end < partition.holdout[0].test_start
    assert partition.gap_days == 10


def test_freeze_uses_only_prior_outcomes_for_prequential_replay() -> None:
    frozen = freeze_m2_workflow(
        _panel(),
        initial_train_days=365,
        hashes=_hashes(),
        m1_champion="seasonal_naive_7d",
        calibrate=_calibrate,
        apply_intervals=_apply,
        monitor=_monitor,
    )

    assert len(frozen.replay) == 14
    assert frozen.replay[0]["calibration_folds"] == list(range(6))
    for window in frozen.replay:
        assert window["calibration_max_outcome_date"] <= window["as_of"]
    assert frozen.calibration_contract["rows"] == 20 * 14 * 2
    assert frozen.contract["partition"]["holdout_status"] == "frozen_not_evaluated"
    assert frozen.contract["thresholds"] == {
        "minimum_coverage": 0.85,
        "maximum_coverage": 0.98,
        "maximum_absolute_bias": 0.10,
        "maximum_wape": 2.0,
    }
    assert "contract_sha256" in frozen.contract


def test_freeze_rejects_a_different_m1_champion() -> None:
    with pytest.raises(DataContractError, match="frozen for seasonal_naive_7d"):
        freeze_m2_workflow(
            _panel(),
            initial_train_days=365,
            hashes=_hashes(),
            m1_champion="poisson_conservative",
            calibrate=_calibrate,
            apply_intervals=_apply,
            monitor=_monitor,
        )


def test_freeze_integrates_real_interval_and_monitoring_apis() -> None:
    frozen = freeze_m2_workflow(
        _panel(),
        initial_train_days=365,
        hashes=_hashes(),
        m1_champion="seasonal_naive_7d",
    )

    assert frozen.calibration_contract["residual"] == ("(actual-prediction)/causal_seasonal_scale")
    assert frozen.calibration_contract["fallback"] == "raw_signed_residual_by_horizon"
    assert frozen.replay[0]["monitoring"]["nominal_coverage"] == 0.90
    assert "alerts" in frozen.replay[0]["monitoring"]


def test_holdout_preparation_does_not_generate_predictions(monkeypatch) -> None:
    frozen = freeze_m2_workflow(
        _panel(),
        initial_train_days=365,
        hashes=_hashes(),
        m1_champion="seasonal_naive_7d",
        calibrate=_calibrate,
        apply_intervals=_apply,
        monitor=_monitor,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("holdout preparation must not generate predictions")

    monkeypatch.setattr("retail_forecasting.m2_workflow.seasonal_naive_predictions", forbidden)
    plan = prepare_holdout_evaluation(frozen)

    assert plan.champion == "seasonal_naive_7d"
    assert len(plan.folds) == 6
    assert plan.folds[0].index == 20
    assert plan.interval_calibration is frozen.calibration


def test_holdout_preparation_rejects_contract_tampering() -> None:
    frozen = freeze_m2_workflow(
        _panel(),
        initial_train_days=365,
        hashes=_hashes(),
        m1_champion="seasonal_naive_7d",
        calibrate=_calibrate,
        apply_intervals=_apply,
        monitor=_monitor,
    )
    tampered_contract = {**frozen.contract, "champion": "poisson_conservative"}
    tampered = replace(frozen, contract=tampered_contract)

    with pytest.raises(DataContractError, match="modified"):
        prepare_holdout_evaluation(tampered)


def test_hash_contract_rejects_non_sha_values() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        M2EvidenceHashes("not-a-hash", *("b" * 64, "c" * 64, "d" * 64))
