"""Fair random-entry benchmark for daily trades (tradedesk_lab/harness/fair_null.py).

The properties that matter: random entries use the real exit with no forced close, are
drawn only from eligible bars, the API carries no information about when the real trade
exited, p-values resolve below the old 1/1000 floor, and costs are priced as delivery."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.candidates.base import LabSignal
from tradedesk_lab.harness.fair_null import (
    FairTrade,
    _Frame,
    point_in_time_members,
    run_fair_null,
    simulate_random_entry,
)

from tradedesk.engine.signals import ExitPlan
from tradedesk.models import TradeType

TREND_PLAN = ExitPlan(
    partial_at_r=2.0, partial_fraction=0.5, trail="atr", trail_atr_mult=3.0,
    time_stop_sessions=41, max_hold_sessions=40,
)  # fmt: skip


def _frame(close: np.ndarray, atr: float = 1.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(close), freq="B")
    close = np.asarray(close, float)
    opn = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": opn,
            "high": np.maximum(opn, close) + 0.2,
            "low": np.minimum(opn, close) - 0.2,
            "close": close,
            "ema10": pd.Series(close).ewm(span=10, adjust=False).mean().to_numpy(),
            "atr14": np.full(len(close), atr),
        },
        index=idx,
    )


def _signal(trigger: float = 100.0, atr: float = 1.0) -> LabSignal:
    risk = 2.5 * atr
    return LabSignal(
        id="t", scrip_code="X", symbol="X", armed_on=date(2024, 1, 1), trigger=trigger,
        stop=trigger - risk, t1=trigger + 2 * risk, t2=trigger + 3 * risk, atr=atr,
        exit_plan=TREND_PLAN, valid_sessions=1,
    )  # fmt: skip


def test_trailing_winner_is_not_force_closed():
    # +0.5 a day for 80 days: a fair random entry rides the trend until the trail or max hold.
    close = 100 + 0.5 * np.arange(120)
    fr = _Frame(_frame(close))
    res = simulate_random_entry(fr, 10, _signal(), (2.5, 2.0, 3.0))
    assert res is not None
    gross_r, entry, stop, exit_px = res
    # held 40 sessions on a steady rise: well beyond what a close a few bars later would give
    assert gross_r > 3.0
    assert exit_px > entry


def test_eligible_mask_decides_the_pool():
    up = 100 + 0.5 * np.arange(150)
    down = up[-1] - 0.5 * np.arange(1, 151)
    frame = _frame(np.r_[up, down])
    trade = FairTrade("X", entry_bar=140, signal=_signal(), gross_r=0.0)
    rising = np.zeros(len(frame), bool)
    rising[20:120] = True
    falling = np.zeros(len(frame), bool)
    falling[160:260] = True
    r_up = run_fair_null([trade], {"X": frame}, eligible={"X": rising}, n_cohorts=500,
                         window_sessions=200)  # fmt: skip
    r_dn = run_fair_null([trade], {"X": frame}, eligible={"X": falling}, n_cohorts=500,
                         window_sessions=200)  # fmt: skip
    assert r_up.null_mean_gross_r > 1.0
    assert r_dn.null_mean_gross_r < 0.0


def test_p_value_resolves_below_old_floor():
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, 400))
    frame = _frame(np.maximum(close, 20))
    trades = [
        FairTrade("X", entry_bar=b, signal=_signal(), gross_r=10.0) for b in range(150, 250, 5)
    ]
    res = run_fair_null(trades, {"X": frame}, n_cohorts=20_000)
    assert res.p_value == pytest.approx(1 / 20_001)
    assert res.p_resolution < 0.001
    assert res.passes


def test_setup_like_random_is_not_significant():
    rng = np.random.default_rng(2)
    close = 100 + np.cumsum(rng.normal(0, 1, 600))
    frame = _frame(np.maximum(close, 20))
    probe = run_fair_null([FairTrade("X", 300, _signal(), 0.0)], {"X": frame}, n_cohorts=2000,
                          window_sessions=250, max_pool=None)  # fmt: skip
    trades = [FairTrade("X", 300, _signal(), probe.null_mean_gross_r) for _ in range(30)]
    res = run_fair_null(trades, {"X": frame}, n_cohorts=4000, window_sessions=250, max_pool=None)
    assert 0.05 < res.p_value < 0.95
    assert not res.passes


def test_costs_are_priced_as_delivery():
    seen: list[TradeType] = []

    def round_trip_cost(*, trade_type, qty, entry_price, exit_price, dp_applies=True):
        seen.append(trade_type)
        return SimpleNamespace(total=Decimal("20"))

    model = SimpleNamespace(round_trip_cost=round_trip_cost)
    close = 100 + 0.3 * np.arange(200)
    trades = [FairTrade("X", 100, _signal(), 1.0)]
    res = run_fair_null(trades, {"X": _frame(close)}, n_cohorts=200, cost_model=model)
    assert seen and set(seen) == {TradeType.DELIVERY}
    assert res.null_mean_net_r is not None and res.null_mean_net_r < res.null_mean_gross_r
    assert res.setup_mean_net_r is not None and res.setup_mean_net_r < 1.0


def test_real_net_r_is_used_when_given():
    close = 100 + 0.3 * np.arange(200)
    trades = [FairTrade("X", 100, _signal(), 1.0, net_r=0.7)]
    res = run_fair_null(trades, {"X": _frame(close)}, n_cohorts=200)
    assert res.setup_mean_net_r == pytest.approx(0.7)


def test_no_trades_is_an_error_not_a_verdict():
    with pytest.raises(ValueError):
        run_fair_null([], {})


def test_point_in_time_universe_uses_only_past_turnover():
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    base = {"close": np.full(60, 100.0), "high": 101.0, "low": 99.0, "open": 100.0}
    # A trades heavily for the first 30 days, B for the last 30: membership must flip,
    # and on day 30 it may only reflect turnover up to day 29.
    a = pd.DataFrame({**base, "volume": np.r_[np.full(30, 1e6), np.full(30, 1e3)]}, index=idx)
    b = pd.DataFrame({**base, "volume": np.r_[np.full(30, 1e3), np.full(30, 1e6)]}, index=idx)
    m = point_in_time_members({"A": a, "B": b}, top_n=1, lookback=5)
    assert not m["A"][:5].any()  # no history yet: nobody qualifies
    assert m["A"][10] and not m["B"][10]
    assert m["A"][30] and not m["B"][30]  # day 30 still sees days 25-29 only
    assert m["B"][50] and not m["A"][50]
