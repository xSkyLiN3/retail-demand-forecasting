from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, NamedTuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from retail_forecasting.backtesting import BacktestFold
from retail_forecasting.config import FORECAST_HORIZON_DAYS
from retail_forecasting.dataset import DataContractError
from retail_forecasting.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS


@dataclass(frozen=True)
class ModelConfig:
    """Immutable, deliberately small configuration for the M1 candidate grid."""

    name: str
    loss: Literal["poisson", "squared_error"]
    max_leaf_nodes: int
    min_samples_leaf: int
    learning_rate: float = 0.05
    max_iter: int = 250
    l2_regularization: float = 1.0
    early_stopping: bool = False
    random_state: int = 42

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Model configuration name must not be empty.")
        if self.loss not in {"poisson", "squared_error"}:
            raise ValueError(f"Unsupported M1 loss: {self.loss}")
        if self.max_leaf_nodes < 2 or self.min_samples_leaf < 1:
            raise ValueError("Tree size parameters must be positive and non-trivial.")
        if self.learning_rate <= 0 or self.max_iter < 1 or self.l2_regularization < 0:
            raise ValueError("Learning parameters are outside their valid range.")

    def estimator_parameters(self) -> dict[str, object]:
        return {
            "loss": self.loss,
            "max_leaf_nodes": self.max_leaf_nodes,
            "min_samples_leaf": self.min_samples_leaf,
            "learning_rate": self.learning_rate,
            "max_iter": self.max_iter,
            "l2_regularization": self.l2_regularization,
            "early_stopping": self.early_stopping,
            "random_state": self.random_state,
            "categorical_features": list(CATEGORICAL_FEATURES),
        }


MODEL_CONFIG_GRID = (
    ModelConfig(
        name="poisson_conservative",
        loss="poisson",
        max_leaf_nodes=7,
        min_samples_leaf=80,
    ),
    ModelConfig(
        name="poisson_medium",
        loss="poisson",
        max_leaf_nodes=15,
        min_samples_leaf=40,
    ),
    ModelConfig(
        name="squared_error_control",
        loss="squared_error",
        max_leaf_nodes=15,
        min_samples_leaf=40,
    ),
)


def model_grid_contract() -> dict[str, object]:
    """Return a JSON-serializable description of the frozen M1 model search."""

    return {
        "schema_version": 1,
        "strategy": "global_direct_long_format",
        "horizon_days": FORECAST_HORIZON_DAYS,
        "feature_columns": list(FEATURE_COLUMNS),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "configs": [
            {
                "name": config.name,
                **config.estimator_parameters(),
            }
            for config in MODEL_CONFIG_GRID
        ],
    }


class FitPredictResult(NamedTuple):
    predictions: pd.DataFrame
    audit: pd.DataFrame


def _validate_folds(folds: tuple[BacktestFold, ...]) -> None:
    if not folds:
        raise DataContractError("At least one fold is required for model evaluation.")
    indices = [fold.index for fold in folds]
    if indices != sorted(set(indices)):
        raise DataContractError("Fold identifiers must be ordered and unique.")

    previous_cutoff: pd.Timestamp | None = None
    for fold in folds:
        cutoff = pd.Timestamp(fold.cutoff).normalize()
        test_start = pd.Timestamp(fold.test_start).normalize()
        test_end = pd.Timestamp(fold.test_end).normalize()
        if test_start != cutoff + timedelta(days=1):
            raise DataContractError(f"Fold {fold.index} does not start after its cutoff.")
        if test_end != cutoff + timedelta(days=FORECAST_HORIZON_DAYS):
            raise DataContractError(
                f"Fold {fold.index} does not span {FORECAST_HORIZON_DAYS} horizons."
            )
        if previous_cutoff is not None and cutoff <= previous_cutoff:
            raise DataContractError("Folds must be chronological.")
        previous_cutoff = cutoff


def partition_development_folds(
    folds: tuple[BacktestFold, ...],
) -> tuple[tuple[BacktestFold, ...], tuple[BacktestFold, ...]]:
    """Return the fixed M1 tuning (0-13) and validation (14-19) partitions."""

    if len(folds) != 20:
        raise DataContractError(f"M1 requires exactly 20 development folds, received {len(folds)}.")
    _validate_folds(folds)
    if [fold.index for fold in folds] != list(range(20)):
        raise DataContractError("M1 development fold identifiers must be exactly 0 through 19.")
    return folds[:14], folds[14:]


def _validate_table(table: pd.DataFrame) -> pd.DataFrame:
    required = {"origin_date", "target_date", "sku", "actual", *FEATURE_COLUMNS}
    missing = required.difference(table.columns)
    if missing:
        raise DataContractError(f"Supervised table columns missing: {sorted(missing)}")
    if table.empty:
        raise DataContractError("Supervised table is empty.")

    result = table.loc[:, ["origin_date", "target_date", "sku", "actual", *FEATURE_COLUMNS]].copy()
    result["origin_date"] = pd.to_datetime(
        result["origin_date"], errors="coerce", format="mixed"
    ).dt.normalize()
    result["target_date"] = pd.to_datetime(
        result["target_date"], errors="coerce", format="mixed"
    ).dt.normalize()
    result["sku"] = result["sku"].astype("string").str.strip()
    result["actual"] = pd.to_numeric(result["actual"], errors="coerce")
    for column in FEATURE_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    if result.isna().any().any() or result["sku"].eq("").any():
        raise DataContractError("Supervised table contains missing or invalid values.")
    numeric_columns = ["actual", *FEATURE_COLUMNS]
    if not np.isfinite(result[numeric_columns].to_numpy(dtype="float64")).all():
        raise DataContractError("Supervised table contains non-finite numeric values.")
    if (result["actual"] < 0).any():
        raise DataContractError("Supervised labels must be non-negative.")
    if result.duplicated(["origin_date", "target_date", "sku"]).any():
        raise DataContractError("Supervised table contains duplicate origin/target/SKU rows.")

    implied_horizon = (result["target_date"] - result["origin_date"]).dt.days
    if not implied_horizon.equals(result["horizon"].astype("int64")):
        raise DataContractError("Supervised horizons do not match origin and target dates.")
    if not result["horizon"].between(1, FORECAST_HORIZON_DAYS).all():
        raise DataContractError("Supervised horizons fall outside the M1 forecast range.")
    return result.sort_values(
        ["origin_date", "target_date", "sku"], kind="stable", ignore_index=True
    )


