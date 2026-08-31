from __future__ import annotations

from typing import Any

from retail_forecasting import __version__
from retail_forecasting.dashboard import (
    dashboard_css,
    dashboard_favicon,
    dashboard_html,
    dashboard_javascript,
)
from retail_forecasting.storage import ForecastRepository


def create_app(
    repository: ForecastRepository,
    *,
    public_demo: bool = False,
    allowed_hosts: list[str] | None = None,
) -> Any:
    if public_demo and not allowed_hosts:
        raise ValueError("allowed_hosts is required when public_demo is enabled.")

    try:
        from fastapi import FastAPI, HTTPException, Query, Request, Response
        from fastapi.responses import HTMLResponse
        from starlette.middleware.gzip import GZipMiddleware
        from starlette.middleware.trustedhost import TrustedHostMiddleware
    except ImportError as exc:
        raise RuntimeError("The web service requires fastapi and uvicorn.") from exc

    app = FastAPI(
        title="Retail Forecasting Demo",
        version=__version__,
        docs_url=None if public_demo else "/docs",
        redoc_url=None,
        openapi_url=None if public_demo else "/openapi.json",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        is_docs = request.url.path.startswith("/docs") or request.url.path == "/openapi.json"
        if is_docs:
            content_security_policy = (
                "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "connect-src 'self'; base-uri 'none'; object-src 'none'; "
                "frame-ancestors 'none'"
            )
        else:
            content_security_policy = (
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; base-uri 'none'; object-src 'none'; "
                "frame-ancestors 'none'; form-action 'none'"
            )
        response.headers["Content-Security-Policy"] = content_security_policy
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/assets/") or (
            public_demo and request.url.path.startswith("/api/")
        ):
            response.headers["Cache-Control"] = "public, max-age=300"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        try:
            health_payload = repository.health()
            return {"status": "ok"} if public_demo else health_payload
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Storage is unavailable.") from exc

    @app.get("/api/forecasts")
    def forecasts(
        run_id: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
        sku: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
        cutoff: str | None = Query(
            None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"
        ),
        limit: int = Query(500, ge=1, le=2000),
    ) -> dict[str, Any]:
        rows = repository.list_forecasts(run_id=run_id, sku=sku, cutoff=cutoff, limit=limit)
        return {
            "count": len(rows),
            "items": rows,
            "disclaimer": "Educational historical-data demo; not purchasing advice.",
        }

    @app.get("/api/monitoring")
    def monitoring(
        run_id: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
        sku: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
        limit: int = Query(500, ge=1, le=2000),
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

    @app.get("/assets/dashboard.css", include_in_schema=False)
    def dashboard_stylesheet() -> Response:
        return Response(dashboard_css(), media_type="text/css")

    @app.get("/assets/dashboard.js", include_in_schema=False)
    def dashboard_client() -> Response:
        return Response(dashboard_javascript(), media_type="text/javascript")

    @app.get("/assets/favicon.svg", include_in_schema=False)
    def favicon() -> Response:
        return Response(dashboard_favicon(), media_type="image/svg+xml")

    return app
