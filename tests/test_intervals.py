from __future__ import annotations

import copy
from datetime import timedelta

import pandas as pd
import pytest

from retail_forecasting.dataset import DataContractError
from retail_forecasting.intervals import (
    IntervalCalibration,
    IntervalConfig,
    apply_intervals,
    calibrate_intervals,
)


def _panel(days: int = 30, *, constant_sku_b: bool = False) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for index, date in enumerate(pd.date_range("2020-01-01", periods=days, freq="D")):
        records.append({"date": date, "sku": "A", "units": index % 5 + index // 7})
        records.append({"date": date, "sku": "B", "units": 0 if constant_sku_b else index % 3})
    return pd.DataFrame(records)


def _predictions(panel: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for fold, cutoff in enumerate(pd.to_datetime(["2020-01-14", "2020-01-21"])):
        for horizon in (1, 2):
            date = cutoff + timedelta(days=horizon)
            for sku in ("A", "B"):
                actual = float(
                    panel.loc[(panel["date"] == date) & (panel["sku"] == sku), "units"].iloc[0]
                )
                records.append(
                    {
                        "fold": fold,
                        "cutoff": cutoff,
                        "date": date,
                        "sku": sku,
                        "horizon": horizon,
                        "actual": actual,
                        "prediction": max(0.0, actual - 1.0),
                    }
                )
    return pd.DataFrame(records)


def test_calibration_is_deterministic_and_intervals_contain_point() -> None:
    panel = _panel()
    predictions = _predictions(panel)

    first = calibrate_intervals(predictions, panel, config=IntervalConfig(seasonality_days=7))
    second = calibrate_intervals(predictions, panel, config=IntervalConfig(seasonality_days=7))
    result = apply_intervals(predictions.drop(columns="actual"), panel, first)

    assert first.contract() == second.contract()
    assert result["lower"].le(result["prediction"]).all()
    assert result["prediction"].le(result["upper"]).all()
    assert result["lower"].ge(0).all()


def test_zero_seasonal_scale_uses_raw_fallback() -> None:
    panel = _panel(constant_sku_b=True)
    calibration = calibrate_intervals(_predictions(panel), panel)
    result = apply_intervals(_predictions(panel), panel, calibration)

    assert calibration.quantiles["fallback_rows"].sum() > 0
    assert result.loc[result["sku"] == "B", "uses_raw_fallback"].all()


def test_future_panel_mutation_does_not_change_calibration() -> None:
    panel = _panel()
    predictions = _predictions(panel)
    original = calibrate_intervals(predictions, panel).contract()
    mutated = panel.copy()
    mutated.loc[mutated["date"] > predictions["date"].max(), "units"] += 10000

    assert calibrate_intervals(predictions, mutated).contract() == original


def test_calibration_reconciles_actuals_with_panel() -> None:
    panel = _panel()
    predictions = _predictions(panel)
    predictions.loc[0, "actual"] += 1

    with pytest.raises(DataContractError, match="actuals"):
        calibrate_intervals(predictions, panel)


def test_calibration_rejects_incomplete_fold_grid() -> None:
    panel = _panel()
    predictions = _predictions(panel).iloc[:-1]

    with pytest.raises(DataContractError, match="complete"):
        calibrate_intervals(predictions, panel)


def _fourteen_horizon_predictions(panel: pd.DataFrame) -> pd.DataFrame:
    cutoff = pd.Timestamp("2020-01-14")
    records: list[dict[str, object]] = []
    for horizon in range(1, 15):
        date = cutoff + timedelta(days=horizon)
        for sku in ("A", "B"):
            actual = float(
                panel.loc[(panel["date"] == date) & (panel["sku"] == sku), "units"].iloc[0]
            )
            records.append(
                {
                    "fold": 0,
                    "cutoff": cutoff,
                    "date": date,
                    "sku": sku,
                    "horizon": horizon,
                    "actual": actual,
                    "prediction": max(0.0, actual - 1.0),
                }
            )
    return pd.DataFrame(records)


def test_persisted_calibration_round_trip_is_exact() -> None:
    panel = _panel()
    calibration = calibrate_intervals(_fourteen_horizon_predictions(panel), panel)

    restored = IntervalCalibration.from_contract(calibration.contract())

    assert restored.contract() == calibration.contract()


@pytest.mark.parametrize(
    "mutation",
    ["method", "coverage", "duplicate_horizon", "non_finite", "not_anchored", "fields"],
)
def test_persisted_calibration_rejects_tampering(mutation: str) -> None:
    panel = _panel()
    contract = calibrate_intervals(_fourteen_horizon_predictions(panel), panel).contract()
    tampered = copy.deepcopy(contract)
    if mutation == "method":
        tampered["quantile_method"] = "linear"
    elif mutation == "coverage":
        tampered["nominal_coverage"] = 1.0
    elif mutation == "duplicate_horizon":
        tampered["values"][1]["horizon"] = 1
    elif mutation == "non_finite":
        tampered["values"][0]["raw_upper"] = float("inf")
    elif mutation == "not_anchored":
        tampered["values"][0]["scaled_lower"] = 0.1
    else:
        tampered["values"][0]["unexpected"] = 1

    with pytest.raises(DataContractError):
        IntervalCalibration.from_contract(tampered)