def _category_levels(table: pd.DataFrame) -> dict[str, list[int]]:
    return {
        column: sorted(table[column].astype("int64").unique().tolist())
        for column in CATEGORICAL_FEATURES
    }


def _feature_frame(
    rows: pd.DataFrame,
    category_levels: dict[str, list[int]],
) -> pd.DataFrame:
    features = rows.loc[:, FEATURE_COLUMNS].copy()
    for column in CATEGORICAL_FEATURES:
        features[column] = features[column].astype(
            pd.CategoricalDtype(categories=category_levels[column])
        )
    return features


def _evaluation_rows(
    table: pd.DataFrame,
    fold: BacktestFold,
    expected_skus: tuple[str, ...],
) -> pd.DataFrame:
    cutoff = pd.Timestamp(fold.cutoff).normalize()
    evaluation = table.loc[table["origin_date"].eq(cutoff)].copy()
    if evaluation.empty:
        raise DataContractError(f"Fold {fold.index} has no rows at its forecast origin.")

    expected_horizons = tuple(range(1, FORECAST_HORIZON_DAYS + 1))
    expected_grid = pd.MultiIndex.from_product(
        [expected_skus, expected_horizons], names=["sku", "horizon"]
    )
    actual_grid = pd.MultiIndex.from_frame(evaluation[["sku", "horizon"]])
    if len(actual_grid) != len(expected_grid) or set(actual_grid) != set(expected_grid):
        raise DataContractError(
            f"Fold {fold.index} does not contain one complete 14-horizon row per SKU."
        )
    expected_target = evaluation["origin_date"] + pd.to_timedelta(evaluation["horizon"], unit="D")
    if not evaluation["target_date"].equals(expected_target):
        raise DataContractError(f"Fold {fold.index} target dates do not match their horizons.")
    return evaluation.sort_values(["target_date", "sku"], kind="stable", ignore_index=True)


def fit_predict_folds(
    table: pd.DataFrame,
    folds: tuple[BacktestFold, ...],
    config: ModelConfig,
) -> FitPredictResult:
    """Fit a fresh global direct model per fold and return metric-ready predictions and audit."""

    if not isinstance(config, ModelConfig):
        raise TypeError("config must be a ModelConfig instance.")
    _validate_folds(folds)
    validated = _validate_table(table)
    expected_skus = tuple(sorted(validated["sku"].astype(str).unique()))
    category_levels = _category_levels(validated)

    prediction_frames: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []
    for fold in folds:
        cutoff = pd.Timestamp(fold.cutoff).normalize()
        training = validated.loc[validated["target_date"].le(cutoff)].copy()
        if training.empty:
            raise DataContractError(f"Fold {fold.index} has no causally available training rows.")
        evaluation = _evaluation_rows(validated, fold, expected_skus)

        estimator = HistGradientBoostingRegressor(**config.estimator_parameters())
        estimator.fit(
            _feature_frame(training, category_levels),
            training["actual"].to_numpy(dtype="float64"),
        )
        raw_predictions = np.asarray(
            estimator.predict(_feature_frame(evaluation, category_levels)), dtype="float64"
        )
        if raw_predictions.shape != (len(evaluation),) or not np.isfinite(raw_predictions).all():
            raise DataContractError(f"Fold {fold.index} produced invalid model predictions.")
        negative_count = int((raw_predictions < 0).sum())
        predictions = np.clip(raw_predictions, a_min=0.0, a_max=None)

        prediction_frames.append(
            pd.DataFrame(
                {
                    "fold": int(fold.index),
                    "cutoff": cutoff,
                    "date": evaluation["target_date"].to_numpy(),
                    "sku": evaluation["sku"].astype(str).to_numpy(),
                    "horizon": evaluation["horizon"].astype("int64").to_numpy(),
                    "actual": evaluation["actual"].astype("float64").to_numpy(),
                    "prediction": predictions,
                    "model": config.name,
                }
            )
        )
        audit_rows.append(
            {
                "fold": int(fold.index),
                "cutoff": cutoff,
                "model": config.name,
                "loss": config.loss,
                "train_rows": int(len(training)),
                "train_origins": int(training["origin_date"].nunique()),
                "train_skus": int(training["sku"].nunique()),
                "train_max_target_date": pd.Timestamp(training["target_date"].max()),
                "evaluation_rows": int(len(evaluation)),
                "evaluation_skus": int(evaluation["sku"].nunique()),
                "raw_negative_predictions": negative_count,
                "raw_prediction_min": float(raw_predictions.min()),
                "raw_prediction_max": float(raw_predictions.max()),
                "prediction_min": float(predictions.min()),
                "prediction_max": float(predictions.max()),
            }
        )

    all_predictions = pd.concat(prediction_frames, ignore_index=True).sort_values(
        ["fold", "date", "sku"], kind="stable", ignore_index=True
    )
    audit = pd.DataFrame.from_records(audit_rows).sort_values(
        "fold", kind="stable", ignore_index=True
    )
    return FitPredictResult(predictions=all_predictions, audit=audit)
