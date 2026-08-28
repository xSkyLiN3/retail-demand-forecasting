from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pandas as pd

from retail_forecasting.backtesting import validate_panel
from retail_forecasting.config import FORECAST_HORIZON_DAYS, FORECAST_SEASONALITY_DAYS
from retail_forecasting.dataset import DataContractError
from retail_forecasting.storage import ForecastRepository


def _calibration_contract(calibration: Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(calibration, Mapping):
        return calibration
    contract = getattr(calibration, "contract", None)
    if not callable(contract):
        raise DataContractError("M2 calibration must be a mapping or expose contract().")
    result = contract()
    if not isinstance(result, Mapping):
        raise DataContractError("M2 calibration contract must be a mapping.")
    return result


def _calibration_offsets(
    calibration: Mapping[str, Any] | object, horizon_days: int
) -> tuple[Mapping[str, Any], dict[int, tuple[float, float, float, float]]]:
    calibration = _calibration_contract(calibration)
    coverage = calibration.get("nominal_coverage")
    if (
        not isinstance(coverage, (int, float))
        or isinstance(coverage, bool)
        or not 0 < float(coverage) < 1
    ):
        raise DataContractError("Calibration requires nominal_coverage between zero and one.")
    values = calibration.get("values")
    raw_widths = calibration.get("absolute_residual_quantile_by_horizon")
    if values is not None:
        if not isinstance(values, list):
            raise DataContractError("M2 calibration values must be a list.")
        raw = {int(row["horizon"]): row for row in values if isinstance(row, Mapping)}
    elif isinstance(raw_widths, Mapping):
        raw = {
            int(key): {
                "scaled_lower": -float(value),
                "scaled_upper": float(value),
                "raw_lower": -float(value),
                "raw_upper": float(value),
            }
            for key, value in raw_widths.items()
        }
    else:
        raise DataContractError("Calibration requires frozen M2 horizon quantiles.")
    offsets: dict[int, tuple[float, float, float, float]] = {}
    for horizon in range(1, horizon_days + 1):
        row = raw.get(horizon)
        if not isinstance(row, Mapping):
            raise DataContractError(f"Calibration quantiles for horizon {horizon} are absent.")
        try:
            quantiles = tuple(
                float(row[name])
                for name in ("scaled_lower", "scaled_upper", "raw_lower", "raw_upper")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataContractError(
                f"Calibration quantiles for horizon {horizon} are invalid."
            ) from exc
        if not all(math.isfinite(value) for value in quantiles):
            raise DataContractError(f"Calibration quantiles for horizon {horizon} are invalid.")
        if quantiles[0] > 0 or quantiles[1] < 0 or quantiles[2] > 0 or quantiles[3] < 0:
            raise DataContractError("M2 intervals must be anchored at the point forecast.")
        offsets[horizon] = quantiles
    if len(raw) != horizon_days:
        raise DataContractError("Calibration must contain exactly one record per horizon.")
    return calibration, offsets


def run_champion_batch(
    panel: pd.DataFrame,
    *,
    cutoff: str | pd.Timestamp,
    calibration: Mapping[str, Any] | object,
    repository: ForecastRepository,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    seasonality_days: int = FORECAST_SEASONALITY_DAYS,
) -> dict[str, Any]:
    """Persist one deterministic, idempotent seasonal-naive forecast from data known at cutoff."""
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days < 1:
        raise DataContractError("horizon_days must be a positive integer.")
    if (
        isinstance(seasonality_days, bool)
        or not isinstance(seasonality_days, int)
        or seasonality_days < 1
    ):
        raise DataContractError("seasonality_days must be a positive integer.")
    validated = validate_panel(panel)
    origin = pd.Timestamp(cutoff).normalize()
    if pd.isna(origin):
        raise DataContractError("cutoff must be a valid date.")
    known = validated.loc[validated["date"] <= origin].copy()
    if known.empty or known["date"].max() != origin:
        raise DataContractError("Panel must contain a complete observation grid at cutoff.")
    contract, offsets = _calibration_offsets(calibration, horizon_days)
    calibrated_seasonality = contract.get("seasonality_days", seasonality_days)
    if calibrated_seasonality != seasonality_days:
        raise DataContractError("Batch seasonality differs from the frozen M2 calibration.")
    model = f"seasonal_naive_{seasonality_days}d"
    records: list[dict[str, Any]] = []
    for sku, history in known.groupby("sku", sort=True):
        pattern = history.sort_values("date").tail(seasonality_days)["units"].astype(float).tolist()
        if len(pattern) != seasonality_days:
            raise DataContractError(f"SKU {sku} has insufficient history at cutoff.")
        history_values = history.sort_values("date")["units"].astype(float)
        differences = (
            history_values.iloc[seasonality_days:].to_numpy()
            - history_values.iloc[:-seasonality_days].to_numpy()
        )
        scale = float(abs(differences).mean()) if len(differences) else float("nan")
        for horizon in range(1, horizon_days + 1):
            prediction = max(0.0, pattern[(horizon - 1) % seasonality_days])
            scaled_lower, scaled_upper, raw_lower, raw_upper = offsets[horizon]
            lower_offset = scale * scaled_lower if math.isfinite(scale) and scale > 0 else raw_lower
            upper_offset = scale * scaled_upper if math.isfinite(scale) and scale > 0 else raw_upper
            records.append(
                {
                    "cutoff": origin.date().isoformat(),
                    "forecast_date": (origin + timedelta(days=horizon)).date().isoformat(),
                    "sku": str(sku),
                    "horizon": horizon,
                    "prediction": prediction,
                    "lower": max(0.0, prediction + lower_offset),
                    "upper": max(prediction, prediction + upper_offset),
                    "model": model,
                }
            )
    fingerprint = hashlib.sha256()
    fingerprint.update(known.to_csv(index=False, lineterminator="\n").encode())
    fingerprint.update(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode())
    fingerprint.update(f"{origin.date()}|{model}|{horizon_days}".encode())
    run_id = fingerprint.hexdigest()[:16]
    created_at = f"{origin.date().isoformat()}T00:00:00+00:00"
    run = {
        "run_id": run_id,
        "cutoff": origin.date().isoformat(),
        "model": model,
        "created_at": created_at,
        "nominal_coverage": float(contract["nominal_coverage"]),
        "source": "historical educational retail panel",
        "decision_use": "demonstration only; not validated for purchasing decisions",
    }
    for record in records:
        record["run_id"] = run_id
    created = repository.save_run(run, records)
    return {
        "run_id": run_id,
        "created": created,
        "forecast_rows": len(records),
        "cutoff": run["cutoff"],
        "model": model,
    }


def reconcile_known_outcomes(
    repository: ForecastRepository,
    *,
    run_id: str,
    outcomes: pd.DataFrame,
) -> dict[str, Any]:
    """Persist monitoring only for stored forecasts whose outcomes are now known."""
    actual_column = "actual" if "actual" in outcomes.columns else "units"
    required = {"date", "sku", actual_column}
    missing = required.difference(outcomes.columns)
    if missing:
        raise DataContractError(f"Outcome columns missing: {sorted(missing)}")
    known = outcomes.loc[:, ["date", "sku", actual_column]].rename(
        columns={actual_column: "actual"}
    )
    known["date"] = pd.to_datetime(known["date"], errors="coerce", format="mixed").dt.normalize()
    known["sku"] = known["sku"].astype(str).str.strip()
    known["actual"] = pd.to_numeric(known["actual"], errors="coerce")
    if (
        known.empty
        or known.isna().any().any()
        or known.duplicated(["date", "sku"]).any()
        or not known["actual"].map(lambda value: math.isfinite(float(value))).all()
        or (known["actual"] < 0).any()
    ):
        raise DataContractError("Outcomes must be finite, non-negative and unique by date/SKU.")
    forecasts = repository.list_forecasts(run_id=run_id, limit=5_000)
    if not forecasts:
        raise DataContractError("No persisted forecasts exist for the requested run.")
    actual_by_key = {
        (row.date.date().isoformat(), str(row.sku)): float(row.actual)
        for row in known.itertuples(index=False)
    }
    monitoring: list[dict[str, Any]] = []
    for forecast in forecasts:
        key = (str(forecast["forecast_date"]), forecast["sku"])
        if key not in actual_by_key:
            continue
        actual = actual_by_key[key]
        prediction = float(forecast["prediction"])
        lower = float(forecast["lower"])
        upper = float(forecast["upper"])
        monitoring.append(
            {
                "run_id": run_id,
                "forecast_date": str(forecast["forecast_date"]),
                "sku": forecast["sku"],
                "horizon": int(forecast["horizon"]),
                "actual": actual,
                "prediction": prediction,
                "lower": lower,
                "upper": upper,
                "absolute_error": abs(actual - prediction),
                "covered": lower <= actual <= upper,
            }
        )
    if not monitoring:
        return {"run_id": run_id, "created": False, "monitoring_rows": 0}
    created = repository.save_monitoring(run_id, monitoring)
    return {"run_id": run_id, "created": created, "monitoring_rows": len(monitoring)}
