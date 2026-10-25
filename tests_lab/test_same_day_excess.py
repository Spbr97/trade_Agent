"""Same-day excess evidence (tradedesk_lab/same_day_excess.py): each call counted once,
scored only once matured, against random stocks of the SAME day (never itself), with the
day as the unit and profit_gate's own confidence sequence deciding the verdict."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from tradedesk_lab.same_day_excess import (
    HORIZON,
    Call,
    Universe,
    dedupe_across_markets,
    distinct_live_calls,
    evaluate,
    stock_days,
)

DAYS = np.arange(np.datetime64("2026-01-01"), np.datetime64("2026-01-01") + 120)


def _stock(drift: float, *, vol: float = 1e6, price: float = 100.0):
    c = price * np.cumprod(np.full(len(DAYS), 1 + drift))
    o = np.r_[c[0], c[:-1]]
    return stock_days(DAYS, o, np.maximum(o, c) * 1.01, np.minimum(o, c) * 0.99, c,
                      np.full(len(DAYS), vol))  # fmt: skip


def _universe(winner_drift: float = 0.0) -> Universe:
    stocks = {f"P{i}": _stock(0.0) for i in range(30)}
    stocks["W"] = _stock(winner_drift)
    return Universe(stocks)


def _row(**kw):
    base = {"source": "live", "contract_version": "swing-v1", "setup": "s", "symbol": "W",
            "scrip_code": "W", "armed_on": "2026-02-10", "entry": 100.0, "stop": 95.0,
            "rejected_for": []}  # fmt: skip
    return base | kw


def test_each_call_counts_once_and_atr_band_rejects_are_dropped():
    rows = [
        _row(),
        _row(contract_version="quick-profit-v1"),  # the same call, another contract
        _row(contract_version="legacy-t1-tracker-v1"),  # same key: counted once
        _row(symbol="LIQUIDBEES", scrip_code="L", rejected_for=["ATR 0.1% outside 1.5-5.0%"]),
        _row(source="backfill", symbol="Z"),
    ]
    calls = distinct_live_calls(rows, "nse")
    assert [(c.symbol, c.armed_on) for c in calls] == [("W", date(2026, 2, 10))]


def test_same_company_on_both_exchanges_is_one_bet():
    a = Call("s", "nse", "NSE_1", "ABC", date(2026, 2, 10), 100, 95)
    b = Call("s", "bse", "BSE_9", "ABC", date(2026, 2, 10), 100, 95)
    assert dedupe_across_markets([b, a]) == [a]


def test_excess_is_zero_for_a_stock_that_moves_like_its_peers():
    uni = _universe(0.0)
    call = Call("s", "nse", "W", "W", date(2026, 2, 10), 100.0, 95.0)
    x = uni.excess(call, np.random.default_rng(0))
    assert x == pytest.approx(0.0, abs=1e-9)


def test_excess_is_positive_for_a_stock_that_beats_same_day_peers():
    uni = _universe(0.01)  # +1% a day vs flat peers
    s = uni.stocks["W"]
    t = int(np.nonzero(s.dates == np.datetime64("2026-02-10"))[0][0])
    call = Call("s", "nse", "W", "W", date(2026, 2, 10), s.open[t + 1], s.open[t + 1] - 5)
    x = uni.excess(call, np.random.default_rng(0))
    expected = (s.close[t + HORIZON] - s.open[t + 1]) / 5
    assert x == pytest.approx(expected)  # peers are flat, so excess = the call's own R


def test_immature_calls_are_not_scored():
    uni = _universe(0.0)
    late = str(DAYS[-HORIZON + 2])
    call = Call("s", "nse", "W", "W", date.fromisoformat(late), 100.0, 95.0)
    assert uni.excess(call, np.random.default_rng(0)) is None


def test_verdict_uses_days_as_the_unit_and_needs_enough_days():
    uni = _universe(0.01)
    days = [str(d) for d in DAYS[30:40]]  # 10 decision days: below profit_gate.MIN_CALLS
    calls = [
        Call("s", "nse", "W", "W", date.fromisoformat(d), 100.0, 95.0)
        for d in days
        for _ in range(3)  # three calls a day still make ten units, not thirty
    ]
    (ev,) = evaluate(calls, {"nse": uni})
    assert ev.n_days == 10 and ev.n_calls == 30
    assert ev.verdict == "collecting"
    assert ev.mean_excess_r is not None and ev.mean_excess_r > 0
