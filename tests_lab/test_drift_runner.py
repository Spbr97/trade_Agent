"""drift_runner: each exit must sell where it says, using only prices up to that session."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest
from tradedesk_lab import drift_runner as dr


def test_the_target_sells_at_its_level_or_a_gap_past_it() -> None:
    opens = [100.0, 101.0, 115.0, 100.0]
    highs = [103.0, 104.0, 116.0, 101.0]
    closes = [102.0, 103.0, 114.0, 100.0]
    assert dr.target_exit(opens, highs, closes, 0, 4) == (2, 115.0)  # gapped past 110
    row, price = dr.target_exit(opens, [103.0, 111.0, 116.0, 101.0], closes, 0, 4)
    assert (row, price) == (1, pytest.approx(110.0))


def test_with_no_target_hit_it_sells_at_the_last_close() -> None:
    flat = [100.0] * 20
    assert dr.target_exit(flat, flat, flat, 0, 20) == (19, 100.0)
    assert dr.target_exit(flat, flat, flat, 5, 20) is None  # history stops first


def test_the_trail_follows_the_highest_close() -> None:
    closes = [100.0, 120.0, 109.0, 108.0] + [150.0] * 60
    assert dr.trail_exit(closes, 0, 0.10) == (3, 108.0)  # 108 is 10% under 120, 109 is not
    assert dr.trail_exit(closes, 0, 0.15) == (59, 150.0)


def test_the_trail_never_uses_a_close_before_the_entry() -> None:
    closes = [500.0, 100.0, 101.0] + [101.0] * 60
    assert dr.trail_exit(closes, 1, 0.10) == (60, 101.0)


def test_the_trail_steps_over_a_missing_close() -> None:
    closes = [100.0, math.nan, 89.0] + [89.0] * 60
    assert dr.trail_exit(closes, 0, 0.10) == (2, 89.0)


def test_the_first_close_rule_sells_a_trade_that_is_not_working() -> None:
    opens, highs = [100.0] * 70, [100.0] * 70
    assert dr.legs("first_close", opens, highs, [99.0] * 70, 0) == [(0, 99.0, 1.0, True)]
    held = dr.legs("first_close", opens, highs, [101.0] * 70, 0)
    assert held == [(59, 101.0, 1.0, False)]


def test_the_half_runner_has_two_halves_that_can_end_apart() -> None:
    opens = [100.0] * 70
    highs = [100.0, 111.0] + [125.0] * 68
    closes = [100.0, 110.0, 120.0, 107.0] + [130.0] * 66
    assert dr.legs("half_runner", opens, highs, closes, 0) == [
        (1, pytest.approx(110.0), 0.5, False),
        (3, 107.0, 0.5, False),
    ]


def test_every_rule_needs_enough_history() -> None:
    short = [100.0] * 30
    for rule in dr.EXITS:
        assert dr.legs(rule, short, short, short, 0) is None


def test_the_live_rule_is_the_ten_percent_target_or_twentieth_close() -> None:
    flat = [100.0] * 25
    assert dr.legs("live", flat, flat, flat, 0) == [(19, 100.0, 1.0, False)]


def _calls(rows: list[tuple[int, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["e", "x", "net"])


def test_the_book_refuses_calls_when_every_slot_is_busy() -> None:
    rows = [(0, 100, 0.01)] * 10 + [(1, 5, 0.50)]
    got = dr.book(_calls(rows), 1.0)
    assert got["taken"] == 10
    assert got["rupees_a_year"] == pytest.approx(10 * 0.01 * 10_000)


def test_a_slot_frees_only_after_its_exit_session() -> None:
    rows = [(0, 3, 0.01)] * 10 + [(3, 9, 0.01), (4, 9, 0.01)]
    assert dr.book(_calls(rows), 1.0)["taken"] == 11  # on day 3 every slot was still busy


def test_the_worst_fall_counts_from_the_highest_closed_equity() -> None:
    rows = [(0, 1, 0.10), (2, 3, -0.05), (4, 5, -0.03), (6, 7, 0.20)]
    assert dr.book(_calls(rows), 1.0)["worst_fall"] == pytest.approx(-800.0)


def _frame(excess: list[float], net: float, x: int, start: date) -> pd.DataFrame:
    days = [start + timedelta(days=7 * i) for i in range(len(excess))]
    n = range(len(excess))
    return pd.DataFrame({
        "id": [str(i) for i in n], "day": days, "e": [10 * i for i in n],
        "x": [10 * i + x for i in n], "net": net, "excess": excess, "days": x + 1,
    })  # fmt: skip


def test_a_longer_exit_that_only_earns_the_market_does_not_pass() -> None:
    start = date(2020, 1, 6)
    live = _frame([0.01, 0.012] * 60, 0.01, 5, start)
    # More rupees from holding, but no better than the live exit over the market.
    longer = _frame([0.01, 0.012] * 60, 0.03, 5, start)
    got = dr.judge(longer, live, 2.0)
    assert not got["passed"]
    assert any("over the market" in f for f in got["fails"])


def test_a_real_improvement_passes() -> None:
    start = date(2020, 1, 6)
    live = _frame([0.01, 0.012] * 60, 0.01, 5, start)
    better = _frame([0.02, 0.025] * 60, 0.02, 5, start)
    got = dr.judge(better, live, 2.0)
    assert got["passed"], got["fails"]


def test_an_exit_with_too_few_rupees_fails_even_with_a_better_excess() -> None:
    start = date(2020, 1, 6)
    live = _frame([0.01, 0.012] * 60, 0.01, 5, start)
    fewer = _frame([0.02, 0.025] * 60, 0.011, 5, start)
    got = dr.judge(fewer, live, 2.0)
    assert any("1.2x" in f for f in got["fails"])
