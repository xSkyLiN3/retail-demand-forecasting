from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from retail_forecasting.backtesting import seasonal_naive_predictions
from retail_forecasting.dataset import DataContractError, validate_cohort_manifest
from retail_forecasting.intervals import IntervalCalibration, apply_intervals
from retail_forecasting.m2_workflow import (
    M2_CHAMPION,
    M2_DEVELOPMENT_FOLDS,
    M2_NOMINAL_COVERAGE,
    M2_REPLAY_WARMUP_FOLDS,
    M2_THRESHOLDS,
    M2EvidenceHashes,
    M2FrozenWorkflow,
    freeze_m2_workflow,
    prepare_holdout_evaluation,
)
from retail_forecasting.monitoring import AlertThresholds, monitor_forecasts

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_M1_SUMMARY = PROJECT_ROOT / "reports" / "m1" / "evidence" / "confirmation_summary.json"
M1_INTEGRITY_KEYS = {
    "baseline_predictions_sha256",
    "candidate_audit_sha256",
    "candidate_predictions_sha256",
    "canonical_comparison_sha256",
    "canonical_run_sha256",
    "receipt_sha256",
    "run_record_sha256",
}
M2_HASH_KEYS = {
    "cohort_manifest_sha256",
    "m1_confirmation_sha256",
    "panel_sha256",
    "source_tree_sha256",
}


