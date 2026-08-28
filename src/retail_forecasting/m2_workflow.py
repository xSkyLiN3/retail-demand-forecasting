from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from retail_forecasting.backtesting import (
    BacktestFold,
    make_folds,
    seasonal_naive_predictions,
    validate_panel,
)
from retail_forecasting.config import FINAL_HOLDOUT_DAYS, FORECAST_HORIZON_DAYS
from retail_forecasting.dataset import DataContractError

M2_CHAMPION = "seasonal_naive_7d"
M2_DEVELOPMENT_FOLDS = 20
M2_HOLDOUT_FOLDS = 6
M2_REPLAY_WARMUP_FOLDS = 6
M2_NOMINAL_COVERAGE = 0.90
M2_THRESHOLDS: dict[str, float] = {
    "maximum_absolute_bias": 0.10,
    "maximum_coverage": 0.98,
    "maximum_wape": 2.0,
    "minimum_coverage": 0.85,
}

Calibrate = Callable[[pd.DataFrame, pd.DataFrame, float], object]
ApplyIntervals = Callable[[pd.DataFrame, pd.DataFrame, object], pd.DataFrame]
Monitor = Callable[[pd.DataFrame, pd.DataFrame, Mapping[str, float]], Mapping[str, Any]]


@dataclass(frozen=True)
class M2Partition:
    development: tuple[BacktestFold, ...]
    holdout: tuple[BacktestFold, ...]
    gap_days: int


@dataclass(frozen=True)
class M2EvidenceHashes:
    panel_sha256: str
    cohort_manifest_sha256: str
    m1_confirmation_sha256: str
    source_tree_sha256: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest.")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest.") from exc
            if value != value.lower():
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest.")


@dataclass(frozen=True)
class M2FrozenWorkflow:
    contract: dict[str, Any]
    calibration: object
    calibration_contract: dict[str, Any]
    development_predictions: pd.DataFrame
    replay: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class HoldoutEvaluationPlan:
    contract_sha256: str
    champion: str
    folds: tuple[BacktestFold, ...]
    interval_calibration: object
    panel_sha256: str


def _fold_payload(fold: BacktestFold) -> dict[str, object]:
    return {
        "cutoff": fold.cutoff.date().isoformat(),
        "fold": int(fold.index),
        "test_end": fold.test_end.date().isoformat(),
        "test_start": fold.test_start.date().isoformat(),
    }


