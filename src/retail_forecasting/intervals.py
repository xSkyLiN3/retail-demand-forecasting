from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from retail_forecasting.backtesting import validate_panel
from retail_forecasting.config import FORECAST_SEASONALITY_DAYS
from retail_forecasting.dataset import DataContractError
from retail_forecasting.metrics import seasonal_scales

PREDICTION_KEYS = ("fold", "cutoff", "date", "sku", "horizon")
QUANTILE_METHOD = "inverted_cdf"
QUANTILE_COLUMNS = (
    "horizon",
    "scaled_lower",
    "scaled_upper",
    "raw_lower",
    "raw_upper",
)
CALIBRATION_HORIZONS = tuple(range(1, 15))
CALIBRATION_VALUE_FIELDS = {
    *QUANTILE_COLUMNS,
    "rows",
    "scaled_rows",
    "fallback_rows",
}


@dataclass(frozen=True)
class IntervalConfig:
    nominal_coverage: float = 0.90
    seasonality_days: int = FORECAST_SEASONALITY_DAYS

    def __post_init__(self) -> None:
        if not 0.0 < self.nominal_coverage < 1.0:
            raise ValueError("nominal_coverage must be strictly between zero and one.")
        if isinstance(self.seasonality_days, bool) or not isinstance(self.seasonality_days, int):
            raise TypeError("seasonality_days must be an integer.")
        if self.seasonality_days < 1:
            raise ValueError("seasonality_days must be positive.")


@dataclass(frozen=True)
class IntervalCalibration:
    config: IntervalConfig
    quantiles: pd.DataFrame

    def contract(self) -> dict[str, object]:
        records = self.quantiles.sort_values("horizon").to_dict(orient="records")
        return {
            "fallback": "raw_signed_residual_by_horizon",
            "grouping": "horizon",
            "nominal_coverage": self.config.nominal_coverage,
            "quantile_method": QUANTILE_METHOD,
            "quantiles_anchored_at_zero": True,
            "residual": "(actual-prediction)/causal_seasonal_scale",
            "seasonality_days": self.config.seasonality_days,
            "values": records,
        }

    @classmethod
    def from_contract(cls, contract: Mapping[str, object]) -> IntervalCalibration:
        expected_fields = {
            "fallback",
            "grouping",
            "nominal_coverage",
            "quantile_method",
            "quantiles_anchored_at_zero",
            "residual",
            "seasonality_days",
            "values",
        }
        if set(contract) != expected_fields:
            raise DataContractError("Persisted interval calibration has an invalid schema.")
        if (
            contract["fallback"] != "raw_signed_residual_by_horizon"
            or contract["grouping"] != "horizon"
            or contract["quantile_method"] != QUANTILE_METHOD
            or contract["quantiles_anchored_at_zero"] is not True
            or contract["residual"] != "(actual-prediction)/causal_seasonal_scale"
        ):
            raise DataContractError("Persisted interval calibration method is not supported.")
        coverage = contract["nominal_coverage"]
        seasonality = contract["seasonality_days"]
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
            raise DataContractError("Persisted nominal coverage is invalid.")
        if isinstance(seasonality, bool) or not isinstance(seasonality, int):
            raise DataContractError("Persisted seasonality is invalid.")
        try:
            config = IntervalConfig(
                nominal_coverage=float(coverage),
                seasonality_days=seasonality,
            )
        except (TypeError, ValueError) as exc:
            raise DataContractError("Persisted interval configuration is invalid.") from exc

        values = contract["values"]
        if not isinstance(values, list) or len(values) != len(CALIBRATION_HORIZONS):
            raise DataContractError("Persisted calibration must contain exactly 14 horizons.")
        records: list[dict[str, float | int]] = []
        for value in values:
            if not isinstance(value, Mapping) or set(value) != CALIBRATION_VALUE_FIELDS:
                raise DataContractError("Persisted calibration horizon fields are invalid.")
            horizon = value["horizon"]
            counts = (value["rows"], value["scaled_rows"], value["fallback_rows"])
            quantiles = (
                value["scaled_lower"],
                value["scaled_upper"],
                value["raw_lower"],
                value["raw_upper"],
            )
            if isinstance(horizon, bool) or not isinstance(horizon, int):
                raise DataContractError("Persisted calibration horizon is invalid.")
            if any(
                isinstance(count, bool) or not isinstance(count, int) or count < 0
                for count in counts
            ):
                raise DataContractError("Persisted calibration row counts are invalid.")
            if counts[0] < 1 or counts[1] < 1 or counts[1] + counts[2] != counts[0]:
                raise DataContractError("Persisted calibration row counts are inconsistent.")
            if any(
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not np.isfinite(float(number))
                for number in quantiles
            ):
                raise DataContractError("Persisted calibration quantiles must be finite numbers.")
            scaled_lower, scaled_upper, raw_lower, raw_upper = map(float, quantiles)
            if not (scaled_lower <= 0.0 <= scaled_upper and raw_lower <= 0.0 <= raw_upper):
                raise DataContractError("Persisted calibration quantiles are not anchored at zero.")
            records.append(
                {
                    "horizon": horizon,
                    "scaled_lower": scaled_lower,
                    "scaled_upper": scaled_upper,
                    "raw_lower": raw_lower,
                    "raw_upper": raw_upper,
                    "rows": counts[0],
                    "scaled_rows": counts[1],
                    "fallback_rows": counts[2],
                }
            )
        horizons = sorted(record["horizon"] for record in records)
        if horizons != list(CALIBRATION_HORIZONS):
            raise DataContractError("Persisted calibration horizons must be unique and span 1-14.")
        return cls(config=config, quantiles=pd.DataFrame.from_records(records))


