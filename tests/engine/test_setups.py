"""Each setup arms on its hand-built chart with the right levels, and the shared gates
(relative strength, results blackout, trend) block it."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.engine.charts import flat, frame, trend
from tradedesk.engine.indicators import daily_features, ema
from tradedesk.engine.signals import SetupKind
from tradedesk.setups import REGISTRY, SetupContext

CTX = SetupContext(scrip_code="NSE_1", symbol="ONE", rs_percentile=90.0)


def _base_chart() -> pd.DataFrame:
    up = trend(80, start=100, step_pct=0.008, seed=1)
    base = flat(15, up[-1] * 0.99, wobble_pct=0.008, seed=2)
    ranges = [c * 0.02 for c in up] + [c * 0.01 for c in base]
    vols = [1_500_000] * 80 + [700_000] * 15
    return daily_features(frame(up + base, ranges=ranges, volumes=vols))


def test_base_breakout_levels_and_gates() -> None:
    df = _base_chart()
    setup = REGISTRY[SetupKind.BASE_BREAKOUT]
    sig = setup.arm(df, CTX, {"rs_percentile_min": 75, "max_stop_distance_atr": 3.0})
    assert sig is not None
    base = sig.geometry["base"]
    assert sig.trigger == base["high"] and sig.stop == base["low"]
    assert sig.t1 == pytest.approx(sig.trigger + 2 * sig.risk_per_share)
    assert sig.t2 >= sig.t1 and sig.gross_rr_t2 >= 2.0
    assert sig.armed_on == df.index[-1].date() and sig.scrip_code == "NSE_1"
    assert sig.exit_plan.trail == "ema10_close"
    # gates
    weak_rs = SetupContext(scrip_code="NSE_1", symbol="ONE", rs_percentile=40.0)
    assert setup.arm(df, weak_rs, {"rs_percentile_min": 75, "max_stop_distance_atr": 3.0}) is None
    results_soon = SetupContext(
        scrip_code="NSE_1", symbol="ONE", rs_percentile=90.0, results_in_sessions=6
    )
    assert setup.arm(df, results_soon, {"max_stop_distance_atr": 3.0}) is None
    assert setup.arm(df, CTX, {"max_stop_distance_atr": 0.5}) is None  # stop too far in ATRs
    downtrend = daily_features(frame(trend(95, start=200, step_pct=-0.006, seed=3)))
    assert setup.arm(downtrend, CTX, {}) is None


def test_trend_pullback_arms_on_ema20_touch() -> None:
    up = trend(240, start=100, step_pct=0.006, seed=6)
    df_up = frame(up, volumes=[1_200_000] * 240)
    e = float(ema(df_up["close"], 20).iloc[-1])
    top = up[-1]
    steps = [top * 0.985, top * 0.972, max(e * 1.003, top * 0.96)]
    ranges = [c * 0.012 for c in up] + [c * 0.008 for c in steps]
    vols = [1_200_000] * 240 + [600_000] * 3
    df = frame(up + steps, ranges=ranges, volumes=vols)
    df.iloc[-1, df.columns.get_loc("low")] = float(ema(df["close"], 20).iloc[-1]) * 1.002
    feats = daily_features(df)
    setup = REGISTRY[SetupKind.TREND_PULLBACK]
    sig = setup.arm(feats, CTX, {"rs_percentile_min": 75})
    assert sig is not None
    pb = sig.geometry["pullback"]
    assert sig.trigger == pb["trigger_level"] == df["high"].iloc[-1]
    assert sig.stop == pb["low"]
    assert 1.0 <= sig.exit_plan.partial_at_r <= 2.0
    assert setup.arm(feats.iloc[:150], CTX, {}) is None  # not enough history for EMA200


def test_nr7_breakout_arms_on_coil_near_high() -> None:
    up = trend(70, start=100, step_pct=0.008, seed=8)
    closes = up + [up[-1]] * 3
    ranges = [c * 0.02 for c in up] + [up[-1] * 0.012, up[-1] * 0.012, up[-1] * 0.004]
    feats = daily_features(frame(closes, ranges=ranges))
    setup = REGISTRY[SetupKind.NR7_BREAKOUT]
    sig = setup.arm(feats, CTX, {"rs_percentile_min": 75})
    assert sig is not None
    sq = sig.geometry["squeeze"]
    assert sq["nr7"] and sq["near_high"]
    assert sig.trigger == sq["day_high"] and sig.stop == sq["day_low"]
    assert sig.exit_plan.trail == "atr"
    wide = daily_features(frame(closes, ranges=[c * 0.02 for c in closes[:-1]] + [up[-1] * 0.05]))
    assert setup.arm(wide, CTX, {}) is None  # last bar is the widest, not a coil


def test_signal_ids_are_unique_per_code_setup_and_day() -> None:
    df = _base_chart()
    a = REGISTRY[SetupKind.BASE_BREAKOUT].arm(df, CTX, {"max_stop_distance_atr": 3.0})
    b = REGISTRY[SetupKind.BASE_BREAKOUT].arm(df.iloc[:-1], CTX, {"max_stop_distance_atr": 3.0})
    assert a is not None
    assert b is None or a.id != b.id
