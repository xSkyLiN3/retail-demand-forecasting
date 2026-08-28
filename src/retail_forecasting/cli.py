from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from retail_forecasting import __version__
from retail_forecasting.backtesting import BacktestFold, make_folds, seasonal_naive_predictions
from retail_forecasting.comparison import compare_candidate_to_baseline
from retail_forecasting.config import (
    DEFAULT_PROCESSED_DIR,
    DEFAULT_RAW_DIR,
    DEFAULT_REPORT_DIR,
    FINAL_HOLDOUT_DAYS,
    FORECAST_HORIZON_DAYS,
    FORECAST_SEASONALITY_DAYS,
    M1_RANDOM_SEED,
    SOURCE_SHA256,
    SOURCE_WORKBOOK_NAME,
    SOURCE_WORKBOOK_SHA256,
)
from retail_forecasting.dataset import (
    DataContractError,
    load_workbook,
    prepare_daily_panel,
    validate_cohort_manifest,
    write_prepared_dataset,
)
from retail_forecasting.features import build_supervised_table, feature_contract
from retail_forecasting.metrics import summarize_predictions
from retail_forecasting.modeling import (
    MODEL_CONFIG_GRID,
    ModelConfig,
    fit_predict_folds,
    model_grid_contract,
    partition_development_folds,
)
from retail_forecasting.source import (
    download_archive,
    extract_workbook,
    sha256_file,
    verify_sha256,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retail-forecast",
        description="Prepare and evaluate the retail demand forecasting project.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download and verify the UCI source.")
    download.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)

    prepare = subparsers.add_parser("prepare", help="Build the training-only SKU cohort and panel.")
    prepare.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_RAW_DIR / SOURCE_WORKBOOK_NAME,
    )
    prepare.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)

    baseline = subparsers.add_parser("baseline", help="Run rolling-origin seasonal naive.")
    baseline.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "daily_demand.csv",
    )
    baseline.add_argument(
        "--cohort",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "cohort.json",
    )
    baseline.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    tune = subparsers.add_parser(
        "tune-model",
        help="Select one frozen M1 model using development folds 0-13.",
    )
    tune.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "daily_demand.csv",
    )
    tune.add_argument(
        "--cohort",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "cohort.json",
    )
    tune.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    validate = subparsers.add_parser(
        "validate-model",
        help="Confirm the frozen M1 selection once on development folds 14-19.",
    )
    validate.add_argument("--selection", type=Path, required=True)
    validate.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "daily_demand.csv",
    )
    validate.add_argument(
        "--cohort",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "cohort.json",
    )
    validate.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    freeze_m2 = subparsers.add_parser(
        "freeze-m2",
        help="Freeze 90%% intervals and monitoring using development folds only.",
    )
    freeze_m2.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "daily_demand.csv",
    )
    freeze_m2.add_argument(
        "--cohort",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "cohort.json",
    )
    freeze_m2.add_argument(
        "--m1-summary",
        type=Path,
        default=PROJECT_ROOT / "reports" / "m1" / "evidence" / "confirmation_summary.json",
    )
    freeze_m2.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    holdout = subparsers.add_parser(
        "evaluate-holdout",
        help="Evaluate a frozen M2 contract once on the final 84-day holdout.",
    )
    holdout.add_argument("--contract", type=Path, required=True)
    holdout.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "daily_demand.csv",
    )
    holdout.add_argument(
        "--cohort",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "cohort.json",
    )
    holdout.add_argument(
        "--m1-summary",
        type=Path,
        default=PROJECT_ROOT / "reports" / "m1" / "evidence" / "confirmation_summary.json",
    )
    holdout.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    batch = subparsers.add_parser(
        "run-batch",
        help="Persist an idempotent champion forecast from an as-of cutoff.",
    )
    batch.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "daily_demand.csv",
    )
    batch.add_argument(
        "--cohort",
        type=Path,
        default=DEFAULT_PROCESSED_DIR / "cohort.json",
    )
    batch.add_argument(
        "--m1-summary",
        type=Path,
        default=PROJECT_ROOT / "reports" / "m1" / "evidence" / "confirmation_summary.json",
    )
    batch.add_argument("--contract", type=Path, required=True)
    batch.add_argument("--cutoff", required=True)
    batch_storage = batch.add_mutually_exclusive_group(required=True)
    batch_storage.add_argument("--demo-repository", type=Path)
    batch_storage.add_argument("--database-url")

    reconcile = subparsers.add_parser(
        "reconcile",
        help="Persist monitoring for known outcomes of a forecast run.",
    )
    reconcile.add_argument("--outcomes", type=Path, required=True)
    reconcile.add_argument("--run-id", required=True)
    reconcile_storage = reconcile.add_mutually_exclusive_group(required=True)
    reconcile_storage.add_argument("--demo-repository", type=Path)
    reconcile_storage.add_argument("--database-url")
    return parser


