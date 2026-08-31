from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from retail_forecasting.dataset import DataContractError

SCHEMA_VERSION = 1


class ForecastRepository(Protocol):
    def migrate(self) -> None: ...

    def health(self) -> dict[str, Any]: ...

    def save_run(self, run: Mapping[str, Any], forecasts: Sequence[Mapping[str, Any]]) -> bool: ...

    def save_monitoring(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> bool: ...

    def list_forecasts(
        self,
        *,
        run_id: str | None = None,
        sku: str | None = None,
        cutoff: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def list_monitoring(
        self, *, run_id: str | None = None, sku: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]: ...


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _positive_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5_000:
        raise DataContractError("limit must be an integer between 1 and 5000.")
    return limit


def _validate_run(
    run: Mapping[str, Any], forecasts: Sequence[Mapping[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    run_id = str(run.get("run_id", "")).strip()
    cutoff = str(run.get("cutoff", "")).strip()
    model = str(run.get("model", "")).strip()
    if not run_id or not cutoff or not model:
        raise DataContractError("Run requires non-empty run_id, cutoff and model.")
    try:
        datetime.fromisoformat(cutoff)
    except ValueError as exc:
        raise DataContractError("Run cutoff must be ISO formatted.") from exc
    if not forecasts:
        raise DataContractError("A forecast run cannot be empty.")

    required = {
        "run_id",
        "cutoff",
        "forecast_date",
        "sku",
        "horizon",
        "prediction",
        "lower",
        "upper",
        "model",
    }
    normalized: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    for source in forecasts:
        missing = required.difference(source)
        if missing:
            raise DataContractError(f"Forecast record is missing fields: {sorted(missing)}")
        row = dict(source)
        if (
            str(row["run_id"]) != run_id
            or str(row["cutoff"]) != cutoff
            or str(row["model"]) != model
        ):
            raise DataContractError("Forecast records do not match their run contract.")
        sku = str(row["sku"]).strip()
        try:
            horizon = int(row["horizon"])
            forecast_date = datetime.fromisoformat(str(row["forecast_date"])).date()
            prediction, lower, upper = (
                float(row[name]) for name in ("prediction", "lower", "upper")
            )
        except (TypeError, ValueError) as exc:
            raise DataContractError("Forecast record contains invalid typed values.") from exc
        if not sku or horizon < 1 or not all(math.isfinite(v) for v in (prediction, lower, upper)):
            raise DataContractError("Forecast identifiers and bounds must be valid and finite.")
        if forecast_date != datetime.fromisoformat(cutoff).date() + timedelta(days=horizon):
            raise DataContractError("Forecast date must equal cutoff plus horizon.")
        if lower < 0 or prediction < 0 or not lower <= prediction <= upper:
            raise DataContractError(
                "Forecast bounds must satisfy 0 <= lower <= prediction <= upper."
            )
        key = (sku, horizon)
        if key in keys:
            raise DataContractError("Forecast run contains duplicate SKU/horizon keys.")
        keys.add(key)
        row.update(sku=sku, horizon=horizon, prediction=prediction, lower=lower, upper=upper)
        normalized.append(row)
    return run_id, normalized


def _validate_monitoring(run_id: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    run_id = str(run_id).strip()
    if not run_id or not rows:
        raise DataContractError("Monitoring requires a run id and at least one row.")
    required = {
        "run_id",
        "forecast_date",
        "sku",
        "horizon",
        "actual",
        "prediction",
        "lower",
        "upper",
        "absolute_error",
        "covered",
    }
    normalized: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    for source in rows:
        missing = required.difference(source)
        if missing:
            raise DataContractError(f"Monitoring row is missing fields: {sorted(missing)}")
        unexpected = set(source).difference(required)
        if unexpected:
            raise DataContractError(
                f"Monitoring row contains unexpected fields: {sorted(unexpected)}"
            )
        row = dict(source)
        sku = str(row["sku"]).strip()
        try:
            horizon = int(row["horizon"])
            datetime.fromisoformat(str(row["forecast_date"]))
            actual, prediction, lower, upper, error = (
                float(row[name])
                for name in ("actual", "prediction", "lower", "upper", "absolute_error")
            )
        except (TypeError, ValueError) as exc:
            raise DataContractError("Monitoring row contains invalid typed values.") from exc
        covered = row["covered"]
        if str(row["run_id"]) != run_id or not sku or horizon < 1:
            raise DataContractError("Monitoring row identifiers are invalid.")
        if not isinstance(covered, bool) or not all(
            math.isfinite(value) for value in (actual, prediction, lower, upper, error)
        ):
            raise DataContractError("Monitoring values must be finite and covered must be boolean.")
        expected_covered = lower <= actual <= upper
        if (
            min(actual, prediction, lower, upper, error) < 0
            or not lower <= prediction <= upper
            or not math.isclose(error, abs(actual - prediction), rel_tol=0, abs_tol=1e-12)
            or covered is not expected_covered
        ):
            raise DataContractError("Monitoring metrics do not reconcile with forecast and actual.")
        key = (sku, horizon)
        if key in keys:
            raise DataContractError("Monitoring rows contain duplicate SKU/horizon keys.")
        keys.add(key)
        row.update(
            sku=sku,
            horizon=horizon,
            actual=actual,
            prediction=prediction,
            lower=lower,
            upper=upper,
            absolute_error=error,
            covered=covered,
        )
        normalized.append(row)
    return normalized


class JsonForecastRepository:
    """Small atomic JSON repository intended only for the local/public demo."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def migrate(self) -> None:
        if not self.path.exists():
            self._write(
                {"schema_version": SCHEMA_VERSION, "runs": [], "forecasts": [], "monitoring": []}
            )
        else:
            self._read()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "runs": [], "forecasts": [], "monitoring": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataContractError("Demo repository is unreadable or invalid JSON.") from exc
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise DataContractError("Unsupported demo repository schema version.")
        for key in ("runs", "forecasts", "monitoring"):
            if not isinstance(payload.get(key), list):
                raise DataContractError(f"Demo repository field {key!r} must be a list.")
        return payload

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_value) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def health(self) -> dict[str, Any]:
        payload = self._read()
        return {"status": "ok", "backend": "json", "schema_version": payload["schema_version"]}

    def save_run(self, run: Mapping[str, Any], forecasts: Sequence[Mapping[str, Any]]) -> bool:
        run_id, rows = _validate_run(run, forecasts)
        payload = self._read()
        existing = next((item for item in payload["runs"] if item.get("run_id") == run_id), None)
        if existing is not None:
            existing_rows = [row for row in payload["forecasts"] if row.get("run_id") == run_id]
            if existing != dict(run) or existing_rows != rows:
                raise DataContractError("Run id already exists with different content.")
            return False
        payload["runs"].append(dict(run))
        payload["forecasts"].extend(rows)
        self._write(payload)
        return True

    def save_monitoring(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> bool:
        normalized = _validate_monitoring(run_id, rows)
        payload = self._read()
        if not any(run.get("run_id") == run_id for run in payload["runs"]):
            raise DataContractError("Monitoring references an unknown forecast run.")
        forecasts = {
            (row["sku"], int(row["horizon"])): row
            for row in payload["forecasts"]
            if row["run_id"] == run_id
        }
        for row in normalized:
            forecast = forecasts.get((row["sku"], row["horizon"]))
            if forecast is None:
                raise DataContractError("Monitoring references a forecast that does not exist.")
            for name in ("forecast_date", "prediction", "lower", "upper"):
                if row[name] != forecast[name]:
                    raise DataContractError("Monitoring does not match its persisted forecast.")
        existing = [row for row in payload["monitoring"] if row["run_id"] == run_id]
        existing_by_key = {(row["sku"], int(row["horizon"])): row for row in existing}
        changed = False
        for row in normalized:
            key = (row["sku"], row["horizon"])
            if key in existing_by_key:
                if existing_by_key[key] != row:
                    raise DataContractError("Monitoring key already exists with different content.")
                continue
            payload["monitoring"].append(row)
            changed = True
        if changed:
            self._write(payload)
        return changed

    def list_forecasts(
        self,
        *,
        run_id: str | None = None,
        sku: str | None = None,
        cutoff: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = _positive_limit(limit)
        rows = self._read()["forecasts"]
        if run_id is not None:
            rows = [row for row in rows if row["run_id"] == run_id]
        if sku is not None:
            rows = [row for row in rows if row["sku"] == sku]
        if cutoff is not None:
            rows = [row for row in rows if row["cutoff"] == cutoff]
        return [
            dict(row)
            for row in sorted(rows, key=lambda row: (row["forecast_date"], row["sku"]))[:limit]
        ]

    def list_monitoring(
        self, *, run_id: str | None = None, sku: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        limit = _positive_limit(limit)
        rows = self._read()["monitoring"]
        if run_id is not None:
            rows = [row for row in rows if row["run_id"] == run_id]
        if sku is not None:
            rows = [row for row in rows if row.get("sku") == sku]
        return [dict(row) for row in rows[:limit]]


class ImmutableJsonForecastRepository(JsonForecastRepository):
    """Load one reviewed snapshot in memory and reject every write attempt."""

    def __init__(self, path: str | Path):
        super().__init__(path)
        if not self.path.is_file():
            raise DataContractError(f"Immutable demo snapshot not found: {self.path}")
        self._snapshot = super()._read()

    def migrate(self) -> None:
        self._read()

    def _read(self) -> dict[str, Any]:
        return self._snapshot

    def _write(self, payload: Mapping[str, Any]) -> None:
        del payload
        raise DataContractError("Immutable demo repository does not permit writes.")

    def save_run(self, run: Mapping[str, Any], forecasts: Sequence[Mapping[str, Any]]) -> bool:
        del run, forecasts
        raise DataContractError("Immutable demo repository does not permit forecast writes.")

    def save_monitoring(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> bool:
        del run_id, rows
        raise DataContractError("Immutable demo repository does not permit monitoring writes.")


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_runs (
  run_id text PRIMARY KEY, cutoff date NOT NULL, model text NOT NULL,
  created_at timestamptz NOT NULL, metadata jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
  run_id text NOT NULL REFERENCES forecast_runs(run_id), sku text NOT NULL,
  horizon integer NOT NULL CHECK (horizon > 0), forecast_date date NOT NULL,
  prediction double precision NOT NULL CHECK (prediction >= 0),
  lower_bound double precision NOT NULL CHECK (lower_bound >= 0),
  upper_bound double precision NOT NULL,
  PRIMARY KEY (run_id, sku, horizon),
  CHECK (lower_bound <= prediction AND prediction <= upper_bound)
);
CREATE TABLE IF NOT EXISTS monitoring (
  run_id text NOT NULL REFERENCES forecast_runs(run_id), sku text NOT NULL,
  horizon integer NOT NULL, forecast_date date NOT NULL, actual double precision NOT NULL,
  prediction double precision NOT NULL, lower_bound double precision NOT NULL,
  upper_bound double precision NOT NULL, absolute_error double precision NOT NULL,
  covered boolean NOT NULL, PRIMARY KEY (run_id, sku, horizon)
);
CREATE INDEX IF NOT EXISTS forecasts_date_idx ON forecasts (forecast_date, sku);
CREATE INDEX IF NOT EXISTS monitoring_date_idx ON monitoring (forecast_date, sku);
"""


class PostgresForecastRepository:
    def __init__(self, dsn: str):
        if not dsn.strip():
            raise DataContractError("PostgreSQL DSN cannot be empty.")
        self.dsn = dsn

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL support requires psycopg[binary].") from exc
        return psycopg.connect(self.dsn)

    def migrate(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(POSTGRES_SCHEMA)

    def health(self) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok", "backend": "postgresql", "schema_version": SCHEMA_VERSION}

    def save_run(self, run: Mapping[str, Any], forecasts: Sequence[Mapping[str, Any]]) -> bool:
        run_id, rows = _validate_run(run, forecasts)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (run_id,))
            cursor.execute("SELECT metadata FROM forecast_runs WHERE run_id = %s", (run_id,))
            found = cursor.fetchone()
            if found is not None:
                cursor.execute(
                    "SELECT sku, horizon, forecast_date, prediction, lower_bound, upper_bound "
                    "FROM forecasts WHERE run_id = %s ORDER BY forecast_date, sku",
                    (run_id,),
                )
                existing_rows = cursor.fetchall()
                requested = [
                    (
                        r["sku"],
                        r["horizon"],
                        date.fromisoformat(r["forecast_date"]),
                        r["prediction"],
                        r["lower"],
                        r["upper"],
                    )
                    for r in sorted(rows, key=lambda r: (r["forecast_date"], r["sku"]))
                ]
                if found[0] != dict(run) or existing_rows != requested:
                    raise DataContractError("Run id already exists with different content.")
                return False
            cursor.execute(
                "INSERT INTO forecast_runs "
                "(run_id, cutoff, model, created_at, metadata) "
                "VALUES (%s, %s, %s, %s, %s)",
                (run_id, run["cutoff"], run["model"], run["created_at"], json.dumps(dict(run))),
            )
            cursor.executemany(
                "INSERT INTO forecasts "
                "(run_id, sku, horizon, forecast_date, prediction, lower_bound, upper_bound) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        run_id,
                        r["sku"],
                        r["horizon"],
                        r["forecast_date"],
                        r["prediction"],
                        r["lower"],
                        r["upper"],
                    )
                    for r in rows
                ],
            )
        return True

    def save_monitoring(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> bool:
        normalized = _validate_monitoring(run_id, rows)
        changed = False
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (run_id,))
            for row in normalized:
                cursor.execute(
                    "SELECT forecast_date, prediction, lower_bound, upper_bound "
                    "FROM forecasts WHERE run_id = %s AND sku = %s AND horizon = %s",
                    (run_id, row["sku"], row["horizon"]),
                )
                forecast = cursor.fetchone()
                expected = (
                    date.fromisoformat(row["forecast_date"]),
                    row["prediction"],
                    row["lower"],
                    row["upper"],
                )
                if forecast is None or forecast != expected:
                    raise DataContractError("Monitoring does not match a persisted forecast.")
                cursor.execute(
                    "SELECT actual, prediction, lower_bound, upper_bound, absolute_error, covered "
                    "FROM monitoring WHERE run_id = %s AND sku = %s AND horizon = %s",
                    (run_id, row["sku"], row["horizon"]),
                )
                existing = cursor.fetchone()
                values = (
                    row["actual"],
                    row["prediction"],
                    row["lower"],
                    row["upper"],
                    row["absolute_error"],
                    row["covered"],
                )
                if existing is not None:
                    if existing != values:
                        raise DataContractError(
                            "Monitoring key already exists with different content."
                        )
                    continue
                cursor.execute(
                    "INSERT INTO monitoring "
                    "(run_id, sku, horizon, forecast_date, actual, prediction, "
                    "lower_bound, upper_bound, absolute_error, covered) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        run_id,
                        row["sku"],
                        row["horizon"],
                        row["forecast_date"],
                        *values,
                    ),
                )
                changed = True
        return changed

    def list_forecasts(
        self,
        *,
        run_id: str | None = None,
        sku: str | None = None,
        cutoff: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = _positive_limit(limit)
        clauses, parameters = [], []
        if run_id is not None:
            clauses.append("f.run_id = %s")
            parameters.append(run_id)
        if sku is not None:
            clauses.append("f.sku = %s")
            parameters.append(sku)
        if cutoff is not None:
            clauses.append("r.cutoff = %s")
            parameters.append(cutoff)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT f.run_id, r.cutoff, f.forecast_date, f.sku, f.horizon, "
            "f.prediction, f.lower_bound, f.upper_bound, r.model "
            "FROM forecasts f JOIN forecast_runs r USING (run_id)"
            + where
            + " ORDER BY f.forecast_date, f.sku LIMIT %s"
        )
        parameters.append(limit)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            return [
                dict(
                    zip(
                        (
                            "run_id",
                            "cutoff",
                            "forecast_date",
                            "sku",
                            "horizon",
                            "prediction",
                            "lower",
                            "upper",
                            "model",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in cursor.fetchall()
            ]

    def list_monitoring(
        self, *, run_id: str | None = None, sku: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        limit = _positive_limit(limit)
        query = (
            "SELECT run_id, forecast_date, sku, horizon, actual, prediction, "
            "lower_bound, upper_bound, absolute_error, covered FROM monitoring"
        )
        parameters: list[Any] = []
        clauses: list[str] = []
        if run_id is not None:
            clauses.append("run_id = %s")
            parameters.append(run_id)
        if sku is not None:
            clauses.append("sku = %s")
            parameters.append(sku)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY forecast_date, sku LIMIT %s"
        parameters.append(limit)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            return [
                dict(
                    zip(
                        (
                            "run_id",
                            "forecast_date",
                            "sku",
                            "horizon",
                            "actual",
                            "prediction",
                            "lower",
                            "upper",
                            "absolute_error",
                            "covered",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in cursor.fetchall()
            ]