def _validated_prediction_rows(
    predictions: pd.DataFrame,
    *,
    require_actual: bool,
) -> pd.DataFrame:
    required = {*PREDICTION_KEYS, "prediction"}
    if require_actual:
        required.add("actual")
    missing = required.difference(predictions.columns)
    if missing:
        raise DataContractError(f"Prediction columns missing: {sorted(missing)}")
    if predictions.empty:
        raise DataContractError("Predictions are empty.")

    result = predictions.copy()
    result["cutoff"] = pd.to_datetime(
        result["cutoff"], errors="coerce", format="mixed"
    ).dt.normalize()
    result["date"] = pd.to_datetime(result["date"], errors="coerce", format="mixed").dt.normalize()
    result["sku"] = result["sku"].astype("string").str.strip()
    numeric = ["fold", "horizon", "prediction"]
    if require_actual:
        numeric.append("actual")
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    checked = [*PREDICTION_KEYS, "prediction"]
    if require_actual:
        checked.append("actual")
    if result[checked].isna().any().any():
        raise DataContractError("Predictions contain missing or invalid values.")
    if not np.isfinite(result[numeric]).all().all():
        raise DataContractError("Predictions contain non-finite values.")
    if (result["fold"] % 1 != 0).any() or (result["horizon"] % 1 != 0).any():
        raise DataContractError("Fold and horizon identifiers must be integers.")
    result["fold"] = result["fold"].astype("int64")
    result["horizon"] = result["horizon"].astype("int64")
    if (result["prediction"] < 0).any() or (require_actual and (result["actual"] < 0).any()):
        raise DataContractError("Predictions and actuals must be non-negative.")
    if result.duplicated(list(PREDICTION_KEYS)).any():
        raise DataContractError("Predictions contain duplicate forecast keys.")
    implied_horizon = (result["date"] - result["cutoff"]).dt.days
    if (result["horizon"] < 1).any() or not implied_horizon.equals(result["horizon"]):
        raise DataContractError("Prediction horizon does not match date minus cutoff.")

    expected_skus = frozenset(result["sku"].unique())
    expected_horizons = frozenset(result["horizon"].unique())
    for fold, rows in result.groupby("fold", sort=True):
        if rows["cutoff"].nunique() != 1:
            raise DataContractError(f"Fold {fold} contains multiple cutoffs.")
        grid = pd.MultiIndex.from_frame(rows[["sku", "horizon"]])
        expected = pd.MultiIndex.from_product([expected_skus, expected_horizons])
        if len(grid) != len(expected) or set(grid) != set(expected):
            raise DataContractError(f"Fold {fold} does not contain a complete SKU/horizon grid.")
    return result.sort_values(list(PREDICTION_KEYS), kind="stable", ignore_index=True)


def _causal_scale_table(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    seasonality_days: int,
) -> pd.DataFrame:
    source = validate_panel(panel)
    records: list[dict[str, object]] = []
    for cutoff in sorted(predictions["cutoff"].unique()):
        normalized_cutoff = pd.Timestamp(cutoff)
        training = source.loc[source["date"] <= normalized_cutoff]
        scales = seasonal_scales(training, seasonality_days=seasonality_days)
        records.extend(
            {"cutoff": normalized_cutoff, "sku": sku, "seasonal_scale": scale}
            for sku, scale in scales.items()
        )
    return pd.DataFrame.from_records(records)


def _attach_scales(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    seasonality_days: int,
) -> pd.DataFrame:
    scales = _causal_scale_table(
        predictions,
        panel,
        seasonality_days=seasonality_days,
    )
    result = predictions.merge(scales, on=["cutoff", "sku"], how="left", validate="many_to_one")
    result["uses_raw_fallback"] = result["seasonal_scale"].isna() | result["seasonal_scale"].le(0)
    return result


