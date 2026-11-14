"""drift_survivors: renames, added panels, excess over the market and the pre-registered bar."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from tradedesk_lab import drift_survivors as ds

CHANGES = [("OLDA", "NEWA", date(2020, 1, 10))]


def test_canonical_names_renames_only_rows_before_the_change() -> None:
    frame = pd.DataFrame({
        "symbol": ["OLDA", "OLDA", "KEEP"],
        "day": pd.to_datetime(["2019-06-03", "2021-06-03", "2019-06-03"]),
    })  # fmt: skip
    assert ds.canonical_names(frame, CHANGES).tolist() == ["NEWA", "OLDA", "KEEP"]


def test_canonical_actions_follow_the_company() -> None:
    got = ds.canonical_actions({"OLDA": {date(2019, 3, 1): 0.5}, "X": {date(2019, 3, 1): 0.2}},
                               CHANGES)  # fmt: skip
    assert got == {"NEWA": {date(2019, 3, 1): 0.5}, "X": {date(2019, 3, 1): 0.2}}


def _rows() -> pd.DataFrame:
    days = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    return pd.DataFrame({
        "name": ["GONE", "GONE", "SPLIT", "SPLIT", "SPLIT"],
        "day": [days[0], days[1], days[0], days[1], days[2]],
        "series": ["EQ"] * 5, "open": [10.0, 11, 100, 101, 50], "high": [10.5, 11.5, 102, 103, 52],
        "low": [9.5, 10.5, 99, 100, 49], "close": [10.2, 11.2, 101, 100, 51],
        "prevclose": np.nan, "volume": [100.0, 200, 1000, 1000, 2000],
    })  # fmt: skip


def test_added_panels_adjust_carry_the_last_close_and_stay_aligned() -> None:
    index = pd.DatetimeIndex(pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06",
                                             "2021-01-07"]))  # fmt: skip
    panels, info = ds.added_panels(_rows(), {"GONE", "SPLIT"}, index,
                                   {"SPLIT": {date(2021, 1, 6): 0.5}})  # fmt: skip
    cols = ["BHAV_GONE", "BHAV_SPLIT"]
    for k in ("open", "high", "close", "volume"):
        assert list(panels[k].columns) == cols and list(panels[k].index) == list(index)
    assert panels["close"]["BHAV_GONE"].tolist() == [10.2, 11.2, 11.2, 11.2]
    assert np.isnan(panels["open"].at[index[2], "BHAV_GONE"])
    assert panels["close"]["BHAV_SPLIT"].tolist() == [50.5, 50.0, 51.0, 51.0]
    assert panels["volume"]["BHAV_SPLIT"].tolist()[:3] == [2000.0, 2000.0, 2000.0]
    assert info["traded"]["BHAV_GONE"].tolist() == [True, True, False, False]
    assert list(info["adjusted"]) == ["SPLIT"]


def test_excess_is_net_minus_the_market_from_reaction_close_to_exit() -> None:
    calls = pd.DataFrame({"entry_i": [2], "exit_i": [4], "net": [0.05]})
    index = np.array([1.0, 1.0, 1.0, 1.01, 1.02])  # reaction close at 1, exit close at 4
    assert ds.with_excess(calls, index)["excess"].round(6).tolist() == [0.03]


def test_t_by_week_averages_within_a_week_first() -> None:
    days = pd.Series(pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-11", "2021-01-18"]))
    values = pd.Series([0.1, 0.3, 0.1, 0.3])
    weekly = np.array([0.2, 0.1, 0.3])
    expected = weekly.mean() / (weekly.std(ddof=1) / np.sqrt(3))
    assert abs(ds.t_by_week(values, days) - expected) < 1e-9
    assert ds.t_by_week(values[:1], days[:1]) is None


def _summary(**kw: float) -> dict[str, float]:
    base = {"mean_net": 0.01, "mean_excess": 0.005, "t_excess_weeks": 2.5,
            "first_half_excess": 0.004, "second_half_excess": 0.006}  # fmt: skip
    return {**base, **kw}


def test_judge_needs_every_part() -> None:
    book = {"profit_inr": 300, "loss_inr": -200}
    assert ds.judge(_summary(), book)["passed"]
    for bad in ({"mean_net": -0.001}, {"t_excess_weeks": 1.9}, {"second_half_excess": -0.001},
                {"mean_excess": -0.001}, {"t_excess_weeks": None}):  # fmt: skip
        assert not ds.judge(_summary(**bad), book)["passed"]
    assert not ds.judge(_summary(), {"profit_inr": 200, "loss_inr": -200})["passed"]


def test_summary_splits_halves_by_entry_session() -> None:
    sessions = pd.DatetimeIndex(pd.date_range("2021-01-04", periods=10, freq="B"))
    calls = pd.DataFrame({"entry_i": [5, 1, 7, 3], "net": [0.02, -0.01, 0.03, 0.0],
                          "excess": [0.01, -0.02, 0.02, -0.01]})  # fmt: skip
    got = ds.summary(calls, sessions)
    assert got["calls"] == 4 and got["first_half_excess"] == -0.015
    assert got["second_half_excess"] == 0.015 and got["split_day"] == "2021-01-11"
    assert got["by_year"]["2021"]["calls"] == 4
