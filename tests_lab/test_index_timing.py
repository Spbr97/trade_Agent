"""index_timing: each rule must trade what it says and use only what it knows."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import index_timing as it


def _bars(first_move: float, last_move: float, day: date = date(2025, 1, 6)) -> pd.DataFrame:
    rows = []
    for bar in range(25):
        rows.append({"day": day, "bar": bar, "open": 100.0, "close": 100.0})
    rows[1]["close"] = 100.0 * (1 + first_move)
    rows[24]["close"] = 100.0 * (1 + last_move)
    return pd.DataFrame(rows)


def test_the_first_and_last_half_hours_are_measured_from_the_right_bars() -> None:
    (row,) = it.intraday_days(_bars(0.01, 0.005)).to_dict("records")
    assert row["first"] == pytest.approx(0.01)
    assert row["last"] == pytest.approx(0.005)


def _days(first: list[float], last: list[float]) -> pd.DataFrame:
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(len(first))]
    return pd.DataFrame({"day": days, "first": first, "last": last})


def test_momentum_follows_the_first_half_hours_sign() -> None:
    got = it.momentum_trades(_days([0.01, -0.01, 0.0], [0.005, 0.005, 0.005]))
    assert list(got["net"]) == pytest.approx([0.005 - it.RT_COST, -0.005 - it.RT_COST])


def test_the_big_filter_uses_only_earlier_days() -> None:
    first = [0.001] * 70 + [0.01]
    frame = _days(first, [0.0] * 71)
    assert len(it.momentum_trades(frame, big_only=True)) >= 1
    # A big move later must not change which earlier days qualified.
    longer = _days([*first, 1.0], [0.0] * 72)
    a = it.momentum_trades(frame, big_only=True)["day"].tolist()
    b = it.momentum_trades(longer, big_only=True)["day"].tolist()
    assert b[: len(a)] == a


def _daily(opens: list[float], closes: list[float]) -> pd.DataFrame:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(opens))]
    return pd.DataFrame({"open": opens, "close": closes}, index=days)


def test_overnight_runs_from_a_close_to_the_next_open() -> None:
    got = it.overnight_trades(_daily([100.0, 102.0], [100.0, 90.0]))
    assert list(got["net"]) == pytest.approx([0.02 - it.RT_COST - it.NIGHT_CARRY])


def test_a_day_short_gains_when_the_session_falls() -> None:
    got = it.day_short_trades(_daily([100.0], [99.0]))
    assert list(got["net"]) == pytest.approx([0.01 - it.RT_COST])


def test_a_calm_night_uses_the_vix_known_at_the_prior_close() -> None:
    n = 260
    daily = _daily([100.0] * n, [100.0] * n)
    vix = pd.Series([20.0] * 255 + [10.0, 20.0, 20.0, 20.0, 20.0], index=daily.index)
    got = it.calm_nights(daily, vix)
    # VIX fell on row 255; the night that begins at that close ends on row 256's morning.
    assert list(got["day"]) == [daily.index[256]]


def _frame(net: list[float]) -> pd.DataFrame:
    days = [date(2017, 1, 2) + timedelta(days=9 * i) for i in range(len(net))]
    return pd.DataFrame({"day": days, "net": net})


def test_a_steady_edge_passes() -> None:
    assert it.judge(_frame(list(np.resize([0.001, 0.002], 400))))["passed"]


def test_too_few_trades_or_a_fading_edge_fail() -> None:
    assert not it.judge(_frame(list(np.resize([0.001, 0.002], 100))))["passed"]
    fading = [0.002, 0.003] * 100 + [-0.0001, -0.0002] * 100
    assert any("both halves" in f for f in it.judge(_frame(fading))["fails"])


def test_before_costs_is_reported() -> None:
    got = it.judge(_frame([0.001] * 2 + [0.002] * 2))
    assert got["before_costs"] == pytest.approx(0.0015 + it.RT_COST)
