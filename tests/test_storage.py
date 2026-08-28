from datetime import date, timedelta
from pathlib import Path

import pytest

from retail_forecasting.dataset import DataContractError
from retail_forecasting.storage import POSTGRES_SCHEMA, JsonForecastRepository


def _fixture():
    run = {
        "run_id": "abc",
        "cutoff": "2024-01-01",
        "model": "seasonal_naive_7d",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    rows = [
        {
            "run_id": "abc",
            "cutoff": "2024-01-01",
            "forecast_date": (date(2024, 1, 1) + timedelta(days=h)).isoformat(),
            "sku": "A",
            "horizon": h,
            "prediction": 2.0,
            "lower": 1.0,
            "upper": 3.0,
            "model": "seasonal_naive_7d",
        }
        for h in range(1, 3)
    ]
    return run, rows


def test_json_repository_migrates_and_is_idempotent(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    repository.migrate()
    run, rows = _fixture()
    assert repository.save_run(run, rows) is True
    assert repository.save_run(run, rows) is False
    assert repository.list_forecasts(sku="A") == rows
    assert repository.health()["status"] == "ok"


def test_json_repository_rejects_run_id_collision(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    run, rows = _fixture()
    repository.save_run(run, rows)
    rows[0]["prediction"] = 2.5
    with pytest.raises(DataContractError, match="different content"):
        repository.save_run(run, rows)


def test_json_monitoring_is_strict_and_idempotent(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    run, forecasts = _fixture()
    repository.save_run(run, forecasts)
    row = {
        key: forecasts[0][key]
        for key in (
            "run_id",
            "forecast_date",
            "sku",
            "horizon",
            "prediction",
            "lower",
            "upper",
        )
    }
    row.update(actual=4.0, absolute_error=2.0, covered=False)
    assert repository.save_monitoring("abc", [row]) is True
    assert repository.save_monitoring("abc", [row]) is False
    assert repository.list_monitoring() == [row]
    with pytest.raises(DataContractError, match="do not reconcile"):
        repository.save_monitoring("abc", [{**row, "absolute_error": 1.0}])


def test_postgres_migration_is_idempotent_by_contract():
    assert "CREATE TABLE IF NOT EXISTS forecast_runs" in POSTGRES_SCHEMA
    assert "CREATE INDEX IF NOT EXISTS" in POSTGRES_SCHEMA


def test_json_forecast_query_filters_run_before_applying_limit(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    first_run, first_rows = _fixture()
    repository.save_run(first_run, first_rows)
    second_run = {
        **first_run,
        "run_id": "second",
        "cutoff": "2024-02-01",
        "created_at": "2024-02-01T00:00:00+00:00",
    }
    second_rows = [
        {
            **first_rows[0],
            "run_id": "second",
            "cutoff": "2024-02-01",
            "forecast_date": "2024-02-02",
        }
    ]
    repository.save_run(second_run, second_rows)
    assert repository.list_forecasts(run_id="second", limit=1) == second_rows


def test_postgres_writes_are_serialized_by_run_id():
    source = (Path(__file__).parents[1] / "src" / "retail_forecasting" / "storage.py").read_text(
        encoding="utf-8"
    )
    assert source.count("pg_advisory_xact_lock(hashtextextended(%s, 0))") == 2
