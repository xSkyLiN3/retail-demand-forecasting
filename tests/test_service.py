import importlib.util

import pytest

from retail_forecasting.service import create_app
from retail_forecasting.storage import JsonForecastRepository

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None,
    reason="FastAPI optional dependency is not installed",
)


def test_service_exposes_read_only_health_forecast_monitoring_and_dashboard(tmp_path):
    from fastapi.testclient import TestClient

    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    client = TestClient(create_app(repository))
    assert client.get("/health").status_code == 200
    assert client.get("/api/forecasts").json()["items"] == []
    assert client.get("/api/monitoring").json()["items"] == []
    page = client.get("/").text
    assert "Educational historical demo" in page
    assert "/api/forecasts" in page and "/api/monitoring" in page
    assert "Interval coverage" in page and "Normalized bias" in page
    assert "Forecast run" in page and "Product SKU" in page
    assert "cdn" not in page.lower()
    assert client.post("/api/forecasts").status_code == 405
