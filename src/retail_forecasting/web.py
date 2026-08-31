from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from retail_forecasting.dataset import DataContractError
from retail_forecasting.service import create_app
from retail_forecasting.storage import (
    ForecastRepository,
    ImmutableJsonForecastRepository,
    JsonForecastRepository,
    PostgresForecastRepository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEMO_SNAPSHOT = PROJECT_ROOT / "demo" / "demo_snapshot.json"
APPROVED_DEMO_SNAPSHOT_SHA256 = "6a1e418049eb2a2c5094be44c6cf722452a2d9c471ba2a230c5c9f0488f4caad"


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


def verify_snapshot_integrity(path: Path, expected_sha256: str) -> str:
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise DataContractError("DEMO_SNAPSHOT_SHA256 must be a 64-character SHA-256 hex digest.")
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DataContractError(f"Demo snapshot cannot be hashed: {path}") from exc
    if not hmac.compare_digest(actual, expected):
        raise DataContractError("Demo snapshot SHA-256 does not match the reviewed artifact.")
    return actual


def _environment_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise DataContractError(f"{name} must be one of 1, 0, true, false, yes or no.")


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
    public_demo = _environment_flag("PUBLIC_DEMO")
    if public_demo and database_url:
        raise DataContractError("The public demo must use the immutable JSON snapshot.")
    if public_demo:
        expected_sha256 = os.environ.get("DEMO_SNAPSHOT_SHA256", APPROVED_DEMO_SNAPSHOT_SHA256)
        verify_snapshot_integrity(snapshot, expected_sha256)
    if database_url:
        repository: ForecastRepository = PostgresForecastRepository(database_url)
        repository.migrate()
        if _environment_flag("SEED_DEMO", default=True):
            seed_repository(repository, snapshot)
    elif public_demo:
        repository = ImmutableJsonForecastRepository(snapshot)
        repository.migrate()
    else:
        repository = JsonForecastRepository(snapshot)
        repository.migrate()
    allowed_hosts = [
        host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()
    ]
    if public_demo and not allowed_hosts:
        raise DataContractError("ALLOWED_HOSTS is required for the public demo.")
    return create_app(repository, public_demo=public_demo, allowed_hosts=allowed_hosts or None)


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
        server_header=False,
        date_header=False,
    )


if __name__ == "__main__":
    main()
