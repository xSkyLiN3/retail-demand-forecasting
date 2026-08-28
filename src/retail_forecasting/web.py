from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from retail_forecasting.dataset import DataContractError
from retail_forecasting.service import create_app
from retail_forecasting.storage import (
    ForecastRepository,
    JsonForecastRepository,
    PostgresForecastRepository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEMO_SNAPSHOT = PROJECT_ROOT / "demo" / "demo_snapshot.json"


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataContractError(f"Demo snapshot not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError("Demo snapshot is unreadable or invalid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise DataContractError("Demo snapshot has an unsupported schema.")
    for key in ("runs", "forecasts", "monitoring"):
        if not isinstance(payload.get(key), list):
            raise DataContractError(f"Demo snapshot field {key!r} must be a list.")
    return payload


def seed_repository(repository: ForecastRepository, snapshot_path: Path) -> None:
    snapshot = _read_snapshot(snapshot_path)
    forecasts = snapshot["forecasts"]
    monitoring = snapshot["monitoring"]
    for run in snapshot["runs"]:
        run_id = str(run.get("run_id", ""))
        run_forecasts = [row for row in forecasts if row.get("run_id") == run_id]
        repository.save_run(run, run_forecasts)
        run_monitoring = [row for row in monitoring if row.get("run_id") == run_id]
        if run_monitoring:
            repository.save_monitoring(run_id, run_monitoring)


def create_application():
    """Uvicorn factory configured through environment variables."""

    snapshot = Path(os.environ.get("DEMO_SNAPSHOT_PATH", DEFAULT_DEMO_SNAPSHOT))
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        repository: ForecastRepository = PostgresForecastRepository(database_url)
        repository.migrate()
        if os.environ.get("SEED_DEMO", "1") == "1":
            seed_repository(repository, snapshot)
    else:
        repository = JsonForecastRepository(snapshot)
        repository.migrate()
    return create_app(repository)


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Serving the demo requires uvicorn.") from exc
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise DataContractError("PORT must be an integer.") from exc
    uvicorn.run(
        "retail_forecasting.web:create_application",
        factory=True,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