def _json_default(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _json_text(payload: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _is_short_hex(value: object, *, length: int = 16) -> bool:
    if not isinstance(value, str) or len(value) != length or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_tree_sha256() -> str:
    files: list[Path] = []
    for directory, pattern in (
        (PROJECT_ROOT / "src", "*.py"),
        (PROJECT_ROOT / "tests", "*.py"),
        (PROJECT_ROOT / "requirements", "*.txt"),
    ):
        if directory.is_dir():
            files.extend(path for path in directory.rglob(pattern) if path.is_file())
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)

    digest = hashlib.sha256()

    def key(path: Path) -> str:
        return path.relative_to(PROJECT_ROOT).as_posix()

    for path in sorted(set(files), key=key):
        relative = key(path).encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError(f"{label} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise DataContractError(f"{label} must be a JSON object.")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_json_text(payload), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DataContractError(f"Exclusive M2 artifact already exists: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
        target.write(_json_text(payload))


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, date_format="%Y-%m-%d", lineterminator="\n")
    os.replace(temporary, path)


def _output_record(path: Path, root: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DataContractError("M2 output escaped its run directory.") from exc
    return {"path": relative, "sha256": _sha256_file(resolved)}


def _manifest_output_path(root: Path, record: object, *, label: str) -> Path:
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        raise DataContractError(f"{label} has an invalid output record.")
    path_value = record.get("path")
    expected_sha256 = record.get("sha256")
    if not isinstance(path_value, str) or not _is_sha256(expected_sha256):
        raise DataContractError(f"{label} has an invalid output reference.")
    relative = Path(path_value)
    if relative.is_absolute():
        raise DataContractError(f"{label} output path must be relative.")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise DataContractError(f"{label} output escaped its run directory.") from exc
    if not resolved.is_file() or _sha256_file(resolved) != expected_sha256:
        raise DataContractError(f"{label} output is missing or has been modified.")
    return resolved


def _verify_output_manifest(
    root: Path,
    outputs: object,
    *,
    expected_names: set[str],
    label: str,
) -> dict[str, Path]:
    if not isinstance(outputs, dict) or set(outputs) != expected_names:
        raise DataContractError(f"{label} output manifest is invalid.")
    return {
        name: _manifest_output_path(root, outputs[name], label=f"{label} {name}")
        for name in sorted(expected_names)
    }


def _validate_m1_summary(
    summary: Mapping[str, Any],
    *,
    panel: pd.DataFrame,
    cohort: Mapping[str, Any],
) -> None:
    if summary.get("artifact_schema_version") != 1:
        raise DataContractError("M1 confirmation summary schema is unsupported.")
    decision = summary.get("decision")
    if decision != {
        "champion": M2_CHAMPION,
        "promoted": False,
        "status": "candidate_rejected",
    }:
        raise DataContractError("M2 requires the frozen M1 baseline-champion decision.")
    if summary.get("panel_sha256") != cohort.get("panel_sha256"):
        raise DataContractError("M1 evidence and the prepared panel do not match.")

    if not _is_short_hex(summary.get("selection_id")) or not _is_short_hex(
        summary.get("validation_id")
    ):
        raise DataContractError("M1 selection or validation identity is invalid.")
    if not _is_sha256(summary.get("selection_sha256")):
        raise DataContractError("M1 selection digest is invalid.")
    integrity = summary.get("integrity")
    if (
        not isinstance(integrity, dict)
        or set(integrity) != M1_INTEGRITY_KEYS
        or not all(_is_sha256(value) for value in integrity.values())
    ):
        raise DataContractError("M1 confirmation integrity manifest is invalid.")

    window = summary.get("window")
    if not isinstance(window, dict) or window.get("final_holdout_status") != (
        "reserved_not_evaluated"
    ):
        raise DataContractError("M1 evidence does not preserve the final holdout.")
    if window.get("folds") != list(range(14, 20)):
        raise DataContractError("M1 confirmation fold manifest is invalid.")
    try:
        window_start = pd.Timestamp(window["start"]).normalize()
        window_end = pd.Timestamp(window["end"]).normalize()
    except (KeyError, TypeError, ValueError) as exc:
        raise DataContractError("M1 confirmation window is invalid.") from exc
    if pd.isna(window_start) or pd.isna(window_end) or (window_end - window_start).days != 83:
        raise DataContractError("M1 confirmation window must span exactly 84 days.")
    holdout_start = pd.Timestamp(panel["date"].max()).normalize() - pd.Timedelta(days=83)
    if window_end >= holdout_start:
        raise DataContractError("M1 confirmation window overlaps the final holdout.")

    folds = summary.get("folds")
    if (
        not isinstance(folds, list)
        or len(folds) != 6
        or [row.get("fold") for row in folds if isinstance(row, dict)] != list(range(14, 20))
    ):
        raise DataContractError("M1 confirmation fold evidence is incomplete.")
    audit = summary.get("audit")
    expected_rows = 6 * 14 * len(cohort.get("skus", []))
    if not isinstance(audit, dict) or audit != {
        "folds": 6,
        "raw_negative_predictions": 0,
        "rows": expected_rows,
        "training_cutoff_mismatches": 0,
    }:
        raise DataContractError("M1 confirmation audit is inconsistent with the cohort.")
    required_sections = ("baseline", "candidate", "criteria", "sku_evidence")
    if any(not isinstance(summary.get(name), dict) for name in required_sections):
        raise DataContractError("M1 confirmation decision evidence is incomplete.")


def _load_verified_inputs(
    panel_path: Path,
    cohort_path: Path,
    m1_summary_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if not panel_path.is_file():
        raise FileNotFoundError(f"Prepared panel not found: {panel_path}")
    panel = pd.read_csv(panel_path, parse_dates=["date"], dtype={"sku": "string"})
    cohort = validate_cohort_manifest(panel, panel_path, cohort_path)
    m1_summary = _read_json(m1_summary_path, label="M1 confirmation summary")
    _validate_m1_summary(m1_summary, panel=panel, cohort=cohort)
    return panel, cohort, m1_summary


def freeze_m2(
    panel_path: Path,
    cohort_path: Path,
    m1_summary_path: Path,
    report_dir: Path,
) -> Path:
    """Freeze interval and monitoring policy using development folds only."""

    panel, cohort, m1_summary = _load_verified_inputs(
        panel_path,
        cohort_path,
        m1_summary_path,
    )
    hashes = M2EvidenceHashes(
        panel_sha256=str(cohort["panel_sha256"]),
        cohort_manifest_sha256=_sha256_file(cohort_path),
        m1_confirmation_sha256=_sha256_file(m1_summary_path),
        source_tree_sha256=_source_tree_sha256(),
    )
    frozen = freeze_m2_workflow(
        panel,
        initial_train_days=int(cohort["selection"]["training_days"]),
        hashes=hashes,
        m1_champion=str(m1_summary["decision"]["champion"]),
    )
    freeze_id = str(frozen.contract["contract_sha256"])[:16]
    run_dir = report_dir / "m2" / "freeze" / freeze_id
    contract_path = run_dir / "contract.json"
    if contract_path.exists():
        existing = _read_json(contract_path, label="Existing M2 contract")
        if existing != frozen.contract:
            raise DataContractError(
                "Existing M2 freeze differs from the current deterministic run."
            )
        _verify_freeze_run(run_dir, expected_contract=frozen.contract, freeze_id=freeze_id)
        return contract_path

    calibration_path = run_dir / "calibration.json"
    replay_path = run_dir / "replay.json"
    predictions_path = run_dir / "development_predictions.csv"
    _write_json_atomic(calibration_path, frozen.calibration_contract)
    _write_json_atomic(
        replay_path,
        {"warmup_folds": 6, "windows": list(frozen.replay)},
    )
    _write_csv_atomic(predictions_path, frozen.development_predictions)
    _write_json_atomic(contract_path, frozen.contract)
    run_record: dict[str, Any] = {
        "artifact_schema_version": 1,
        "contract_sha256": frozen.contract["contract_sha256"],
        "id": freeze_id,
        "outputs": {
            "calibration": _output_record(calibration_path, run_dir),
            "contract": _output_record(contract_path, run_dir),
            "development_predictions": _output_record(predictions_path, run_dir),
            "replay": _output_record(replay_path, run_dir),
        },
        "phase": "m2_development_freeze",
    }
    run_record["record_sha256"] = _payload_sha256(run_record)
    _write_json_atomic(run_dir / "run.json", run_record)
    return contract_path


def _verify_freeze_run(
    run_dir: Path,
    *,
    expected_contract: Mapping[str, Any],
    freeze_id: str,
) -> None:
    run = _read_json(run_dir / "run.json", label="Existing M2 freeze run")
    record_sha256 = run.get("record_sha256")
    unsigned = {key: value for key, value in run.items() if key != "record_sha256"}
    if not _is_sha256(record_sha256) or record_sha256 != _payload_sha256(unsigned):
        raise DataContractError("Existing M2 freeze run record has been modified.")
    if (
        run.get("artifact_schema_version") != 1
        or run.get("phase") != "m2_development_freeze"
        or run.get("id") != freeze_id
        or run.get("contract_sha256") != expected_contract.get("contract_sha256")
    ):
        raise DataContractError("Existing M2 freeze run identity is inconsistent.")
    outputs = _verify_output_manifest(
        run_dir,
        run.get("outputs"),
        expected_names={"calibration", "contract", "development_predictions", "replay"},
        label="Existing M2 freeze",
    )
    persisted_contract = _read_json(outputs["contract"], label="Existing M2 contract output")
    if persisted_contract != expected_contract:
        raise DataContractError("Existing M2 freeze contract output is inconsistent.")


def _restore_frozen(contract: dict[str, Any]) -> M2FrozenWorkflow:
    if (
        contract.get("artifact_schema_version") != 1
        or contract.get("phase") != "m2_development_freeze"
        or contract.get("champion") != M2_CHAMPION
        or contract.get("nominal_coverage") != M2_NOMINAL_COVERAGE
        or contract.get("thresholds") != M2_THRESHOLDS
        or not _is_sha256(contract.get("development_predictions_sha256"))
    ):
        raise DataContractError("Frozen M2 contract was modified or its identity is invalid.")
    hashes = contract.get("hashes")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != M2_HASH_KEYS
        or not all(_is_sha256(value) for value in hashes.values())
    ):
        raise DataContractError("Frozen M2 evidence hashes are invalid.")
    calibration_payload = contract.get("interval_calibration")
    if not isinstance(calibration_payload, dict):
        raise DataContractError("Frozen M2 interval calibration is missing.")
    if contract.get("interval_calibration_sha256") != _payload_sha256(calibration_payload):
        raise DataContractError("Frozen M2 interval calibration has been modified.")
    calibration = IntervalCalibration.from_contract(calibration_payload)
    replay_payload = contract.get("replay")
    if not isinstance(replay_payload, dict) or not isinstance(replay_payload.get("windows"), list):
        raise DataContractError("Frozen M2 replay contract is missing.")
    if (
        replay_payload.get("warmup_folds") != M2_REPLAY_WARMUP_FOLDS
        or len(replay_payload["windows"]) != M2_DEVELOPMENT_FOLDS - M2_REPLAY_WARMUP_FOLDS
    ):
        raise DataContractError("Frozen M2 replay window count is invalid.")
    if contract.get("replay_sha256") != _payload_sha256(replay_payload):
        raise DataContractError("Frozen M2 replay contract has been modified.")
    return M2FrozenWorkflow(
        contract=contract,
        calibration=calibration,
        calibration_contract=calibration_payload,
        development_predictions=pd.DataFrame(),
        replay=tuple(replay_payload["windows"]),
    )


def _verify_frozen_inputs(
    contract: Mapping[str, Any],
    *,
    cohort_path: Path,
    m1_summary_path: Path,
    cohort: Mapping[str, Any],
) -> None:
    expected = contract.get("hashes")
    if not isinstance(expected, dict):
        raise DataContractError("Frozen M2 input hashes are missing.")
    current = {
        "cohort_manifest_sha256": _sha256_file(cohort_path),
        "m1_confirmation_sha256": _sha256_file(m1_summary_path),
        "panel_sha256": str(cohort["panel_sha256"]),
        "source_tree_sha256": _source_tree_sha256(),
    }
    if expected != current:
        raise DataContractError("Current code or evidence no longer matches the frozen M2 inputs.")


def load_verified_frozen(
    contract_path: Path,
    panel_path: Path,
    cohort_path: Path,
    m1_summary_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], M2FrozenWorkflow]:
    """Load a frozen M2 contract only when all bound code and evidence still match."""

    contract = _read_json(contract_path, label="Frozen M2 contract")
    frozen = _restore_frozen(contract)
    plan = prepare_holdout_evaluation(frozen)
    panel, cohort, _ = _load_verified_inputs(panel_path, cohort_path, m1_summary_path)
    _verify_frozen_inputs(
        contract,
        cohort_path=cohort_path,
        m1_summary_path=m1_summary_path,
        cohort=cohort,
    )
    if plan.panel_sha256 != cohort["panel_sha256"]:
        raise DataContractError("Frozen M2 contract and current panel do not match.")
    return panel, cohort, frozen


def _thresholds_from_contract(contract: Mapping[str, Any]) -> AlertThresholds:
    thresholds = contract.get("thresholds")
    if not isinstance(thresholds, dict):
        raise DataContractError("Frozen M2 thresholds are missing.")
    try:
        return AlertThresholds(**thresholds)
    except (TypeError, ValueError) as exc:
        raise DataContractError("Frozen M2 thresholds are invalid.") from exc


def _demo_snapshot(
    intervals: pd.DataFrame,
    *,
    contract_sha256: str,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    forecasts: list[dict[str, Any]] = []
    monitoring: list[dict[str, Any]] = []
    for fold, rows in intervals.groupby("fold", sort=True):
        cutoff = pd.Timestamp(rows["cutoff"].iloc[0]).date().isoformat()
        run_id = hashlib.sha256(
            f"{contract_sha256}|holdout|{int(fold)}|{cutoff}".encode()
        ).hexdigest()[:16]
        runs.append(
            {
                "created_at": f"{cutoff}T00:00:00+00:00",
                "cutoff": cutoff,
                "decision_use": "historical educational replay only",
                "model": M2_CHAMPION,
                "nominal_coverage": M2_NOMINAL_COVERAGE,
                "run_id": run_id,
                "source": "UCI Online Retail II, CC BY 4.0",
            }
        )
        for row in rows.itertuples(index=False):
            forecast_date = pd.Timestamp(row.date).date().isoformat()
            prediction = float(row.prediction)
            lower = float(row.lower)
            upper = float(row.upper)
            actual = float(row.actual)
            forecasts.append(
                {
                    "cutoff": cutoff,
                    "forecast_date": forecast_date,
                    "horizon": int(row.horizon),
                    "lower": lower,
                    "model": M2_CHAMPION,
                    "prediction": prediction,
                    "run_id": run_id,
                    "sku": str(row.sku),
                    "upper": upper,
                }
            )
            monitoring.append(
                {
                    "absolute_error": abs(prediction - actual),
                    "actual": actual,
                    "covered": lower <= actual <= upper,
                    "forecast_date": forecast_date,
                    "horizon": int(row.horizon),
                    "lower": lower,
                    "prediction": prediction,
                    "run_id": run_id,
                    "sku": str(row.sku),
                    "upper": upper,
                }
            )
    return {
        "forecasts": forecasts,
        "monitoring": monitoring,
        "runs": runs,
        "schema_version": 1,
    }


def _verify_receipt(
    receipt_path: Path,
    *,
    expected_identity: Mapping[str, Any],
    expected_evaluation_id: str,
    expected_run_path: Path,
) -> Path:
    receipt = _read_json(receipt_path, label="M2 holdout receipt")
    record_sha256 = receipt.get("record_sha256")
    unsigned = {key: value for key, value in receipt.items() if key != "record_sha256"}
    if not _is_sha256(record_sha256) or record_sha256 != _payload_sha256(unsigned):
        raise DataContractError("M2 holdout receipt has been modified.")
    if set(receipt) != {
        "artifact_schema_version",
        "contract_sha256",
        "evaluation_id",
        "panel_sha256",
        "record_sha256",
        "run_path",
        "run_sha256",
    }:
        raise DataContractError("M2 holdout receipt schema is invalid.")
    if (
        receipt.get("artifact_schema_version") != 1
        or receipt.get("contract_sha256") != expected_identity["contract_sha256"]
        or receipt.get("panel_sha256") != expected_identity["panel_sha256"]
        or receipt.get("evaluation_id") != expected_evaluation_id
    ):
        raise DataContractError("M2 holdout receipt does not match the frozen evaluation.")
    run_path_value = receipt.get("run_path")
    run_sha256 = receipt.get("run_sha256")
    if not isinstance(run_path_value, str) or not _is_sha256(run_sha256):
        raise DataContractError("M2 holdout receipt has an invalid run reference.")
    run_path = Path(run_path_value).resolve()
    if run_path != expected_run_path.resolve():
        raise DataContractError("M2 holdout receipt points outside its expected run.")
    if not run_path.is_file() or _sha256_file(run_path) != run_sha256:
        raise DataContractError("M2 holdout receipt points to missing or modified evidence.")
    run = _read_json(run_path, label="M2 holdout run")
    run_record_sha256 = run.get("record_sha256")
    run_unsigned = {key: value for key, value in run.items() if key != "record_sha256"}
    if not _is_sha256(run_record_sha256) or run_record_sha256 != _payload_sha256(run_unsigned):
        raise DataContractError("M2 holdout run record has been modified.")
    for key, value in expected_identity.items():
        if run.get(key) != value:
            raise DataContractError(f"M2 holdout run does not match {key}.")
    if (
        run.get("evaluation_id") != expected_evaluation_id
        or run.get("holdout_status") != "evaluated_once_no_retuning"
    ):
        raise DataContractError("M2 holdout run identity is inconsistent.")
    _verify_output_manifest(
        run_path.parent,
        run.get("outputs"),
        expected_names={"demo_snapshot", "evaluation", "monitoring", "predictions"},
        label="M2 holdout",
    )
    return run_path


def evaluate_holdout(
    contract_path: Path,
    panel_path: Path,
    cohort_path: Path,
    m1_summary_path: Path,
    report_dir: Path,
) -> Path:
    """Evaluate the frozen champion exactly once on the final 84 days."""

    panel, cohort, frozen = load_verified_frozen(
        contract_path,
        panel_path,
        cohort_path,
        m1_summary_path,
    )
    contract = frozen.contract
    plan = prepare_holdout_evaluation(frozen)

    identity = {
        "artifact_schema_version": 1,
        "contract_sha256": plan.contract_sha256,
        "panel_sha256": plan.panel_sha256,
        "phase": "m2_final_holdout",
        "source_tree_sha256": contract["hashes"]["source_tree_sha256"],
    }
    evaluation_id = _payload_sha256(identity)[:16]
    run_dir = report_dir / "m2" / "holdout" / evaluation_id
    run_path = run_dir / "run.json"

    registry = panel_path.parent / ".m2_evaluation"
    registry_key = str(cohort["panel_sha256"])
    receipt_path = registry / f"holdout_receipt_{registry_key}.json"
    if receipt_path.is_file():
        return _verify_receipt(
            receipt_path,
            expected_identity=identity,
            expected_evaluation_id=evaluation_id,
            expected_run_path=run_path,
        )
    claim_path = registry / f"holdout_claim_{registry_key}.json"
    if claim_path.exists():
        raise DataContractError(
            "The final holdout already has a claim; inspect it before any retry."
        )

    claim = {**identity, "evaluation_id": evaluation_id, "run_path": str(run_path.resolve())}
    claim["record_sha256"] = _payload_sha256(claim)
    _write_json_exclusive(claim_path, claim)

    predictions = seasonal_naive_predictions(panel, plan.folds)
    intervals = apply_intervals(predictions, panel, plan.interval_calibration)
    outcomes = predictions.loc[:, ["date", "sku", "actual"]]
    monitoring = monitor_forecasts(
        intervals,
        outcomes,
        nominal_coverage=M2_NOMINAL_COVERAGE,
        thresholds=_thresholds_from_contract(contract),
    )
    overall = monitoring["overall"]
    evaluation = {
        "alerts": monitoring["alerts"],
        "champion": M2_CHAMPION,
        "folds": [int(fold.index) for fold in plan.folds],
        "holdout_end": plan.folds[-1].test_end.date().isoformat(),
        "holdout_start": plan.folds[0].test_start.date().isoformat(),
        "interval_status": (
            "within_predeclared_guardrails"
            if not monitoring["alerts"]
            else "degraded_with_published_alerts"
        ),
        "nominal_coverage": M2_NOMINAL_COVERAGE,
        "overall": overall,
        "rows": int(len(intervals)),
    }

    predictions_path = run_dir / "holdout_predictions.csv"
    monitoring_path = run_dir / "monitoring.json"
    evaluation_path = run_dir / "evaluation.json"
    demo_path = run_dir / "demo_snapshot.json"
    _write_csv_atomic(predictions_path, intervals)
    _write_json_atomic(monitoring_path, monitoring)
    _write_json_atomic(evaluation_path, evaluation)
    _write_json_atomic(
        demo_path,
        _demo_snapshot(intervals, contract_sha256=plan.contract_sha256),
    )
    run_record = {
        **identity,
        "evaluation_id": evaluation_id,
        "holdout_status": "evaluated_once_no_retuning",
        "outputs": {
            "demo_snapshot": _output_record(demo_path, run_dir),
            "evaluation": _output_record(evaluation_path, run_dir),
            "monitoring": _output_record(monitoring_path, run_dir),
            "predictions": _output_record(predictions_path, run_dir),
        },
    }
    run_record["record_sha256"] = _payload_sha256(run_record)
    _write_json_atomic(run_path, run_record)
    receipt = {
        "artifact_schema_version": 1,
        "contract_sha256": plan.contract_sha256,
        "evaluation_id": evaluation_id,
        "panel_sha256": plan.panel_sha256,
        "run_path": str(run_path.resolve()),
        "run_sha256": _sha256_file(run_path),
    }
    receipt["record_sha256"] = _payload_sha256(receipt)
    _write_json_atomic(receipt_path, receipt)
    return run_path