def _download(raw_dir: Path) -> int:
    archive = download_archive(raw_dir)
    workbook = extract_workbook(archive, raw_dir)
    verify_sha256(workbook, SOURCE_WORKBOOK_SHA256)
    print(f"Archive: {archive} ({sha256_file(archive)})")
    print(f"Workbook: {workbook} ({sha256_file(workbook)})")
    return 0


def _prepare(workbook: Path, processed_dir: Path) -> int:
    verify_sha256(workbook, SOURCE_WORKBOOK_SHA256)
    loaded = load_workbook(workbook)
    prepared = prepare_daily_panel(
        loaded.transactions,
        source_metadata={
            "archive_sha256": SOURCE_SHA256,
            "workbook_audit": loaded.audit,
            "workbook_bytes": workbook.stat().st_size,
            "workbook_filename": SOURCE_WORKBOOK_NAME,
            "workbook_sha256": SOURCE_WORKBOOK_SHA256,
        },
    )
    panel, cohort, quality = write_prepared_dataset(prepared, processed_dir)
    print(f"Panel: {panel} ({len(prepared.panel)} rows)")
    print(f"Cohort: {cohort} ({len(prepared.cohort)} SKUs)")
    print(
        "Cross-sheet copies removed: "
        f"{loaded.audit['cross_sheet_exact_rows_removed']} "
        f"({loaded.audit['cross_sheet_overlap_start']} to "
        f"{loaded.audit['cross_sheet_overlap_end']})"
    )
    print(f"Quality: {quality}")
    return 0


