from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from retail_forecasting import product_cli
from retail_forecasting.cli import build_parser
from retail_forecasting.dataset import DataContractError, target_contract
from retail_forecasting.m2_cli import evaluate_holdout, freeze_m2


def _write_inputs(tmp_path):
    dates = pd.date_range("2020-01-01", periods=365 + 20 * 14 + 84 + 10, freq="D")
    panel = pd.DataFrame(
        [
            {"date": date, "sku": sku, "units": (date.dayofyear + offset) % 11}
            for date in dates
            for offset, sku in enumerate(("A", "B"))
        ]
    )
    panel_path = tmp_path / "daily_demand.csv"
    panel.to_csv(panel_path, index=False, date_format="%Y-%m-%d", lineterminator="\n")
    panel_sha256 = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    cohort_path = tmp_path / "cohort.json"
    cohort = {
        "cutoff_exclusive": "2020-12-31",
        "panel_sha256": panel_sha256,
        "selection": {
            "max_skus": 2,
            "min_active_days": 1,
            "recency_days": 7,
            "training_days": 365,
        },
        "skus": ["A", "B"],
        "source": {"name": "synthetic test data"},
        "target": target_contract(),
    }
    cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
    m1_path = tmp_path / "confirmation_summary.json"
    integrity_names = (
        "baseline_predictions_sha256",
        "candidate_audit_sha256",
        "candidate_predictions_sha256",
        "canonical_comparison_sha256",
        "canonical_run_sha256",
        "receipt_sha256",
        "run_record_sha256",
    )
    confirmation_start = dates[365 + 14 * 14]
    confirmation_end = confirmation_start + pd.Timedelta(days=83)
    m1_path.write_text(
        json.dumps(
            {
                "artifact_schema_version": 1,
                "audit": {
                    "folds": 6,
                    "raw_negative_predictions": 0,
                    "rows": 6 * 14 * 2,
                    "training_cutoff_mismatches": 0,
                },
                "baseline": {},
                "candidate": {},
                "criteria": {},
                "decision": {
                    "champion": "seasonal_naive_7d",
                    "promoted": False,
                    "status": "candidate_rejected",
                },
                "folds": [{"fold": fold} for fold in range(14, 20)],
                "integrity": {
                    name: hashlib.sha256(name.encode()).hexdigest() for name in integrity_names
                },
                "panel_sha256": panel_sha256,
                "selection_id": "a" * 16,
                "selection_sha256": "b" * 64,
                "sku_evidence": {},
                "validation_id": "c" * 16,
                "window": {
                    "end": confirmation_end.date().isoformat(),
                    "folds": list(range(14, 20)),
                    "final_holdout_status": "reserved_not_evaluated",
                    "start": confirmation_start.date().isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )
    return panel_path, cohort_path, m1_path


def test_m2_freeze_and_holdout_are_separate_and_idempotent(tmp_path) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    report_dir = tmp_path / "reports"

    contract_path = freeze_m2(panel_path, cohort_path, m1_path, report_dir)
    assert contract_path.is_file()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["partition"]["holdout_status"] == "frozen_not_evaluated"
    assert not list((report_dir / "m2").glob("holdout/*/evaluation.json"))

    run_path = evaluate_holdout(
        contract_path,
        panel_path,
        cohort_path,
        m1_path,
        report_dir,
    )
    same_run = evaluate_holdout(
        contract_path,
        panel_path,
        cohort_path,
        m1_path,
        report_dir,
    )
    assert same_run == run_path
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["holdout_status"] == "evaluated_once_no_retuning"
    evaluation_path = run_path.parent / run["outputs"]["evaluation"]["path"]
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    assert evaluation["folds"] == list(range(20, 26))
    assert evaluation["rows"] == 6 * 14 * 2


def test_parser_exposes_separate_m2_commands() -> None:
    freeze = build_parser().parse_args(["freeze-m2"])
    holdout = build_parser().parse_args(["evaluate-holdout", "--contract", "contract.json"])

    assert freeze.command == "freeze-m2"
    assert holdout.command == "evaluate-holdout"


def test_product_batch_rejects_tampered_contract_before_opening_storage(
    tmp_path, monkeypatch
) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    contract_path = freeze_m2(panel_path, cohort_path, m1_path, tmp_path / "reports")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["nominal_coverage"] = 0.75
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    storage_opened = False

    def reject_storage(**_kwargs):
        nonlocal storage_opened
        storage_opened = True
        raise AssertionError("Storage must not open for an invalid contract.")

    monkeypatch.setattr(product_cli, "repository_from_options", reject_storage)
    with pytest.raises(DataContractError, match="modified"):
        product_cli.run_batch(
            panel_path=panel_path,
            cohort_path=cohort_path,
            m1_summary_path=m1_path,
            contract_path=contract_path,
            cutoff="2021-01-01",
            demo_repository=tmp_path / "demo.json",
            database_url=None,
        )
    assert storage_opened is False


def test_product_batch_rejects_foreign_panel_before_opening_storage(tmp_path, monkeypatch) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    contract_path = freeze_m2(panel_path, cohort_path, m1_path, tmp_path / "reports")
    panel = pd.read_csv(panel_path)
    panel.loc[0, "units"] = int(panel.loc[0, "units"]) + 1
    panel.to_csv(panel_path, index=False, lineterminator="\n")
    storage_opened = False

    def reject_storage(**_kwargs):
        nonlocal storage_opened
        storage_opened = True
        raise AssertionError("Storage must not open for a foreign panel.")

    monkeypatch.setattr(product_cli, "repository_from_options", reject_storage)
    with pytest.raises(DataContractError, match="hash"):
        product_cli.run_batch(
            panel_path=panel_path,
            cohort_path=cohort_path,
            m1_summary_path=m1_path,
            contract_path=contract_path,
            cutoff="2021-01-01",
            demo_repository=tmp_path / "demo.json",
            database_url=None,
        )
    assert storage_opened is False


def test_freeze_rejects_a_promoted_candidate(tmp_path) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    summary = json.loads(m1_path.read_text(encoding="utf-8"))
    summary["decision"] = {
        "champion": "poisson_conservative",
        "promoted": True,
        "status": "candidate_promoted_to_m2",
    }
    m1_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(DataContractError, match="baseline-champion"):
        freeze_m2(panel_path, cohort_path, m1_path, tmp_path / "reports")


def test_freeze_rejects_a_minimal_unverifiable_m1_summary(tmp_path) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    summary = json.loads(m1_path.read_text(encoding="utf-8"))
    m1_path.write_text(
        json.dumps(
            {
                "decision": summary["decision"],
                "panel_sha256": summary["panel_sha256"],
                "window": {"final_holdout_status": "reserved_not_evaluated"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataContractError, match="schema"):
        freeze_m2(panel_path, cohort_path, m1_path, tmp_path / "reports")


def test_existing_receipt_is_bound_to_outputs_and_evaluation_identity(tmp_path) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    report_dir = tmp_path / "reports"
    contract_path = freeze_m2(panel_path, cohort_path, m1_path, report_dir)
    run_path = evaluate_holdout(
        contract_path,
        panel_path,
        cohort_path,
        m1_path,
        report_dir,
    )
    run = json.loads(run_path.read_text(encoding="utf-8"))
    evaluation_path = run_path.parent / run["outputs"]["evaluation"]["path"]
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["rows"] += 1
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

    with pytest.raises(DataContractError, match="output is missing or has been modified"):
        evaluate_holdout(
            contract_path,
            panel_path,
            cohort_path,
            m1_path,
            report_dir,
        )


def test_rehashed_receipt_cannot_redirect_to_another_run(tmp_path) -> None:
    panel_path, cohort_path, m1_path = _write_inputs(tmp_path)
    report_dir = tmp_path / "reports"
    contract_path = freeze_m2(panel_path, cohort_path, m1_path, report_dir)
    evaluate_holdout(
        contract_path,
        panel_path,
        cohort_path,
        m1_path,
        report_dir,
    )
    receipts = list((panel_path.parent / ".m2_evaluation").glob("holdout_receipt_*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    receipt["evaluation_id"] = "0" * 16
    unsigned = {key: value for key, value in receipt.items() if key != "record_sha256"}
    receipt["record_sha256"] = hashlib.sha256(
        json.dumps(unsigned, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    receipts[0].write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(DataContractError, match="does not match the frozen evaluation"):
        evaluate_holdout(
            contract_path,
            panel_path,
            cohort_path,
            m1_path,
            report_dir,
        )
