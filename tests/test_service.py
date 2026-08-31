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
    page_response = client.get("/")
    page = page_response.text
    assert "Educational historical demo" in page
    assert "NO-GO for operational use" in page
    assert "77.02%" in page and "85% minimum" in page
    assert "Forecast issue date" in page and "Product SKU" in page
    assert '<link rel="stylesheet" href="/assets/dashboard.css">' in page
    assert '<script defer src="/assets/dashboard.js"></script>' in page
    assert '<link rel="canonical" href="https://retail.nightstrike.cloud/">' in page
    assert '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">' in page
    assert "<style>" not in page and "<script>" not in page
    assert "cdn" not in page.lower()
    assert "default-src 'none'" in page_response.headers["content-security-policy"]
    assert page_response.headers["x-frame-options"] == "DENY"

    stylesheet = client.get("/assets/dashboard.css")
    script = client.get("/assets/dashboard.js")
    assert stylesheet.status_code == 200 and "text/css" in stylesheet.headers["content-type"]
    assert script.status_code == 200 and "text/javascript" in script.headers["content-type"]
    assert "/api/forecasts" in script.text and "/api/monitoring" in script.text
    assert "innerHTML" not in script.text
    assert client.get("/api/forecasts").headers["cache-control"] == "no-store"
    assert stylesheet.headers["cache-control"] == "public, max-age=300"
    favicon = client.get("/assets/favicon.svg")
    assert favicon.status_code == 200 and "image/svg+xml" in favicon.headers["content-type"]

    docs = client.get("/docs")
    docs_csp = docs.headers["content-security-policy"]
    assert docs.status_code == 200 and "SwaggerUIBundle" in docs.text
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" in docs_csp
    assert "connect-src 'self'" in docs_csp
    assert client.post("/api/forecasts").status_code == 405


def test_public_service_hides_internal_health_and_api_docs(tmp_path):
    from fastapi.testclient import TestClient

    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()
    client = TestClient(create_app(repository, public_demo=True, allowed_hosts=["testserver"]))

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/api/forecasts?limit=2001").status_code == 422
    assert client.get("/api/forecasts?sku=%3Cscript%3E").status_code == 422
    assert client.get("/api/forecasts").headers["cache-control"] == "public, max-age=300"
    assert client.get("/", headers={"host": "untrusted.example"}).status_code == 400


def test_public_service_requires_explicit_trusted_hosts(tmp_path):
    repository = JsonForecastRepository(tmp_path / "demo.json")
    repository.migrate()

    with pytest.raises(ValueError, match="allowed_hosts"):
        create_app(repository, public_demo=True)