def _baseline(panel_path: Path, cohort_path: Path, report_dir: Path) -> int:
    if not panel_path.is_file():
        raise FileNotFoundError(f"Prepared panel not found: {panel_path}")
    panel = pd.read_csv(panel_path, parse_dates=["date"], dtype={"sku": "string"})
    cohort = validate_cohort_manifest(panel, panel_path, cohort_path)
    initial_train_days = cohort["selection"]["training_days"]
    folds = make_folds(panel, initial_train_days=initial_train_days)
    cohort_cutoff = pd.Timestamp(cohort["cutoff_exclusive"])
    if folds[0].test_start != cohort_cutoff:
        raise DataContractError(
            "First development fold does not align with the frozen cohort cutoff."
        )
    predictions = seasonal_naive_predictions(panel, folds)
    summary = summarize_predictions(predictions, panel)

    panel_dates = pd.to_datetime(panel["date"], format="mixed")
    panel_start = pd.Timestamp(panel_dates.min())
    panel_end = pd.Timestamp(panel_dates.max())
    holdout_start = panel_end - timedelta(days=FINAL_HOLDOUT_DAYS - 1)
    fold_records = [
        {
            "cutoff": fold.cutoff.date().isoformat(),
            "fold": fold.index,
            "test_end": fold.test_end.date().isoformat(),
            "test_start": fold.test_start.date().isoformat(),
        }
        for fold in folds
    ]
    folds_payload: dict[str, object] = {"folds": fold_records}
    folds_bytes = _json_text(folds_payload).encode("utf-8")
    run_contract = {
        "artifact_schema_version": 1,
        "cohort_manifest_sha256": sha256_file(cohort_path),
        "cohort_selection": cohort.get("selection"),
        "cohort_skus": cohort["skus"],
        "cohort_cutoff_exclusive": cohort["cutoff_exclusive"],
        "configuration": {
            "final_holdout_days": FINAL_HOLDOUT_DAYS,
            "horizon_days": FORECAST_HORIZON_DAYS,
            "seasonality_days": FORECAST_SEASONALITY_DAYS,
        },
        "development": {
            "first_cutoff": folds[0].cutoff.date().isoformat(),
            "fold_count": len(folds),
            "fold_manifest_sha256": hashlib.sha256(folds_bytes).hexdigest(),
            "last_test_date": folds[-1].test_end.date().isoformat(),
        },
        "environment": {
            "packages": _dependency_versions(),
            "project_version": __version__,
            "python": platform.python_version(),
        },
        "final_holdout": {
            "days": FINAL_HOLDOUT_DAYS,
            "end": panel_end.date().isoformat(),
            "start": holdout_start.date().isoformat(),
            "status": "reserved_not_evaluated",
        },
        "model": {
            "name": f"seasonal_naive_{FORECAST_SEASONALITY_DAYS}d",
            "type": "development_baseline",
        },
        "panel_range": {
            "end": panel_end.date().isoformat(),
            "start": panel_start.date().isoformat(),
        },
        "panel_sha256": cohort["panel_sha256"],
        "repository": _git_metadata(PROJECT_ROOT),
        "source_tree_sha256": _source_tree_sha256(PROJECT_ROOT),
        "source": cohort.get("source"),
        "target": cohort["target"],
    }
    run_id = hashlib.sha256(
        json.dumps(run_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    run_dir = report_dir / "baseline" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = run_dir / "predictions.csv"
    folds_path = run_dir / "folds.json"
    report_path = run_dir / "metrics.json"
    run_path = run_dir / "run.json"
    _write_csv_atomic(predictions_path, predictions)
    _write_json_atomic(folds_path, folds_payload)
    run_record = {"id": run_id, **run_contract}
    summary["run"] = run_record
    _write_json_atomic(report_path, summary)
    run_record["outputs"] = {
        "folds_sha256": sha256_file(folds_path),
        "metrics_sha256": sha256_file(report_path),
        "predictions_sha256": sha256_file(predictions_path),
    }
    _write_json_atomic(run_path, run_record)
    print(f"Folds: {len(folds)}")
    print(f"Run: {run_id}")
    print(f"Predictions: {predictions_path}")
    print(f"Fold manifest: {folds_path}")
    print(f"Metrics: {report_path}")
    return 0


def _read_verified_m1_inputs(
    panel_path: Path,
    cohort_path: Path,
) -> tuple[
    pd.DataFrame,
    dict[str, Any],
    tuple[BacktestFold, ...],
    tuple[BacktestFold, ...],
]:
    if not panel_path.is_file():
        raise FileNotFoundError(f"Prepared panel not found: {panel_path}")
    panel = pd.read_csv(panel_path, parse_dates=["date"], dtype={"sku": "string"})
    cohort = validate_cohort_manifest(panel, panel_path, cohort_path)
    folds = make_folds(
        panel,
        initial_train_days=int(cohort["selection"]["training_days"]),
    )
    cohort_cutoff = pd.Timestamp(cohort["cutoff_exclusive"])
    if folds[0].test_start != cohort_cutoff:
        raise DataContractError(
            "First development fold does not align with the frozen cohort cutoff."
        )
    tuning_folds, confirmation_folds = partition_development_folds(folds)
    return panel, cohort, tuning_folds, confirmation_folds


def _fold_payload(folds: tuple[BacktestFold, ...]) -> list[dict[str, object]]:
    return [
        {
            "cutoff": fold.cutoff.date().isoformat(),
            "fold": int(fold.index),
            "test_end": fold.test_end.date().isoformat(),
            "test_start": fold.test_start.date().isoformat(),
        }
        for fold in folds
    ]


def _partition_payload(
    panel: pd.DataFrame,
    tuning_folds: tuple[BacktestFold, ...],
    confirmation_folds: tuple[BacktestFold, ...],
) -> dict[str, object]:
    panel_dates = pd.to_datetime(panel["date"], format="mixed")
    panel_end = pd.Timestamp(panel_dates.max())
    holdout_start = panel_end - timedelta(days=FINAL_HOLDOUT_DAYS - 1)
    gap_days = (holdout_start - confirmation_folds[-1].test_end).days - 1
    return {
        "confirmation": _fold_payload(confirmation_folds),
        "development_gap_after_confirmation_days": int(gap_days),
        "final_holdout": {
            "days": FINAL_HOLDOUT_DAYS,
            "end": panel_end.date().isoformat(),
            "start": holdout_start.date().isoformat(),
            "status": "reserved_not_evaluated",
        },
        "tuning": _fold_payload(tuning_folds),
    }


def _model_config_payload(config: ModelConfig) -> dict[str, object]:
    return {"name": config.name, **config.estimator_parameters()}


def _payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_text(payload).encode("utf-8")).hexdigest()


def _audit_records(audit: pd.DataFrame) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for source_record in audit.to_dict(orient="records"):
        record: dict[str, object] = {}
        for key, value in source_record.items():
            if isinstance(value, pd.Timestamp):
                record[str(key)] = value.date().isoformat()
            elif isinstance(value, (np.integer, np.floating)):
                record[str(key)] = value.item()
            else:
                record[str(key)] = value
        records.append(record)
    return records


def _selection_rule_payload() -> dict[str, object]:
    return {
        "primary": "minimum aggregate WAPE on tuning folds 0-13",
        "tie_breakers": [
            "minimum absolute normalized bias",
            "minimum max_leaf_nodes",
            "maximum min_samples_leaf",
            "configuration name",
        ],
    }


def _selection_sort_key(
    config: ModelConfig,
    summary: dict[str, object],
) -> tuple[float, float, int, int, str]:
    overall = summary["overall"]
    wape = overall["wape"]
    bias = overall["normalized_bias"]
    return (
        float(wape) if wape is not None else math.inf,
        abs(float(bias)) if bias is not None else math.inf,
        int(config.max_leaf_nodes),
        -int(config.min_samples_leaf),
        config.name,
    )


def _tune_model(panel_path: Path, cohort_path: Path, report_dir: Path) -> int:
    panel, cohort, tuning_folds, confirmation_folds = _read_verified_m1_inputs(
        panel_path,
        cohort_path,
    )
    feature_payload = feature_contract()
    grid_payload = model_grid_contract()
    partition = _partition_payload(panel, tuning_folds, confirmation_folds)
    supervised = build_supervised_table(
        panel,
        max_target_date=tuning_folds[-1].test_end,
    )
    baseline_predictions = seasonal_naive_predictions(panel, tuning_folds)
    baseline_summary = summarize_predictions(baseline_predictions, panel)

    candidate_results: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]] = {}
    candidate_records: list[dict[str, object]] = []
    for config in MODEL_CONFIG_GRID:
        fitted = fit_predict_folds(supervised, tuning_folds, config)
        summary = summarize_predictions(fitted.predictions, panel)
        candidate_results[config.name] = (fitted.predictions, fitted.audit, summary)
        candidate_records.append(
            {
                "config": _model_config_payload(config),
                "fit_audit": _audit_records(fitted.audit),
                "metrics": summary,
            }
        )

    selected_config = min(
        MODEL_CONFIG_GRID,
        key=lambda config: _selection_sort_key(
            config,
            candidate_results[config.name][2],
        ),
    )
    selected_predictions, selected_audit, selected_summary = candidate_results[selected_config.name]
    if selected_summary["overall"]["wape"] is None:
        raise DataContractError("No M1 configuration has an evaluable tuning WAPE.")

    environment = {
        "packages": _dependency_versions(),
        "project_version": __version__,
        "python": platform.python_version(),
    }
    selection_contract: dict[str, Any] = {
        "confirmation_status": "not_run",
        "environment": environment,
        "feature_contract": feature_payload,
        "feature_contract_sha256": _payload_sha256(feature_payload),
        "inputs": {
            "cohort_manifest_sha256": sha256_file(cohort_path),
            "cohort_skus": cohort["skus"],
            "panel_sha256": cohort["panel_sha256"],
            "source": cohort.get("source"),
            "target": cohort["target"],
        },
        "model_grid": grid_payload,
        "model_grid_sha256": _payload_sha256(grid_payload),
        "partition": partition,
        "phase": "m1_tuning_selection",
        "random_seed": M1_RANDOM_SEED,
        "repository": _git_metadata(PROJECT_ROOT),
        "selected_config": _model_config_payload(selected_config),
        "selection_rule": _selection_rule_payload(),
        "source_tree_sha256": _source_tree_sha256(PROJECT_ROOT),
        "tuning_result": selected_summary,
    }
    tuning_run_id = _payload_sha256(selection_contract)[:16]
    run_dir = report_dir / "m1" / "tuning" / tuning_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    feature_path = run_dir / "feature_contract.json"
    grid_path = run_dir / "model_grid.json"
    search_path = run_dir / "search.json"
    selected_predictions_path = run_dir / "selected_tuning_predictions.csv"
    baseline_predictions_path = run_dir / "baseline_tuning_predictions.csv"
    audit_path = run_dir / "selected_tuning_audit.csv"
    selection_path = run_dir / "selection.json"
    search_payload: dict[str, Any] = {
        "artifact_schema_version": 1,
        "baseline": baseline_summary,
        "candidates": candidate_records,
        "selection_rule": _selection_rule_payload(),
        "selected_config": selected_config.name,
        "tuning_run_id": tuning_run_id,
    }
    _write_json_atomic(feature_path, feature_payload)
    _write_json_atomic(grid_path, grid_payload)
    _write_json_atomic(search_path, search_payload)
    _write_csv_atomic(selected_predictions_path, selected_predictions)
    _write_csv_atomic(baseline_predictions_path, baseline_predictions)
    _write_csv_atomic(audit_path, selected_audit)
    outputs = {
        "baseline_tuning_predictions": _output_record(
            baseline_predictions_path,
            run_dir,
        ),
        "feature_contract": _output_record(feature_path, run_dir),
        "model_grid": _output_record(grid_path, run_dir),
        "search": _output_record(search_path, run_dir),
        "selected_tuning_audit": _output_record(audit_path, run_dir),
        "selected_tuning_predictions": _output_record(
            selected_predictions_path,
            run_dir,
        ),
    }
    selection_contract["outputs"] = outputs
    selection_id = _payload_sha256(selection_contract)[:16]
    selection_record: dict[str, Any] = {
        "artifact_schema_version": 1,
        "confirmation_status": "not_run",
        "id": selection_id,
        "outputs": outputs,
        "selection_contract": selection_contract,
    }
    _write_json_atomic(selection_path, selection_record)

    print(f"Tuning folds: {len(tuning_folds)} (0-13)")
    print(f"Confirmation folds reserved: {len(confirmation_folds)} (14-19)")
    print(f"Selected configuration: {selected_config.name}")
    print(f"Tuning WAPE: {selected_summary['overall']['wape']:.6f}")
    print(f"Selection ID: {selection_id}")
    print(f"Selection: {selection_path}")
    return 0


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise DataContractError(f"{label} must contain one JSON object.")
    return payload


