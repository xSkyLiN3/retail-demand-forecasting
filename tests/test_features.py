from __future__ import annotations

import pandas as pd
import pytest

from retail_forecasting.dataset import DataContractError
from retail_forecasting.features import FEATURE_COLUMNS, build_supervised_table


def _panel(days: int = 100) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for day_index, day in enumerate(pd.date_range("2020-01-01", periods=days, freq="D")):
        records.extend(
            [
                {"date": day, "sku": "A", "units": day_index + 1},
                {"date": day, "sku": "B", "units": 1_000 + 10 * day_index},
            ]
        )
    return pd.DataFrame(records)


def _rows_at_origin(table: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    return table.loc[table["origin_date"].eq(origin)].sort_values(
        ["sku", "horizon"], ignore_index=True
    )


def test_supervised_rows_align_origin_target_date_and_horizon() -> None:
    panel = _panel()
    target_end = pd.Timestamp("2020-03-25")

    table = build_supervised_table(panel, max_target_date=target_end)

    implied_horizon = (table["target_date"] - table["origin_date"]).dt.days
    assert implied_horizon.equals(table["horizon"])
    assert table["horizon"].between(1, 14, inclusive="both").all()

    actual_lookup = panel.set_index(["date", "sku"])["units"]
    expected_actual = [
        actual_lookup.loc[(row.target_date, row.sku)] for row in table.itertuples(index=False)
    ]
    assert table["actual"].tolist() == expected_actual


def test_features_are_causal_and_do_not_mix_skus() -> None:
    panel = _panel()
    origin = pd.Timestamp("2020-03-01")
    table = build_supervised_table(panel, max_target_date=pd.Timestamp("2020-03-15"))

    rows = _rows_at_origin(table, origin)
    a_h1 = rows.loc[(rows["sku"] == "A") & (rows["horizon"] == 1)].iloc[0]
    b_h1 = rows.loc[(rows["sku"] == "B") & (rows["horizon"] == 1)].iloc[0]

    # March 1 is day index 60. All values below must come from the same SKU
    # on or before that origin.
    assert a_h1["last_units"] == 61.0
    assert a_h1["origin_lag_1"] == 60.0
    assert a_h1["origin_lag_7"] == 54.0
    assert a_h1["rolling_mean_7"] == 58.0
    assert a_h1["baseline_prediction"] == 55.0
    assert a_h1["target_lag_14"] == 48.0

    assert b_h1["last_units"] == 1_600.0
    assert b_h1["origin_lag_1"] == 1_590.0
    assert b_h1["origin_lag_7"] == 1_530.0
    assert b_h1["rolling_mean_7"] == 1_570.0
    assert b_h1["baseline_prediction"] == 1_540.0
    assert b_h1["target_lag_14"] == 1_470.0


def test_mutating_outcomes_after_origin_does_not_change_origin_features() -> None:
    panel = _panel()
    origin = pd.Timestamp("2020-03-05")
    target_end = pd.Timestamp("2020-03-19")
    original = build_supervised_table(panel, max_target_date=target_end)

    mutated_panel = panel.copy()
    mutated_panel.loc[mutated_panel["date"] > origin, "units"] += 1_000_000
    mutated = build_supervised_table(mutated_panel, max_target_date=target_end)

    original_rows = _rows_at_origin(original, origin)
    mutated_rows = _rows_at_origin(mutated, origin)
    pd.testing.assert_frame_equal(
        original_rows.loc[:, ["sku", "horizon", *FEATURE_COLUMNS]],
        mutated_rows.loc[:, ["sku", "horizon", *FEATURE_COLUMNS]],
    )
    assert not original_rows["actual"].equals(mutated_rows["actual"])


def test_max_target_date_excludes_all_later_labels() -> None:
    panel = _panel()
    target_end = pd.Timestamp("2020-03-10")

    table = build_supervised_table(panel, max_target_date=target_end)

    assert not table.empty
    assert table["target_date"].max() == target_end
    assert table["target_date"].le(target_end).all()
    assert table["origin_date"].lt(table["target_date"]).all()


def test_history_must_cover_the_fixed_feature_lookback() -> None:
    with pytest.raises(ValueError, match="shorter than the fixed feature lookback"):
        build_supervised_table(
            _panel(),
            max_target_date=pd.Timestamp("2020-03-15"),
            history_days=55,
        )


def test_panel_must_have_history_plus_at_least_one_labeled_day() -> None:
    panel = _panel(days=56)

    with pytest.raises(DataContractError, match="history|supervised|training"):
        build_supervised_table(panel, max_target_date=panel["date"].max())


def test_nan_panel_values_are_rejected() -> None:
    panel = _panel()
    panel.loc[10, "units"] = float("nan")

    with pytest.raises(DataContractError, match="missing or invalid"):
        build_supervised_table(panel, max_target_date=pd.Timestamp("2020-03-15"))


def test_nan_max_target_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_target_date"):
        build_supervised_table(_panel(), max_target_date=pd.NaT)