def _folds_from_contract(
    records: object,
    *,
    expected_indices: range,
    label: str,
) -> tuple[BacktestFold, ...]:
    if not isinstance(records, list) or len(records) != len(expected_indices):
        raise DataContractError(f"The frozen M2 {label} fold manifest is invalid.")
    folds: list[BacktestFold] = []
    expected_fields = {"cutoff", "fold", "test_end", "test_start"}
    try:
        for record in records:
            if not isinstance(record, dict) or set(record) != expected_fields:
                raise DataContractError(f"The frozen M2 {label} fold schema is invalid.")
            folds.append(
                BacktestFold(
                    index=int(record["fold"]),
                    cutoff=pd.Timestamp(record["cutoff"]).normalize(),
                    test_start=pd.Timestamp(record["test_start"]).normalize(),
                    test_end=pd.Timestamp(record["test_end"]).normalize(),
                )
            )
    except (TypeError, ValueError) as exc:
        raise DataContractError(f"The frozen M2 {label} fold values are invalid.") from exc
    if [fold.index for fold in folds] != list(expected_indices):
        raise DataContractError(f"The frozen M2 {label} fold indices are invalid.")
    if any(
        pd.isna(fold.cutoff)
        or pd.isna(fold.test_start)
        or pd.isna(fold.test_end)
        or fold.cutoff != fold.test_start - timedelta(days=1)
        or fold.test_end - fold.test_start != timedelta(days=FORECAST_HORIZON_DAYS - 1)
        for fold in folds
    ):
        raise DataContractError(f"The frozen M2 {label} fold boundaries are invalid.")
    if any(
        current.test_end + timedelta(days=1) != following.test_start
        for current, following in zip(folds, folds[1:], strict=False)
    ):
        raise DataContractError(f"The frozen M2 {label} folds are not contiguous.")
    return tuple(folds)


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _frame_sha256(frame: pd.DataFrame) -> str:
    canonical = frame.copy()
    for column in canonical.columns:
        if pd.api.types.is_datetime64_any_dtype(canonical[column]):
            canonical[column] = canonical[column].dt.strftime("%Y-%m-%d")
    canonical = canonical.sort_values(list(canonical.columns), kind="stable", ignore_index=True)
    contents = canonical.to_csv(index=False, lineterminator="\n", float_format="%.17g")
    return hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _json_contract(value: object, *, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        converter = getattr(value, "contract", None)
        if not callable(converter):
            converter = getattr(value, "to_contract", None)
        if not callable(converter):
            raise TypeError(f"{label} must be a mapping or expose to_contract().")
        payload = converter()
    if not isinstance(payload, dict):
        raise TypeError(f"{label} contract must be a dictionary.")
    json.dumps(payload, allow_nan=False, sort_keys=True)
    return payload


def build_m2_partition(panel: pd.DataFrame, *, initial_train_days: int) -> M2Partition:
    """Build the frozen 20-fold development and exact final-84-day partitions."""

    validated = validate_panel(panel)
    development = make_folds(
        validated,
        initial_train_days=initial_train_days,
        final_holdout_days=FINAL_HOLDOUT_DAYS,
    )
    if len(development) != M2_DEVELOPMENT_FOLDS:
        raise DataContractError(
            f"M2 requires exactly {M2_DEVELOPMENT_FOLDS} development folds; "
            f"found {len(development)}."
        )

    panel_end = pd.Timestamp(validated["date"].max())
    holdout_start = panel_end - timedelta(days=FINAL_HOLDOUT_DAYS - 1)
    holdout: list[BacktestFold] = []
    for offset in range(M2_HOLDOUT_FOLDS):
        test_start = holdout_start + timedelta(days=offset * FORECAST_HORIZON_DAYS)
        test_end = test_start + timedelta(days=FORECAST_HORIZON_DAYS - 1)
        holdout.append(
            BacktestFold(
                index=M2_DEVELOPMENT_FOLDS + offset,
                cutoff=test_start - timedelta(days=1),
                test_start=test_start,
                test_end=test_end,
            )
        )

    frozen_holdout = tuple(holdout)
    if frozen_holdout[0].test_start != holdout_start or frozen_holdout[-1].test_end != panel_end:
        raise DataContractError("M2 holdout does not cover the exact final 84 calendar days.")
    if development[-1].test_end >= frozen_holdout[0].test_start:
        raise DataContractError("M2 development and final holdout overlap.")
    if any(
        fold.test_end - fold.test_start != timedelta(days=FORECAST_HORIZON_DAYS - 1)
        for fold in (*development, *frozen_holdout)
    ):
        raise DataContractError("Every M2 fold must contain exactly 14 calendar days.")

    gap_days = (frozen_holdout[0].test_start - development[-1].test_end).days - 1
    return M2Partition(development, frozen_holdout, gap_days)


def _default_calibrate(
    predictions: pd.DataFrame, panel: pd.DataFrame, nominal_coverage: float
) -> object:
    from retail_forecasting.intervals import IntervalConfig, calibrate_intervals

    return calibrate_intervals(
        predictions,
        panel,
        config=IntervalConfig(nominal_coverage=nominal_coverage),
    )


def _default_apply(
    predictions: pd.DataFrame, panel: pd.DataFrame, calibration: object
) -> pd.DataFrame:
    from retail_forecasting.intervals import apply_intervals

    return apply_intervals(predictions, panel, calibration)


def _default_monitor(
    forecasts: pd.DataFrame,
    outcomes: pd.DataFrame,
    thresholds: Mapping[str, float],
) -> Mapping[str, Any]:
    from retail_forecasting.monitoring import AlertThresholds, monitor_forecasts

    return monitor_forecasts(
        forecasts,
        outcomes,
        nominal_coverage=M2_NOMINAL_COVERAGE,
        thresholds=AlertThresholds(**thresholds),
    )


def freeze_m2_workflow(
    panel: pd.DataFrame,
    *,
    initial_train_days: int,
    hashes: M2EvidenceHashes,
    m1_champion: str,
    calibrate: Calibrate = _default_calibrate,
    apply_intervals: ApplyIntervals = _default_apply,
    monitor: Monitor = _default_monitor,
) -> M2FrozenWorkflow:
    """Freeze M2 using development only; this function never predicts the final holdout."""

    if m1_champion != M2_CHAMPION:
        raise DataContractError(f"M2 is frozen for {M2_CHAMPION}, not {m1_champion}.")
    validated = validate_panel(panel)
    partition = build_m2_partition(validated, initial_train_days=initial_train_days)
    development_predictions = seasonal_naive_predictions(validated, partition.development)

    replay: list[dict[str, Any]] = []
    for position in range(M2_REPLAY_WARMUP_FOLDS, len(partition.development)):
        fold = partition.development[position]
        prior_fold_ids = {item.index for item in partition.development[:position]}
        calibration_rows = development_predictions.loc[
            development_predictions["fold"].isin(prior_fold_ids)
        ].copy()
        if pd.Timestamp(calibration_rows["date"].max()) > fold.cutoff:
            raise DataContractError(
                "Prequential calibration includes outcomes after its as_of date."
            )
        fold_rows = development_predictions.loc[
            development_predictions["fold"].eq(fold.index)
        ].copy()
        fold_calibration = calibrate(calibration_rows, validated, M2_NOMINAL_COVERAGE)
        interval_rows = apply_intervals(fold_rows, validated, fold_calibration)
        outcomes = fold_rows.loc[:, ["date", "sku", "actual"]]
        report = _json_contract(monitor(interval_rows, outcomes, M2_THRESHOLDS), label="monitoring")
        replay.append(
            {
                "as_of": fold.cutoff.date().isoformat(),
                "calibration_folds": sorted(prior_fold_ids),
                "calibration_max_outcome_date": pd.Timestamp(calibration_rows["date"].max())
                .date()
                .isoformat(),
                "evaluation_fold": _fold_payload(fold),
                "monitoring": report,
            }
        )

    final_calibration_object = calibrate(development_predictions, validated, M2_NOMINAL_COVERAGE)
    final_calibration = _json_contract(final_calibration_object, label="interval calibration")
    replay_payload = {"warmup_folds": M2_REPLAY_WARMUP_FOLDS, "windows": replay}
    partition_payload = {
        "development": [_fold_payload(fold) for fold in partition.development],
        "development_gap_before_holdout_days": partition.gap_days,
        "holdout": [_fold_payload(fold) for fold in partition.holdout],
        "holdout_status": "frozen_not_evaluated",
    }
    contract: dict[str, Any] = {
        "artifact_schema_version": 1,
        "champion": M2_CHAMPION,
        "development_predictions_sha256": _frame_sha256(development_predictions),
        "hashes": asdict(hashes),
        "interval_calibration": final_calibration,
        "interval_calibration_sha256": _payload_sha256(final_calibration),
        "nominal_coverage": M2_NOMINAL_COVERAGE,
        "partition": partition_payload,
        "phase": "m2_development_freeze",
        "replay": replay_payload,
        "replay_sha256": _payload_sha256(replay_payload),
        "thresholds": dict(M2_THRESHOLDS),
    }
    contract["contract_sha256"] = _payload_sha256(contract)
    return M2FrozenWorkflow(
        contract,
        final_calibration_object,
        final_calibration,
        development_predictions,
        tuple(replay),
    )


def prepare_holdout_evaluation(frozen: M2FrozenWorkflow) -> HoldoutEvaluationPlan:
    """Validate a frozen contract and return a plan without reading holdout outcomes."""

    contract = frozen.contract
    persisted_hash = contract.get("contract_sha256")
    unsigned = {key: value for key, value in contract.items() if key != "contract_sha256"}
    if not isinstance(persisted_hash, str) or persisted_hash != _payload_sha256(unsigned):
        raise DataContractError("The frozen M2 contract has been modified.")
    if contract.get("champion") != M2_CHAMPION:
        raise DataContractError("The frozen M2 contract has a different champion.")
    partition = contract.get("partition")
    if not isinstance(partition, dict) or partition.get("holdout_status") != "frozen_not_evaluated":
        raise DataContractError("The M2 holdout is not in its frozen pre-evaluation state.")

    development = _folds_from_contract(
        partition.get("development"),
        expected_indices=range(M2_DEVELOPMENT_FOLDS),
        label="development",
    )
    folds = _folds_from_contract(
        partition.get("holdout"),
        expected_indices=range(
            M2_DEVELOPMENT_FOLDS,
            M2_DEVELOPMENT_FOLDS + M2_HOLDOUT_FOLDS,
        ),
        label="holdout",
    )
    gap_days = partition.get("development_gap_before_holdout_days")
    expected_gap = (folds[0].test_start - development[-1].test_end).days - 1
    if (
        isinstance(gap_days, bool)
        or not isinstance(gap_days, int)
        or gap_days != expected_gap
        or gap_days < 0
    ):
        raise DataContractError("The frozen M2 development/holdout gap is invalid.")
    return HoldoutEvaluationPlan(
        contract_sha256=persisted_hash,
        champion=M2_CHAMPION,
        folds=folds,
        interval_calibration=frozen.calibration,
        panel_sha256=str(contract["hashes"]["panel_sha256"]),
    )
