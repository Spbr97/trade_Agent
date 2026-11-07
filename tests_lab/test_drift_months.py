"""drift_months: the book holds what real slots could hold, and the pause does not look ahead."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import drift_months as dm
from tradedesk_lab import drift_timing as dt


def _calls(rows: list[tuple[str, int, int, float, bool]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["code", "entry_i", "exit_i", "net", "marked"])
    first = datetime(2024, 1, 1)
    frame["exit_day"] = [first + timedelta(days=int(i)) for i in frame["exit_i"]]
    return frame


def test_the_exit_matches_the_live_rules_function() -> None:
    opens = np.array([[100.0], [101.0], [102.0], [130.0], [103.0]])
    highs = np.array([[101.0], [102.0], [103.0], [131.0], [104.0]])
    closes = np.array([[100.0], [101.0], [102.0], [125.0], [103.0]])
    at, net = dm.exit_of(opens, highs, closes, 0, 1, 4)
    assert at == 3
    assert net == pytest.approx(dt.trade(opens, highs, closes, 0, 101.0, 1, 4))
    assert net == pytest.approx(130.0 / 101.0 - 1 - dt.RT)  # a gap past the target: the open


def test_a_trade_with_no_target_exits_at_the_last_close() -> None:
    flat = np.full((4, 1), 100.0)
    at, net = dm.exit_of(flat, flat, flat, 0, 1, 3)
    assert at == 3
    assert net == pytest.approx(-dt.RT)


def test_the_book_respects_slots_marks_and_stocks_already_held() -> None:
    calls = _calls([
        ("A", 1, 5, 0.01, False), ("B", 1, 5, 0.01, True), ("A", 2, 6, 0.01, False),
        ("C", 2, 6, 0.01, False), ("D", 3, 7, 0.01, False),
    ])  # fmt: skip
    assert list(dm.book(calls, slots=2)["code"]) == ["A", "C"]


def test_a_slot_stays_held_on_its_exit_session() -> None:
    calls = _calls([("A", 1, 3, 0.01, False), ("B", 3, 5, 0.01, False), ("C", 4, 6, 0.01, False)])
    assert list(dm.book(calls, slots=1)["code"]) == ["A", "C"]


def test_the_pause_sees_only_exits_before_the_entry() -> None:
    calls = _calls([("A", 1, 5, -0.05, False), ("B", 5, 8, 0.01, False), ("C", 6, 9, 0.01, False)])
    got = dm.paused(calls, sessions=12, window=60)
    # B enters on the session A exits, and A's close is not known at B's open.
    assert list(got) == [False, False, True]


def test_the_pause_forgets_old_losses_and_ignores_marked_calls() -> None:
    old = _calls([("A", 1, 2, -0.05, False), ("B", 70, 72, 0.01, False)])
    assert list(dm.paused(old, sessions=80, window=60)) == [False, False]
    marked = _calls([("A", 1, 2, -0.05, True), ("B", 5, 8, 0.01, False)])
    assert list(dm.paused(marked, sessions=12, window=60)) == [False, False]


def test_periods_keep_profit_and_loss_apart() -> None:
    trades = pd.DataFrame({
        "net": [0.10, -0.03, 0.02],
        "exit_day": pd.to_datetime(["2024-01-05", "2024-01-20", "2024-02-02"]),
    })  # fmt: skip
    got = dm.periods(trades, "M")
    assert got["profit"].tolist() == pytest.approx([1000.0, 200.0])
    assert got["loss"].tolist() == pytest.approx([-300.0, 0.0])
    assert got["net"].tolist() == pytest.approx([700.0, 200.0])


def test_the_fall_and_the_wait_are_measured_from_the_peak() -> None:
    trades = pd.DataFrame({"exit_i": [1, 5, 9, 20], "net": [0.10, -0.03, -0.02, 0.10]})
    got = dm.drawdown(trades)
    assert got["worst_inr"] == pytest.approx(-500.0)
    assert got["longest_sessions"] == 8


def _year_trades(nets: list[float], year: int = 2024) -> pd.DataFrame:
    days = pd.date_range(f"{year}-01-15", periods=len(nets), freq="MS") + pd.Timedelta(days=14)
    return pd.DataFrame({"net": nets, "exit_day": days})


def test_one_losing_full_year_fails_the_period_bar() -> None:
    good = _year_trades([0.05] * 12, 2023)
    bad = _year_trades([-0.05], 2024)
    got = dm.period_bar(pd.concat([good, bad]), final_year=2025)
    assert "full years" in got["fails"]
    assert dm.period_bar(good, final_year=2025)["passed"]


def _pause_frame(nets: list[float], first_exit: int = 1) -> pd.DataFrame:
    days = pd.date_range("2020-01-15", periods=len(nets), freq="QS") + pd.Timedelta(days=20)
    return pd.DataFrame({"net": nets, "exit_i": range(first_exit, first_exit + len(nets)),
                         "exit_day": days})  # fmt: skip


def test_a_pause_that_only_costs_money_is_not_adopted() -> None:
    base = _pause_frame([0.5, -0.1, 0.5, -0.1, 0.5, -0.1, 0.5, -0.1])
    worse = _pause_frame([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    got = dm.judge_pause(base, worse, pd.Period("2021Q1", "Q"))
    assert not got["adopted"]
    assert "rupees fell by more than 10%" in got["fails"]


def test_a_pause_meeting_all_three_tests_is_adopted() -> None:
    base = _pause_frame([0.5, -0.2, 0.5, -0.2, 0.5, -0.2, 0.5, -0.2])
    better = _pause_frame([0.5, -0.1, 0.5, 0.0001, 0.5, -0.1, 0.5, 0.0001])
    got = dm.judge_pause(base, better, pd.Period("2021Q2", "Q"))
    assert got["adopted"], got["fails"]
