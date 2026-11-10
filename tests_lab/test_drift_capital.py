"""drift_capital: sizes are charged at their own size and a ranking has to beat luck."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import drift_capital as dc
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import earnings_drift_variants as edv


def _calls(rows: list[tuple[str, int, int, float]], marked: bool = False) -> pd.DataFrame:
    first = pd.Timestamp("2020-01-01")
    return pd.DataFrame({
        "code": [r[0] for r in rows], "entry_i": [r[1] for r in rows],
        "exit_i": [r[2] for r in rows], "inr": [r[3] for r in rows],
        "net": [r[3] / 10_000 for r in rows], "marked": [marked] * len(rows),
        "exit_day": [first + pd.Timedelta(days=200 * r[2]) for r in rows],
        "entry_day": [first + pd.Timedelta(days=200 * r[1]) for r in rows],
    })  # fmt: skip


def test_a_position_is_charged_at_its_own_size() -> None:
    calls = pd.DataFrame({"net": [0.05 - dt.RT, -0.02 - dt.RT], "entry_price": [100.0, 400.0]})
    got = dc.rupees_at(calls, 20_000.0, lambda entry, exit_px: 0.004)
    assert got == pytest.approx([20_000 * (0.05 - 0.004), 20_000 * (-0.02 - 0.004)])


def test_the_real_charge_falls_as_the_position_grows() -> None:
    small, large = dc.charge(10_000.0)(500.0, 550.0), dc.charge(25_000.0)(500.0, 550.0)
    assert small == pytest.approx(dt.RT, abs=0.0006)  # the live record's flat 0.564%
    assert large < small - 0.0005


def test_a_book_reports_halves_fall_and_years() -> None:
    rows = [("A", 1, 2, 500.0), ("B", 1, 3, -800.0), ("C", 4, 5, 300.0), ("D", 4, 6, 900.0)]
    calls = _calls(rows)
    got = dc.book_result(calls, slots=10, split=4)
    assert (got["rupees"], got["profit"], got["loss"]) == (900.0, 1700.0, -800.0)
    assert (got["first_half"], got["second_half"]) == (-300.0, 1200.0)
    assert got["worst_fall"] == 800.0
    assert got["worst_trade"] == -800.0
    assert (got["good_years"], got["years"]) == (2, 3)  # A and B both close in 2021


def test_fewer_slots_take_fewer_calls() -> None:
    calls = _calls([("A", 1, 9, 100.0), ("B", 1, 9, 100.0), ("C", 2, 9, 100.0)])
    assert dc.book_result(calls, slots=2, split=5)["trades"] == 2


def test_random_order_only_reorders_calls_of_one_session() -> None:
    calls = _calls([("A", 1, 9, 100.0), ("B", 1, 9, -100.0), ("C", 5, 9, 300.0)])
    runs = dc.random_order(calls, slots=1, split=5, shuffles=40)
    assert {r["rupees"] for r in runs} == {100.0, -100.0}  # C always finds the slot taken
    assert runs == dc.random_order(calls, slots=1, split=5, shuffles=40)


def _size(rupees: float, fall: float, first: float, second: float) -> dict[str, float]:
    return {"rupees": rupees, "worst_fall": fall, "first_half": first, "second_half": second}


def test_a_size_needs_more_rupees_a_similar_fall_and_both_halves() -> None:
    live = _size(100.0, 40.0, 50.0, 50.0)
    assert dc.judge_size(_size(115.0, 48.0, 55.0, 60.0), live)["adopted"]
    assert dc.judge_size(_size(105.0, 40.0, 52.0, 53.0), live)["fails"] == ["under 10% more rupees"]
    deep = dc.judge_size(_size(130.0, 60.0, 60.0, 70.0), live)
    assert deep["fails"] == ["falls more than 1.25 times as far"]
    lopsided = dc.judge_size(_size(130.0, 40.0, 40.0, 90.0), live)
    assert lopsided["fails"] == ["not ahead in both halves"]


def _measured(better_top: bool) -> pd.DataFrame:
    rows = []
    for week in range(40):
        day = pd.Timestamp("2021-01-04") + pd.Timedelta(weeks=week)
        for k in range(4):
            high = k >= 2
            net = (0.03 if high == better_top else 0.0) + 0.001 * ((week + k) % 3)
            rows.append({"entry_day": day, "entry_i": week * 5, "marked": False,
                         "reaction": 0.10 if high else 0.06, "net": net})  # fmt: skip
    return pd.DataFrame(rows)


def test_the_top_half_of_a_measure_is_compared_week_by_week() -> None:
    got = dc.top_against_bottom(_measured(True), "reaction", split=100)
    assert got["weeks"] == 40
    assert got["difference"] == pytest.approx(0.03, abs=0.002)
    assert got["t"] > 10
    assert all(h > 0.02 for h in got["halves"])
    assert dc.top_against_bottom(_measured(False), "reaction", split=100)["difference"] < 0


def test_a_ranking_must_work_per_call_and_beat_luck() -> None:
    runs = [{"rupees": float(r), "worst_fall": 100.0} for r in range(100)]
    strong = {"difference": 0.02, "t": 3.1, "halves": [0.01, 0.02]}
    good = dc.judge_ranking(strong, {"rupees": 98.5, "worst_fall": 110.0}, runs)
    assert good["adopted"] and good["beats_share"] == 0.99
    lucky = dc.judge_ranking(strong, {"rupees": 80.0, "worst_fall": 110.0}, runs)
    assert lucky["fails"] == ["beats only 80% of random-order books"]
    weak = {"difference": 0.02, "t": 1.2, "halves": [0.03, -0.01]}
    got = dc.judge_ranking(weak, {"rupees": 98.5, "worst_fall": 200.0}, runs)
    assert got["fails"] == ["per call t 1.20", "top half not ahead in both halves of history",
                            "falls more than 1.25 times as far"]  # fmt: skip


def test_the_measures_cover_exactly_the_live_rules_reactions() -> None:
    days = pd.date_range("2022-01-03", periods=70, freq="B")
    close = pd.DataFrame(100.0, index=days, columns=["X", "Y", *[f"F{i}" for i in range(8)]])
    volume = pd.DataFrame(3_000_000.0, index=days, columns=close.columns)
    close.loc[days[30]:, "X"] = 108.0  # results on day 29, an 8% reaction the next session
    volume.loc[days[30], "X"] = 9_000_000.0
    close.loc[days[30]:, "Y"] = 103.0  # too small a reaction
    events = pd.DataFrame({"symbol": ["XS", "YS"], "event_date": [days[29], days[29]]})
    p = {"close": close, "volume": volume, "symbols": {"XS": "X", "YS": "Y"}, "events": events}
    got = dc.call_measures(p)
    live = edv.events(p)
    assert list(got["code"]) == list(live["code"]) == ["X"]
    assert list(got["entry_i"]) == [int(live["i1"].iloc[0]) + 1]
    row = got.iloc[0]
    assert row["volume_multiple"] == pytest.approx(3.0)
    assert row["reaction"] == pytest.approx(0.08 - (0.08 + 0.03) / 10)  # over the ten-stock market
    assert row["turnover"] == pytest.approx(3e8)
    assert np.isfinite(row[list(dc.RANKINGS)].to_numpy(float)).all()
