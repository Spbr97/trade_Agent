"""run_lab_candidate.py: Phase 3's separate, simpler validation loop for LabSetup
candidates, built from the real backtest/fills.py primitives (not re-derived)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from tradedesk_lab.candidates.mean_reversion_v1 import MeanReversionV1
from tradedesk_lab.harness.gauntlet import run_gauntlet
from tradedesk_lab.harness.run_lab_candidate import simulate_single_setup, to_harness_trades
from tradedesk_lab.harness.spec import KillCriteria

from tradedesk.backtest.portfolio import ClosedTrade
from tradedesk.engine.indicators import daily_features


class _FakeRoundTrip:
    def __init__(self, total):
        self.total = total


class _FakeCosts:
    """A tiny, real-shaped cost model: 0.1% of notional round trip, matching this
    project's own brokerage-cap-free approximation for a quick smoke test."""

    def round_trip_cost(self, *, trade_type, qty, entry_price, exit_price):
        from decimal import Decimal

        notional = (Decimal(str(entry_price)) + Decimal(str(exit_price))) * Decimal(str(qty))
        return _FakeRoundTrip(total=notional * Decimal("0.0005"))


def _price_series(n: int, *, seed: int = 3) -> pd.DataFrame:
    """A real uptrend with periodic sharp one/two-day pullbacks - engineered so RSI(2)
    dips below 10 repeatedly while price stays above a rising EMA50, the exact
    MeanReversionV1 entry condition, without hand-picking exact bar values."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n, tz="Asia/Kolkata")
    closes = [100.0]
    for i in range(1, n):
        if i % 17 in (0, 1):  # a sharp two-day pullback every ~17 sessions
            drift = -0.018
        else:
            drift = 0.0035
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.002)))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0.004, 0.001, n)))
    lows = closes * (1 - np.abs(rng.normal(0.004, 0.001, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    volume = rng.integers(100_000, 200_000, n)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=idx,
    )


def test_mean_reversion_v1_arms_on_a_real_engineered_dip():
    frame = daily_features(_price_series(300))
    candidate = MeanReversionV1()
    from tradedesk.setups.base import SetupContext

    ctx = SetupContext(scrip_code="NSE_1", symbol="TEST")
    armed_count = 0
    for i in range(210, len(frame)):
        sig = candidate.arm(frame.iloc[: i + 1], ctx, {})
        if sig is not None:
            armed_count += 1
            assert sig.stop < sig.trigger < sig.t1
    assert armed_count > 0, "engineered dips never produced a single arm - fixture is bad"


def test_simulate_single_setup_produces_real_resolved_trades():
    frames = {
        "NSE_1": daily_features(_price_series(300, seed=1)),
        "NSE_2": daily_features(_price_series(300, seed=2)),
    }
    trades = simulate_single_setup(
        MeanReversionV1(),
        frames,
        {"NSE_1": "ONE", "NSE_2": "TWO"},
        costs_model=_FakeCosts(),
        equity=1_000_000.0,
        risk_pct=0.005,
    )
    assert len(trades) > 0
    for trade in trades:
        assert isinstance(trade, ClosedTrade)
        assert trade.setup == "mean_reversion_v1"
        assert trade.exit_date >= trade.entry_date
        assert trade.position.qty_initial > 0
        assert trade.costs > 0  # the fake cost model always charges something
        assert np.isfinite(trade.r_multiple)


def test_to_harness_trades_and_full_gauntlet_run_end_to_end():
    frames = {f"NSE_{i}": daily_features(_price_series(400, seed=i)) for i in range(8)}
    symbols = {code: code for code in frames}
    trades = simulate_single_setup(
        MeanReversionV1(), frames, symbols, costs_model=_FakeCosts(), equity=1_000_000.0
    )
    assert len(trades) > 0

    harness_trades, bars_by_code = to_harness_trades(trades, frames)
    assert len(harness_trades) == len(trades)
    for code in frames:
        assert code in bars_by_code

    report = run_gauntlet(
        "mean_reversion_v1_smoketest",
        harness_trades,
        bars_by_code,
        KillCriteria(
            min_net_expectancy_r=-10.0,  # deliberately permissive - this checks the
            min_sample_size=1,  # PIPELINE runs end to end, not that this candidate wins
            max_drawdown_pct=1.0,
            max_losing_streak=1000,
        ),
        n_null_cohorts=50,
        n_monte_carlo=50,
    )
    assert report.stages  # at least the random-entry-benchmark stage ran
    assert "random_entry_benchmark" in report.stages


def test_simulate_single_setup_handles_a_stock_with_no_arms_gracefully():
    flat = pd.DataFrame(
        {
            "open": [100.0] * 250,
            "high": [100.5] * 250,
            "low": [99.5] * 250,
            "close": [100.0] * 250,
            "volume": [100_000] * 250,
        },
        index=pd.bdate_range("2023-01-02", periods=250, tz="Asia/Kolkata"),
    )
    trades = simulate_single_setup(
        MeanReversionV1(),
        {"NSE_FLAT": daily_features(flat)},
        {"NSE_FLAT": "FLAT"},
        costs_model=_FakeCosts(),
        equity=1_000_000.0,
    )
    assert trades == []
