from __future__ import annotations

import json

import pandas as pd
import pytest

from retail_forecasting.cli import main
from retail_forecasting.dataset import prepare_daily_panel, write_prepared_dataset
from retail_forecasting.source import SourceIntegrityError


def _transaction(day: str, index: int) -> dict[str, object]:
    return {
        "Country": "United Kingdom",
        "Customer ID": 12345,
        "Description": "Example",
        "Invoice": f"{index:06d}",
        "InvoiceDate": day,
        "Price": 1.0,
        "Quantity": (index % 7) + 1,
        "StockCode": "10000",
    }


def test_cli_help(capsys) -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    assert "retail-forecast" in capsys.readouterr().out


def test_prepare_rejects_an_unpinned_workbook(tmp_path) -> None:
    workbook = tmp_path / "untrusted.xlsx"
    workbook.write_bytes(b"not the official workbook")

    with pytest.raises(SourceIntegrityError, match="SHA-256 mismatch"):
        main(
            [
                "prepare",
                "--workbook",
                str(workbook),
                "--processed-dir",
                str(tmp_path / "processed"),
            ]
        )


def test_baseline_cli_writes_reproducible_development_evidence(tmp_path) -> None:
    transactions = pd.DataFrame(
        [
            _transaction(day.isoformat(), index)
            for index, day in enumerate(pd.date_range("2020-01-01", periods=700, freq="D"))
        ]
    )
    prepared = prepare_daily_panel(
        transactions,
        cohort_training_days=500,
        min_active_days=60,
        max_skus=1,
        recency_days=56,
    )
    processed_dir = tmp_path / "processed"
    report_dir = tmp_path / "reports"
    panel_path, cohort_path, _ = write_prepared_dataset(prepared, processed_dir)

    assert (
        main(
            [
                "baseline",
                "--panel",
                str(panel_path),
                "--cohort",
                str(cohort_path),
                "--report-dir",
                str(report_dir),
            ]
        )
        == 0
    )

    run_paths = list(report_dir.glob("baseline/*/run.json"))
    assert len(run_paths) == 1
    run = json.loads(run_paths[0].read_text(encoding="utf-8"))
    assert b"\r\n" not in run_paths[0].read_bytes()
    assert run["final_holdout"]["status"] == "reserved_not_evaluated"
    assert run["outputs"]["metrics_sha256"]
    assert run["development"]["fold_count"] >= 6
    assert run["development"]["first_cutoff"] == "2021-05-14"
    assert run["cohort_cutoff_exclusive"] == "2021-05-15"
