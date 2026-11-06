"""convergence_checks: the attacks themselves must be fair."""

from __future__ import annotations

import math
import random
from datetime import date

import pandas as pd
import pytest
from tradedesk_lab import convergence as cv
from tradedesk_lab import convergence_checks as ck


def test_the_shuffle_keeps_the_fund_bulk_dates_and_moves_the_others() -> None:
    events = [("S", 500, "fund_bulk"), ("S", 500, "both_up"), ("S", 500, "loud_strong")]
    got = ck.shuffled(events, random.Random(1), 2000)
    assert ("S", 500, "fund_bulk") in got
    moved = [e for _, e, k in got if k != "fund_bulk"]
    assert all(60 <= abs(e - 500) <= 250 for e in moved)


def test_the_shuffle_keeps_a_kinds_spacing_within_a_stock() -> None:
    events = [("S", 500, "both_up"), ("S", 563, "both_up")]
    (_, a, _), (_, b, _) = ck.shuffled(events, random.Random(3), 2000)
    assert b - a == 63


def test_the_shuffle_drops_what_falls_outside_the_data() -> None:
    events = [("S", 2, "both_up")] * 1 + [("S", 1999, "loud_strong")]
    got = ck.shuffled(events, random.Random(5), 2000)
    assert all(1 <= e <= 2000 for _, e, _ in got)


def test_a_true_pair_usually_stops_agreeing_after_the_shuffle() -> None:
    events = [("S", 500, "fund_bulk"), ("S", 505, "both_up")]
    rng = random.Random(7)
    agreeing = sum(bool(cv.convergent(ck.shuffled(events, rng, 2000), 2, 60)) for _ in range(200))
    assert agreeing < 10


def _calls(quarter_means: list[float]) -> pd.DataFrame:
    days = [date(2022 + i // 4, 3 * (i % 4) + 1, 15) for i in range(len(quarter_means))]
    return pd.DataFrame({"day": days, "excess": quarter_means})


def test_quarter_t_counts_a_quarter_as_one_unit() -> None:
    frame = pd.concat([_calls([0.01, 0.03, 0.02, 0.04])] * 50)
    mean, t, n = ck.quarter_t(frame)
    assert n == 4
    assert mean == pytest.approx(0.025)
    assert t == pytest.approx(0.025 / (0.012910 / 2), rel=1e-3)  # not inflated by the 200 rows


def test_quarter_t_needs_two_quarters() -> None:
    assert math.isnan(ck.quarter_t(_calls([0.01]))[1])


def test_without_best_removes_the_winners_only() -> None:
    assert ck.without_best([1, 2, 3, 100, 200], 2) == pytest.approx(2.0)
    assert math.isnan(ck.without_best([1, 2], 5))


def test_the_kinds_at_a_call_are_those_inside_its_window() -> None:
    events = [("S", 10, "fund_bulk"), ("S", 40, "both_up"), ("S", 200, "loud_strong")]
    assert ck.kinds_at_call([("S", 40)], events) == {("S", 40): "both_up+fund_bulk"}
