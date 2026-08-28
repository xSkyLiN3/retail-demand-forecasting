from __future__ import annotations

from typing import Any

from retail_forecasting import __version__
from retail_forecasting.dashboard import dashboard_html
from retail_forecasting.storage import ForecastRepository


def create_app(repository: ForecastRepository) -> Any:
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise RuntimeError("The web service requires fastapi and uvicorn.") from exc

    app = FastAPI(title="Retail Forecasting Demo", version=__version__, docs_url="/docs")

    @app.get("/health")
    def health() -> dict[str, Any]:
        try:
            return repository.health()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Storage is unavailable.") from exc

    @app.get("/api/forecasts")
    def forecasts(
        run_id: str | None = None,
        sku: str | None = None,
        cutoff: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict[str, Any]:
        rows = repository.list_forecasts(run_id=run_id, sku=sku, cutoff=cutoff, limit=limit)
        return {
            "count": len(rows),
            "items": rows,
            "disclaimer": "Educational historical-data demo; not purchasing advice.",
        }

    @app.get("/api/monitoring")
    def monitoring(
        run_id: str | None = None,
        sku: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict[str, Any]:
        rows = repository.list_monitoring(run_id=run_id, sku=sku, limit=limit)
        return {
            "count": len(rows),
            "items": rows,
            "disclaimer": "Monitoring is a historical replay, not live operations.",
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return dashboard_html()

    return app