def _anchored_quantiles(values: pd.Series, *, alpha: float) -> tuple[float, float]:
    array = values.to_numpy(dtype="float64")
    if array.size == 0 or not np.isfinite(array).all():
        raise DataContractError("Interval calibration requires finite, non-empty residuals.")
    lower = float(np.quantile(array, alpha / 2.0, method=QUANTILE_METHOD))
    upper = float(np.quantile(array, 1.0 - alpha / 2.0, method=QUANTILE_METHOD))
    return min(lower, 0.0), max(upper, 0.0)


def calibrate_intervals(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    config: IntervalConfig | None = None,
) -> IntervalCalibration:
    config = config or IntervalConfig()
    rows = _validated_prediction_rows(predictions, require_actual=True)
    outcomes = validate_panel(panel).rename(columns={"units": "panel_actual"})
    reconciled = rows.merge(
        outcomes[["date", "sku", "panel_actual"]],
        on=["date", "sku"],
        how="left",
        validate="many_to_one",
    )
    if reconciled["panel_actual"].isna().any() or not np.array_equal(
        reconciled["actual"].to_numpy(dtype="float64"),
        reconciled["panel_actual"].to_numpy(dtype="float64"),
    ):
        raise DataContractError("Calibration actuals do not match the source panel.")
    rows = reconciled.drop(columns="panel_actual")
    rows = _attach_scales(
        rows,
        panel,
        seasonality_days=config.seasonality_days,
    )
    raw_residual = rows["actual"] - rows["prediction"]
    valid_scale = ~rows["uses_raw_fallback"]
    rows["scaled_residual"] = np.nan
    rows.loc[valid_scale, "scaled_residual"] = (
        raw_residual.loc[valid_scale] / rows.loc[valid_scale, "seasonal_scale"]
    )
    rows["raw_residual"] = raw_residual
    alpha = 1.0 - config.nominal_coverage
    records: list[dict[str, float | int]] = []
    for horizon, horizon_rows in rows.groupby("horizon", sort=True):
        scaled = horizon_rows["scaled_residual"].dropna()
        if scaled.empty:
            raise DataContractError(
                f"Horizon {horizon} has no rows with an evaluable causal seasonal scale."
            )
        scaled_lower, scaled_upper = _anchored_quantiles(scaled, alpha=alpha)
        raw_lower, raw_upper = _anchored_quantiles(horizon_rows["raw_residual"], alpha=alpha)
        records.append(
            {
                "horizon": int(horizon),
                "scaled_lower": scaled_lower,
                "scaled_upper": scaled_upper,
                "raw_lower": raw_lower,
                "raw_upper": raw_upper,
                "rows": int(len(horizon_rows)),
                "scaled_rows": int(len(scaled)),
                "fallback_rows": int(horizon_rows["uses_raw_fallback"].sum()),
            }
        )
    quantiles = pd.DataFrame.from_records(records)
    return IntervalCalibration(config=config, quantiles=quantiles)


def apply_intervals(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    calibration: IntervalCalibration,
) -> pd.DataFrame:
    rows = _validated_prediction_rows(
        predictions,
        require_actual="actual" in predictions.columns,
    )
    rows = _attach_scales(
        rows,
        panel,
        seasonality_days=calibration.config.seasonality_days,
    )
    quantiles = calibration.quantiles.copy()
    required = set(QUANTILE_COLUMNS)
    if required.difference(quantiles.columns) or quantiles.duplicated("horizon").any():
        raise DataContractError("Interval calibration quantiles are invalid.")
    rows = rows.merge(
        quantiles[list(QUANTILE_COLUMNS)],
        on="horizon",
        how="left",
        validate="many_to_one",
    )
    if rows[list(required - {"horizon"})].isna().any().any():
        raise DataContractError("A prediction horizon is absent from interval calibration.")

    fallback = rows["uses_raw_fallback"]
    lower_offset = np.where(
        fallback,
        rows["raw_lower"],
        rows["seasonal_scale"] * rows["scaled_lower"],
    )
    upper_offset = np.where(
        fallback,
        rows["raw_upper"],
        rows["seasonal_scale"] * rows["scaled_upper"],
    )
    rows["lower"] = np.maximum(0.0, rows["prediction"] + lower_offset)
    rows["upper"] = np.maximum(rows["lower"], rows["prediction"] + upper_offset)
    if not np.isfinite(rows[["lower", "upper"]]).all().all():
        raise DataContractError("Interval bounds are non-finite.")
    if not (
        rows["lower"].le(rows["prediction"]).all() and rows["prediction"].le(rows["upper"]).all()
    ):
        raise DataContractError("Anchored intervals must contain the point forecast.")
    drop_columns = [
        "scaled_lower",
        "scaled_upper",
        "raw_lower",
        "raw_upper",
    ]
    return rows.drop(columns=drop_columns).sort_values(
        list(PREDICTION_KEYS), kind="stable", ignore_index=True
    )
