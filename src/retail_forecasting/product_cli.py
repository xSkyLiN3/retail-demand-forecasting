from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from retail_forecasting.batch import reconcile_known_outcomes, run_champion_batch
from retail_forecasting.dataset import DataContractError
from retail_forecasting.m2_cli import load_verified_frozen
from retail_forecasting.storage import (
    ForecastRepository,
    JsonForecastRepository,
    PostgresForecastRepository,
)


def repository_from_options(
    *,
    demo_repository: Path | None,
    database_url: str | None,
) -> ForecastRepository:
    if bool(demo_repository) == bool(database_url):
        raise DataContractError(
            "Choose exactly one storage backend: --demo-repository or --database-url."
        )
    repository: ForecastRepository
    if database_url:
        repository = PostgresForecastRepository(database_url)
    else:
        if demo_repository is None:
            raise AssertionError("Demo repository path unexpectedly missing.")
        repository = JsonForecastRepository(demo_repository)
    repository.migrate()
    return repository


def run_batch(
    *,
    panel_path: Path,
    cohort_path: Path,
    m1_summary_path: Path,
    contract_path: Path,
    cutoff: str,
    demo_repository: Path | None,
    database_url: str | None,
) -> dict[str, Any]:
    panel, _, frozen = load_verified_frozen(
        contract_path,
        panel_path,
        cohort_path,
        m1_summary_path,
    )
    repository = repository_from_options(
        demo_repository=demo_repository,
        database_url=database_url,
    )
    return run_champion_batch(
        panel,
        cutoff=cutoff,
        calibration=frozen.calibration,
        repository=repository,
    )


def reconcile(
    *,
    outcomes_path: Path,
    run_id: str,
    demo_repository: Path | None,
    database_url: str | None,
) -> dict[str, Any]:
    if not outcomes_path.is_file():
        raise FileNotFoundError(f"Outcome panel not found: {outcomes_path}")
    outcomes = pd.read_csv(
        outcomes_path,
        parse_dates=["date"],
        dtype={"sku": "string"},
    )
    repository = repository_from_options(
        demo_repository=demo_repository,
        database_url=database_url,
    )
    return reconcile_known_outcomes(repository, run_id=run_id, outcomes=outcomes)
