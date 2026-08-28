import pandas as pd
import pytest

from retail_forecasting.batch import reconcile_known_outcomes, run_champion_batch
from retail_forecasting.dataset import DataContractError
from retail_forecasting.storage import JsonForecastRepository


def _panel():
    dates = pd.date_range("2024-01-01", periods=12)
    return pd.DataFrame(
        [
            {"date": day, "sku": sku, "units": index + offset}
            for index, day in enumerate(dates)
            for sku, offset in (("A", 0), ("B", 10))
        ]
    )


def _calibration():
    return {
        "nominal_coverage": 0.9,
        "absolute_residual_quantile_by_horizon": {str(h): float(h) for h in range(1, 15)},
    }


def test_batch_is_as_of_deterministic_and_idempotent(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    first = run_champion_batch(
        _panel(), cutoff="2024-01-09", calibration=_calibration(), repository=repository
    )
    changed = _panel()
    changed.loc[changed["date"] > "2024-01-09", "units"] = 9999
    second = run_champion_batch(
        changed, cutoff="2024-01-09", calibration=_calibration(), repository=repository
    )
    assert first["run_id"] == second["run_id"]
    assert first["created"] is True and second["created"] is False
    assert first["forecast_rows"] == 28


def test_batch_rejects_incomplete_calibration(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    calibration = _calibration()
    del calibration["absolute_residual_quantile_by_horizon"]["14"]
    with pytest.raises(DataContractError, match="horizon 14"):
        run_champion_batch(
            _panel(), cutoff="2024-01-09", calibration=calibration, repository=repository
        )


def test_reconciliation_persists_only_known_forecast_outcomes(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    run = run_champion_batch(
        _panel(), cutoff="2024-01-09", calibration=_calibration(), repository=repository
    )
    outcomes = pd.DataFrame([{"date": "2024-01-10", "sku": "A", "actual": 99}])
    first = reconcile_known_outcomes(repository, run_id=run["run_id"], outcomes=outcomes)
    second = reconcile_known_outcomes(repository, run_id=run["run_id"], outcomes=outcomes)
    assert first == {"run_id": run["run_id"], "created": True, "monitoring_rows": 1}
    assert second["created"] is False
    assert len(repository.list_monitoring()) == 1


def test_reconciliation_queries_the_requested_run_directly():
    class RecordingRepository:
        def __init__(self):
            self.query = None

        def list_forecasts(self, **kwargs):
            self.query = kwargs
            return []

    repository = RecordingRepository()
    outcomes = pd.DataFrame([{"date": "2024-01-10", "sku": "A", "actual": 1}])
    with pytest.raises(DataContractError, match="No persisted forecasts"):
        reconcile_known_outcomes(repository, run_id="selected", outcomes=outcomes)
    assert repository.query == {"run_id": "selected", "limit": 5_000}