def _output_record(path: Path, root: Path) -> dict[str, str]:
    return {
        "file": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _verify_output_records(root: Path, outputs: object) -> None:
    if not isinstance(outputs, dict) or not outputs:
        raise DataContractError("Artifact output manifest is missing or invalid.")
    resolved_root = root.resolve()
    for name, value in outputs.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise DataContractError("Artifact output manifest contains an invalid record.")
        filename = value.get("file")
        expected_hash = value.get("sha256")
        if not isinstance(filename, str) or not isinstance(expected_hash, str):
            raise DataContractError(f"Artifact output record is invalid: {name}")
        path = (root / filename).resolve()
        if path.parent != resolved_root or not path.is_file():
            raise DataContractError(f"Artifact output is missing or outside its run: {name}")
        if sha256_file(path) != expected_hash:
            raise DataContractError(f"Artifact output hash mismatch: {name}")


def _manifest_output_path(root: Path, outputs: dict[str, Any], name: str) -> Path:
    record = outputs.get(name)
    if not isinstance(record, dict) or not isinstance(record.get("file"), str):
        raise DataContractError(f"Artifact output record is missing: {name}")
    path = (root / record["file"]).resolve()
    if path.parent != root.resolve():
        raise DataContractError(f"Artifact output is outside its run: {name}")
    return path


def _verify_frozen_selection(
    selection_path: Path,
    panel_path: Path,
    cohort_path: Path,
) -> tuple[
    dict[str, Any],
    pd.DataFrame,
    dict[str, Any],
    tuple[BacktestFold, ...],
    ModelConfig,
]:
    selection = _read_json_object(selection_path, label="M1 selection")
    if selection.get("artifact_schema_version") != 1:
        raise DataContractError("M1 selection has an unsupported artifact schema.")
    if selection.get("confirmation_status") != "not_run":
        raise DataContractError("M1 selection is not frozen in the pre-confirmation state.")
    contract = selection.get("selection_contract")
    if not isinstance(contract, dict):
        raise DataContractError("M1 selection contract is missing or invalid.")
    if contract.get("confirmation_status") != "not_run":
        raise DataContractError("M1 selection contract is not in the pre-confirmation state.")
    selection_id = selection.get("id")
    if not isinstance(selection_id, str) or selection_id != _payload_sha256(contract)[:16]:
        raise DataContractError("M1 selection identifier does not match its frozen contract.")
    outputs = selection.get("outputs")
    required_outputs = {
        "baseline_tuning_predictions",
        "feature_contract",
        "model_grid",
        "search",
        "selected_tuning_audit",
        "selected_tuning_predictions",
    }
    if not isinstance(outputs, dict) or set(outputs) != required_outputs:
        raise DataContractError("M1 selection output manifest is incomplete or unexpected.")
    if contract.get("outputs") != outputs:
        raise DataContractError("M1 selection outputs are not bound to its frozen contract.")
    _verify_output_records(selection_path.parent, outputs)

    panel, cohort, tuning_folds, confirmation_folds = _read_verified_m1_inputs(
        panel_path,
        cohort_path,
    )
    expected_partition = _partition_payload(panel, tuning_folds, confirmation_folds)
    current_features = feature_contract()
    current_grid = model_grid_contract()
    persisted_features = _read_json_object(
        _manifest_output_path(selection_path.parent, outputs, "feature_contract"),
        label="Persisted M1 feature contract",
    )
    persisted_grid = _read_json_object(
        _manifest_output_path(selection_path.parent, outputs, "model_grid"),
        label="Persisted M1 model grid",
    )
    expected_inputs = contract.get("inputs")
    if not isinstance(expected_inputs, dict):
        raise DataContractError("M1 selection input contract is missing.")
    if expected_inputs.get("panel_sha256") != cohort["panel_sha256"]:
        raise DataContractError("Current panel does not match the frozen M1 selection.")
    if expected_inputs.get("cohort_manifest_sha256") != sha256_file(cohort_path):
        raise DataContractError("Current cohort manifest does not match the frozen M1 selection.")
    if contract.get("partition") != expected_partition:
        raise DataContractError("Current fold partition does not match the frozen M1 selection.")
    if (
        contract.get("feature_contract") != current_features
        or persisted_features != current_features
        or contract.get("feature_contract_sha256") != _payload_sha256(current_features)
    ):
        raise DataContractError("Current feature contract does not match the frozen M1 selection.")
    if (
        contract.get("model_grid") != current_grid
        or persisted_grid != current_grid
        or contract.get("model_grid_sha256") != _payload_sha256(current_grid)
    ):
        raise DataContractError("Current model grid does not match the frozen M1 selection.")
    if contract.get("source_tree_sha256") != _source_tree_sha256(PROJECT_ROOT):
        raise DataContractError("Current source tree does not match the frozen M1 selection.")
    expected_environment = {
        "packages": _dependency_versions(),
        "project_version": __version__,
        "python": platform.python_version(),
    }
    if contract.get("environment") != expected_environment:
        raise DataContractError("Current Python environment does not match the frozen selection.")

    selected_payload = contract.get("selected_config")
    matches = [
        config for config in MODEL_CONFIG_GRID if _model_config_payload(config) == selected_payload
    ]
    if len(matches) != 1:
        raise DataContractError("Frozen selected configuration is not in the current model grid.")
    return selection, panel, cohort, confirmation_folds, matches[0]


def _decision_payload(selected_config: ModelConfig, promoted: bool) -> dict[str, object]:
    return {
        "champion": selected_config.name if promoted else "seasonal_naive_7d",
        "promoted": promoted,
        "status": "candidate_promoted_to_m2" if promoted else "candidate_rejected",
    }


def _verify_existing_confirmation_run(
    run_path: Path,
    *,
    validation_id: str,
    validation_identity: dict[str, Any],
    selection: dict[str, Any],
    selected_config: ModelConfig,
    confirmation_folds: tuple[BacktestFold, ...],
) -> dict[str, Any]:
    existing = _read_json_object(run_path, label="Existing M1 confirmation run")
    if existing.get("id") != validation_id or existing.get("artifact_schema_version") != 1:
        raise DataContractError("Existing confirmation run identity is inconsistent.")
    for key, expected in validation_identity.items():
        if existing.get(key) != expected:
            raise DataContractError(f"Existing confirmation run does not match {key}.")
    if existing.get("selected_config") != _model_config_payload(selected_config):
        raise DataContractError("Existing confirmation run used a different model configuration.")
    if existing.get("confirmation_folds") != _fold_payload(confirmation_folds):
        raise DataContractError("Existing confirmation run used a different fold partition.")
    expected_holdout = selection["selection_contract"]["partition"]["final_holdout"]
    if existing.get("final_holdout") != expected_holdout:
        raise DataContractError("Existing confirmation run changed the final holdout contract.")
    expected_environment = {
        "packages": _dependency_versions(),
        "project_version": __version__,
        "python": platform.python_version(),
    }
    if existing.get("environment") != expected_environment:
        raise DataContractError("Existing confirmation run used a different Python environment.")

    record_sha256 = existing.get("record_sha256")
    record_contract = {key: value for key, value in existing.items() if key != "record_sha256"}
    if not isinstance(record_sha256, str) or record_sha256 != _payload_sha256(record_contract):
        raise DataContractError("Existing confirmation run record has been modified.")
    outputs = existing.get("outputs")
    expected_output_names = {
        "baseline_confirmation_predictions",
        "candidate_confirmation_audit",
        "candidate_confirmation_predictions",
        "comparison",
    }
    if not isinstance(outputs, dict) or set(outputs) != expected_output_names:
        raise DataContractError("Existing confirmation output manifest is invalid.")
    _verify_output_records(run_path.parent, outputs)
    comparison = _read_json_object(
        _manifest_output_path(run_path.parent, outputs, "comparison"),
        label="Existing M1 comparison",
    )
    promoted = comparison.get("promoted")
    if not isinstance(promoted, bool):
        raise DataContractError("Existing confirmation comparison decision is invalid.")
    if existing.get("decision") != _decision_payload(selected_config, promoted):
        raise DataContractError("Existing confirmation decision does not match its comparison.")
    return existing


def _verify_confirmation_receipt(
    receipt_path: Path,
    *,
    selection: dict[str, Any],
    selection_hash: str,
    validation_id: str,
    validation_identity: dict[str, Any],
    selected_config: ModelConfig,
    confirmation_folds: tuple[BacktestFold, ...],
) -> dict[str, Any]:
    receipt = _read_json_object(receipt_path, label="M1 confirmation receipt")
    receipt_sha256 = receipt.get("record_sha256")
    receipt_contract = {key: value for key, value in receipt.items() if key != "record_sha256"}
    if not isinstance(receipt_sha256, str) or receipt_sha256 != _payload_sha256(receipt_contract):
        raise DataContractError("M1 confirmation receipt has been modified.")
    if (
        receipt.get("artifact_schema_version") != 1
        or receipt.get("selection_id") != selection["id"]
        or receipt.get("selection_sha256") != selection_hash
        or receipt.get("validation_id") != validation_id
    ):
        raise DataContractError("M1 confirmation receipt does not match the frozen selection.")
    referenced_run = receipt.get("run_path")
    expected_run_hash = receipt.get("run_sha256")
    if not isinstance(referenced_run, str) or not isinstance(expected_run_hash, str):
        raise DataContractError("M1 confirmation receipt has an invalid run reference.")
    referenced_run_path = Path(referenced_run).resolve()
    if not referenced_run_path.is_file() or sha256_file(referenced_run_path) != expected_run_hash:
        raise DataContractError("M1 confirmation receipt points to missing or modified evidence.")
    return _verify_existing_confirmation_run(
        referenced_run_path,
        validation_id=validation_id,
        validation_identity=validation_identity,
        selection=selection,
        selected_config=selected_config,
        confirmation_folds=confirmation_folds,
    )


def _validate_model(
    selection_path: Path,
    panel_path: Path,
    cohort_path: Path,
    report_dir: Path,
) -> int:
    selection, panel, cohort, confirmation_folds, selected_config = _verify_frozen_selection(
        selection_path,
        panel_path,
        cohort_path,
    )
    selection_hash = sha256_file(selection_path)
    validation_identity: dict[str, Any] = {
        "panel_sha256": cohort["panel_sha256"],
        "phase": "m1_confirmation",
        "selection_id": selection["id"],
        "selection_sha256": selection_hash,
        "source_tree_sha256": _source_tree_sha256(PROJECT_ROOT),
    }
    validation_id = _payload_sha256(validation_identity)[:16]
    registry_dir = cohort_path.parent / ".m1_evaluation"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_key = str(cohort["panel_sha256"])
    receipt_path = registry_dir / f"confirmation_receipt_{registry_key}.json"
    if receipt_path.is_file():
        existing = _verify_confirmation_receipt(
            receipt_path,
            selection=selection,
            selection_hash=selection_hash,
            validation_id=validation_id,
            validation_identity=validation_identity,
            selected_config=selected_config,
            confirmation_folds=confirmation_folds,
        )
        print(f"Confirmation already completed: {receipt_path}")
        print(f"Promoted: {existing['decision']['promoted']}")
        return 0

    run_dir = report_dir / "m1" / "validation" / validation_id
    run_path = run_dir / "run.json"
    claim_path = registry_dir / f"confirmation_claim_{registry_key}.json"
    if claim_path.exists():
        raise DataContractError(
            "This frozen selection already has a confirmation claim; inspect it before retrying."
        )
    claim_record: dict[str, Any] = {
        "artifact_schema_version": 1,
        "run_path": str(run_path.resolve()),
        "selection_id": selection["id"],
        "selection_sha256": selection_hash,
        "validation_id": validation_id,
    }
    claim_record["record_sha256"] = _payload_sha256(claim_record)
    _write_json_exclusive(claim_path, claim_record)
    if run_path.is_file():
        existing = _verify_existing_confirmation_run(
            run_path,
            validation_id=validation_id,
            validation_identity=validation_identity,
            selection=selection,
            selected_config=selected_config,
            confirmation_folds=confirmation_folds,
        )
        receipt_record: dict[str, Any] = {
            "artifact_schema_version": 1,
            "run_path": str(run_path.resolve()),
            "run_sha256": sha256_file(run_path),
            "selection_id": selection["id"],
            "selection_sha256": selection_hash,
            "validation_id": validation_id,
        }
        receipt_record["record_sha256"] = _payload_sha256(receipt_record)
        _write_json_atomic(receipt_path, receipt_record)
        print(f"Confirmation already completed: {run_path}")
        print(f"Promoted: {existing['decision']['promoted']}")
        return 0

    supervised = build_supervised_table(
        panel,
        max_target_date=confirmation_folds[-1].test_end,
    )
    fitted = fit_predict_folds(supervised, confirmation_folds, selected_config)
    baseline_predictions = seasonal_naive_predictions(panel, confirmation_folds)
    comparison = compare_candidate_to_baseline(
        fitted.predictions,
        baseline_predictions,
        panel,
    )
    run_contract: dict[str, Any] = {
        **validation_identity,
        "artifact_schema_version": 1,
        "confirmation_folds": _fold_payload(confirmation_folds),
        "decision": _decision_payload(selected_config, bool(comparison["promoted"])),
        "environment": {
            "packages": _dependency_versions(),
            "project_version": __version__,
            "python": platform.python_version(),
        },
        "final_holdout": selection["selection_contract"]["partition"]["final_holdout"],
        "selected_config": _model_config_payload(selected_config),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = run_dir / "candidate_confirmation_predictions.csv"
    baseline_path = run_dir / "baseline_confirmation_predictions.csv"
    audit_path = run_dir / "candidate_confirmation_audit.csv"
    comparison_path = run_dir / "comparison.json"
    _write_csv_atomic(candidate_path, fitted.predictions)
    _write_csv_atomic(baseline_path, baseline_predictions)
    _write_csv_atomic(audit_path, fitted.audit)
    _write_json_atomic(comparison_path, comparison)
    outputs = {
        "baseline_confirmation_predictions": _output_record(baseline_path, run_dir),
        "candidate_confirmation_audit": _output_record(audit_path, run_dir),
        "candidate_confirmation_predictions": _output_record(candidate_path, run_dir),
        "comparison": _output_record(comparison_path, run_dir),
    }
    run_record: dict[str, Any] = {"id": validation_id, **run_contract, "outputs": outputs}
    run_record["record_sha256"] = _payload_sha256(run_record)
    _write_json_atomic(run_path, run_record)
    receipt_record = {
        "artifact_schema_version": 1,
        "run_path": str(run_path.resolve()),
        "run_sha256": sha256_file(run_path),
        "selection_id": selection["id"],
        "selection_sha256": selection_hash,
        "validation_id": validation_id,
    }
    receipt_record["record_sha256"] = _payload_sha256(receipt_record)
    _write_json_atomic(receipt_path, receipt_record)

    candidate_overall = comparison["candidate"]["overall"]
    baseline_overall = comparison["baseline"]["overall"]
    print(f"Confirmation folds: {len(confirmation_folds)} (14-19)")
    print(f"Candidate WAPE: {candidate_overall['wape']:.6f}")
    print(f"Baseline WAPE: {baseline_overall['wape']:.6f}")
    print(f"Promoted: {bool(comparison['promoted'])}")
    print(f"Run: {run_path}")
    return 0


def _dependency_versions() -> dict[str, str | None]:
    packages = ("numpy", "openpyxl", "pandas", "scikit-learn")
    result: dict[str, str | None] = {}
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def _git_metadata(repository_root: Path) -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = subprocess.run(
            ["git", "-C", str(repository_root), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    if commit.returncode != 0 or status.returncode != 0:
        return {"commit": None, "dirty": None}
    return {"commit": commit.stdout.strip(), "dirty": bool(status.stdout.strip())}


def _source_tree_sha256(repository_root: Path) -> str:
    files: list[Path] = []
    source_directory = repository_root / "src"
    if source_directory.is_dir():
        files.extend(path for path in source_directory.rglob("*.py") if path.is_file())
    tests_directory = repository_root / "tests"
    if tests_directory.is_dir():
        files.extend(path for path in tests_directory.rglob("*.py") if path.is_file())
    requirements_directory = repository_root / "requirements"
    if requirements_directory.is_dir():
        files.extend(path for path in requirements_directory.rglob("*.txt") if path.is_file())
    pyproject = repository_root / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)

    digest = hashlib.sha256()
    for path in sorted(set(files), key=lambda item: item.relative_to(repository_root).as_posix()):
        relative = path.relative_to(repository_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, date_format="%Y-%m-%d", lineterminator="\n")
    os.replace(temporary, path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as target:
        target.write(_json_text(payload))
    os.replace(temporary, path)


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DataContractError(f"Exclusive artifact already exists: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
        target.write(_json_text(payload))


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "download":
        return _download(args.raw_dir)
    if args.command == "prepare":
        return _prepare(args.workbook, args.processed_dir)
    if args.command == "baseline":
        return _baseline(args.panel, args.cohort, args.report_dir)
    if args.command == "tune-model":
        return _tune_model(args.panel, args.cohort, args.report_dir)
    if args.command == "validate-model":
        return _validate_model(
            args.selection,
            args.panel,
            args.cohort,
            args.report_dir,
        )
    if args.command == "freeze-m2":
        from retail_forecasting.m2_cli import freeze_m2

        contract = freeze_m2(
            args.panel,
            args.cohort,
            args.m1_summary,
            args.report_dir,
        )
        print(f"Frozen M2 contract: {contract}")
        return 0
    if args.command == "evaluate-holdout":
        from retail_forecasting.m2_cli import evaluate_holdout

        run = evaluate_holdout(
            args.contract,
            args.panel,
            args.cohort,
            args.m1_summary,
            args.report_dir,
        )
        print(f"Final holdout run: {run}")
        return 0
    if args.command == "run-batch":
        from retail_forecasting.product_cli import run_batch

        result = run_batch(
            panel_path=args.panel,
            cohort_path=args.cohort,
            m1_summary_path=args.m1_summary,
            contract_path=args.contract,
            cutoff=args.cutoff,
            demo_repository=args.demo_repository,
            database_url=args.database_url,
        )
        print(_json_text(result), end="")
        return 0
    if args.command == "reconcile":
        from retail_forecasting.product_cli import reconcile

        result = reconcile(
            outcomes_path=args.outcomes,
            run_id=args.run_id,
            demo_repository=args.demo_repository,
            database_url=args.database_url,
        )
        print(_json_text(result), end="")
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")
