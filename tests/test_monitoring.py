from __future__ import annotations

import pandas as pd
import pytest

from retail_forecasting.dataset import DataContractError
from retail_forecasting.monitoring import AlertThresholds, monitor_forecasts


def _forecasts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fold": [0, 0, 0, 0],
            "cutoff": pd.to_datetime(["2020-01-01"] * 4),
            "date": pd.to_datetime(["2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03"]),
            "sku": ["A", "B", "A", "B"],
            "horizon": [1, 1, 2, 2],
            "prediction": [10.0, 0.0, 8.0, 0.0],
            "lower": [8.0, 0.0, 6.0, 0.0],
            "upper": [12.0, 1.0, 10.0, 1.0],
        }
    )


def _outcomes(*, all_zero: bool = False) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03"]),
            "sku": ["A", "B", "A", "B"],
            "actual": [0.0, 0.0, 0.0, 0.0] if all_zero else [11.0, 0.0, 14.0, 0.0],
        }
    )


def test_monitoring_reports_interval_and_point_metrics_in_all_views() -> None:
    report = monitor_forecasts(
        _forecasts(),
        _outcomes(),
        thresholds=AlertThresholds(minimum_coverage=0.90),
    )

    assert report["overall"]["coverage"] == 0.75
    assert report["overall"]["mean_width"] == 2.5
    assert report["overall"]["winkler"] > report["overall"]["mean_width"]
    assert len(report["by_horizon"]) == 2
    assert len(report["by_sku"]) == 2
    assert any(alert["metric"] == "coverage" for alert in report["alerts"])


def test_zero_demand_leaves_ratio_metrics_nullable() -> None:
    report = monitor_forecasts(_forecasts(), _outcomes(all_zero=True))

    assert report["overall"]["wape"] is None
    assert report["overall"]["normalized_bias"] is None
    assert report["overall"]["mae"] is not None


def test_monitoring_rejects_duplicate_or_missing_outcomes() -> None:
    duplicate = pd.concat([_outcomes(), _outcomes().iloc[[0]]], ignore_index=True)
    with pytest.raises(DataContractError, match="unique"):
        monitor_forecasts(_forecasts(), duplicate)

    with pytest.raises(DataContractError, match="exactly one"):
        monitor_forecasts(_forecasts(), _outcomes().iloc[:-1])


def test_alert_thresholds_are_predeclarable() -> None:
    report = monitor_forecasts(
        _forecasts(),
        _outcomes(),
        thresholds=AlertThresholds(
            minimum_coverage=None,
            maximum_coverage=None,
            maximum_absolute_bias=None,
            maximum_wape=0.01,
            maximum_mean_width=1.0,
        ),
    )

    metrics = {alert["metric"] for alert in report["alerts"]}
    assert {"wape", "mean_width"}.issubset(metrics)
