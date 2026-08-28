from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

from retail_forecasting import cli
from retail_forecasting.backtesting import BacktestFold
from retail_forecasting.dataset import DataContractError
from retail_forecasting.source import sha256_file


def _fold(index: int) -> BacktestFold:
    cutoff = pd.Timestamp("2020-12-31") + timedelta(days=14 * index)
    return BacktestFold(
        index=index,
        cutoff=cutoff,
        test_start=cutoff + timedelta(days=1),
        test_end=cutoff + timedelta(days=14),
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _frozen_selection(tmp_path: Path, monkeypatch):
    panel_path = tmp_path / "panel.csv"
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text('{"fixture": true}\n', encoding="utf-8")
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()
    evidence_path = selection_dir / "search.json"
    evidence_path.write_text('{"result": "frozen"}\n', encoding="utf-8")

    panel = pd.DataFrame({"date": [pd.Timestamp("2022-12-31")]})
    cohort = {
        "panel_sha256": "panel-sha",
        "skus": [f"SKU-{index:02d}" for index in range(20)],
    }
    tuning_folds = tuple(_fold(index) for index in range(14))
    confirmation_folds = tuple(_fold(index) for index in range(14, 20))
    features = {"schema": "features-v1"}
    grid = {"schema": "grid-v1"}
    environment = {
        "packages": {"fixture": "1"},
        "project_version": "test",
        "python": "3.12-test",
    }
    source_tree = "source-tree-v1"
    selected_config = cli.MODEL_CONFIG_GRID[0]

    monkeypatch.setattr(
        cli,
        "_read_verified_m1_inputs",
        lambda *_: (panel, cohort, tuning_folds, confirmation_folds),
    )
    monkeypatch.setattr(cli, "feature_contract", lambda: features)
    monkeypatch.setattr(cli, "model_grid_contract", lambda: grid)
    monkeypatch.setattr(cli, "_dependency_versions", lambda: environment["packages"])
    monkeypatch.setattr(cli, "__version__", environment["project_version"])
    monkeypatch.setattr(cli.platform, "python_version", lambda: environment["python"])
    monkeypatch.setattr(cli, "_source_tree_sha256", lambda *_: source_tree)

    artifact_payloads = {
        "baseline_tuning_predictions": ("baseline.csv", "baseline\n"),
        "feature_contract": ("feature_contract.json", json.dumps(features) + "\n"),
        "model_grid": ("model_grid.json", json.dumps(grid) + "\n"),
        "search": (evidence_path.name, evidence_path.read_text(encoding="utf-8")),
        "selected_tuning_audit": ("audit.csv", "audit\n"),
        "selected_tuning_predictions": ("selected.csv", "selected\n"),
    }
    outputs: dict[str, dict[str, str]] = {}
    for name, (filename, contents) in artifact_payloads.items():
        path = selection_dir / filename
        path.write_text(contents, encoding="utf-8")
        outputs[name] = {"file": filename, "sha256": sha256_file(path)}

    contract = {
        "confirmation_status": "not_run",
        "environment": environment,
        "feature_contract": features,
        "feature_contract_sha256": cli._payload_sha256(features),
        "inputs": {
            "cohort_manifest_sha256": sha256_file(cohort_path),
            "panel_sha256": cohort["panel_sha256"],
        },
        "model_grid": grid,
        "model_grid_sha256": cli._payload_sha256(grid),
        "outputs": outputs,
        "partition": cli._partition_payload(panel, tuning_folds, confirmation_folds),
        "selected_config": cli._model_config_payload(selected_config),
        "source_tree_sha256": source_tree,
    }
    selection = {
        "artifact_schema_version": 1,
        "confirmation_status": "not_run",
        "id": cli._payload_sha256(contract)[:16],
        "outputs": outputs,
        "selection_contract": contract,
    }
    selection_path = selection_dir / "selection.json"
    _write_json(selection_path, selection)
    return {
        "cohort": cohort,
        "cohort_path": cohort_path,
        "confirmation_folds": confirmation_folds,
        "evidence_path": evidence_path,
        "panel": panel,
        "panel_path": panel_path,
        "selected_config": selected_config,
        "selection": selection,
        "selection_path": selection_path,
        "source_tree": source_tree,
    }


def test_parser_exposes_both_m1_commands() -> None:
    parser = cli.build_parser()

    tune = parser.parse_args(["tune-model"])
    validate = parser.parse_args(["validate-model", "--selection", "selection.json"])

    assert tune.command == "tune-model"
    assert validate.command == "validate-model"
    assert validate.selection == Path("selection.json")


@pytest.mark.parametrize("tampering", ["contract", "output", "source_tree"])
def test_frozen_selection_detects_contract_output_and_source_tampering(
    tmp_path, monkeypatch, tampering
) -> None:
    fixture = _frozen_selection(tmp_path, monkeypatch)
    if tampering == "contract":
        fixture["selection"]["selection_contract"]["random_seed"] = 999
        _write_json(fixture["selection_path"], fixture["selection"])
        message = "identifier does not match"
    elif tampering == "output":
        fixture["evidence_path"].write_text("tampered\n", encoding="utf-8")
        message = "output hash mismatch"
    else:
        monkeypatch.setattr(cli, "_source_tree_sha256", lambda *_: "different-source-tree")
        message = "source tree does not match"

    with pytest.raises(DataContractError, match=message):
        cli._verify_frozen_selection(
            fixture["selection_path"],
            fixture["panel_path"],
            fixture["cohort_path"],
        )


def test_existing_confirmation_is_reused_without_training(tmp_path, monkeypatch) -> None:
    fixture = _frozen_selection(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cli,
        "_verify_frozen_selection",
        lambda *_: (
            fixture["selection"],
            fixture["panel"],
            fixture["cohort"],
            fixture["confirmation_folds"],
            fixture["selected_config"],
        ),
    )

    report_dir = tmp_path / "reports"
    identity = {
        "panel_sha256": fixture["cohort"]["panel_sha256"],
        "phase": "m1_confirmation",
        "selection_id": fixture["selection"]["id"],
        "selection_sha256": sha256_file(fixture["selection_path"]),
        "source_tree_sha256": fixture["source_tree"],
    }
    validation_id = cli._payload_sha256(identity)[:16]
    run_dir = report_dir / "m1" / "validation" / validation_id
    run_dir.mkdir(parents=True)
    comparison_path = run_dir / "comparison.json"
    comparison_path.write_text('{"promoted": false}\n', encoding="utf-8")
    output_files = {
        "baseline_confirmation_predictions": "baseline.csv",
        "candidate_confirmation_audit": "audit.csv",
        "candidate_confirmation_predictions": "candidate.csv",
        "comparison": comparison_path.name,
    }
    outputs: dict[str, dict[str, str]] = {}
    for name, filename in output_files.items():
        path = run_dir / filename
        if not path.exists():
            path.write_text(f"{name}\n", encoding="utf-8")
        outputs[name] = {"file": filename, "sha256": sha256_file(path)}
    run_record = {
        **identity,
        "artifact_schema_version": 1,
        "confirmation_folds": cli._fold_payload(fixture["confirmation_folds"]),
        "decision": cli._decision_payload(fixture["selected_config"], False),
        "environment": {
            "packages": cli._dependency_versions(),
            "project_version": cli.__version__,
            "python": cli.platform.python_version(),
        },
        "final_holdout": fixture["selection"]["selection_contract"]["partition"]["final_holdout"],
        "id": validation_id,
        "outputs": outputs,
        "selected_config": cli._model_config_payload(fixture["selected_config"]),
    }
    run_record["record_sha256"] = cli._payload_sha256(run_record)
    _write_json(run_dir / "run.json", run_record)

    def unexpected_training(*_args, **_kwargs):
        raise AssertionError("An existing confirmation must not train again.")

    monkeypatch.setattr(cli, "build_supervised_table", unexpected_training)
    monkeypatch.setattr(cli, "fit_predict_folds", unexpected_training)

    assert (
        cli._validate_model(
            fixture["selection_path"],
            fixture["panel_path"],
            fixture["cohort_path"],
            report_dir,
        )
        == 0
    )
    receipts = list((fixture["cohort_path"].parent / ".m1_evaluation").glob("*receipt*.json"))
    assert len(receipts) == 1
    assert (
        cli._validate_model(
            fixture["selection_path"],
            fixture["panel_path"],
            fixture["cohort_path"],
            tmp_path / "different-report-root",
        )
        == 0
    )

    tampered_run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    tampered_run["decision"]["promoted"] = True
    _write_json(run_dir / "run.json", tampered_run)
    with pytest.raises(DataContractError, match="modified evidence"):
        cli._validate_model(
            fixture["selection_path"],
            fixture["panel_path"],
            fixture["cohort_path"],
            tmp_path / "third-report-root",
        )


def test_output_manifest_rejects_path_traversal(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    outputs = {
        "outside": {
            "file": "../outside.json",
            "sha256": sha256_file(outside),
        }
    }

    with pytest.raises(DataContractError, match="outside its run"):
        cli._verify_output_records(run_dir, outputs)
