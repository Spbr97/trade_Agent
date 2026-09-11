"""Pattern detectors on hand-built charts (PLAN.md M4 'pattern tests pass on hand-picked
charts'). Each chart is constructed to contain exactly one structure."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.engine.charts import flat, frame, trend
from tradedesk.config.models import PatternConfig
from tradedesk.engine import patterns as pt
from tradedesk.engine.indicators import ema

CFG = PatternConfig()


def test_swing_pivots_are_confirmed_after_right_side_prints() -> None:
    closes = [10, 11, 12, 15, 12, 11, 10, 9, 8, 9, 10, 11, 12]  # peak at 3, trough at 8
    df = frame(closes, ranges=[0.2] * len(closes), open_is_prev_close=False)
    piv = pt.swing_pivots(df, bars=3)
    highs = [p for p in piv if p.kind is pt.PivotKind.HIGH]
    lows = [p for p in piv if p.kind is pt.PivotKind.LOW]
    assert [p.index for p in highs] == [3] and highs[0].confirmed_at == 6
    assert [p.index for p in lows] == [8] and lows[0].confirmed_at == 11
    # With only 10 bars the trough at 8 has no right side yet -> not returned.
    assert [p.index for p in pt.swing_pivots(df.iloc[:10], bars=3)] == [3]


def test_find_base_after_advance() -> None:
    up = trend(40, start=100, step_pct=0.01, seed=1)  # ~100 -> ~148
    top = up[-1]
    base = flat(15, top * 0.99, wobble_pct=0.01, seed=2)
    ranges = [c * 0.03 for c in up] + [c * 0.012 for c in base]  # base bars are tighter
    vols = [1_500_000] * 40 + [700_000] * 15  # volume dries up in the base
    df = frame(up + base, ranges=ranges, volumes=vols)
    b = pt.find_base(df, CFG)
    assert b is not None
    assert 14 <= b.length <= 17  # the flat part, not the advance
    assert b.end == len(df) - 1
    assert b.depth_pct < 0.06
    assert b.contraction_ratio < 1.0 and b.volume_dryup < 1.0
    assert b.breakout_level == b.high and b.measured_target == pytest.approx(
        b.high + (b.high - b.low)
    )
    assert b.tests_of_high >= 1


def test_find_base_rejects_deep_or_far_from_high() -> None:
    up = trend(40, start=100, step_pct=0.01, seed=1)
    deep = flat(15, up[-1] * 0.85, wobble_pct=0.10, seed=3)  # 20%+ swings: not a tight base
    assert pt.find_base(frame(up + deep), CFG) is None
    sagging = [up[-1] * (1 - 0.006 * i) for i in range(15)]  # closes 8% under the high
    assert pt.find_base(frame(up + sagging), CFG) is None


def test_find_flag() -> None:
    pole = [100 * (1 + 0.025 * i) for i in range(9)]  # +20% in 8 bars, wide ranges
    top = pole[-1]
    drift = [top * (1 - 0.006 * (i + 1)) for i in range(6)]  # 6 quiet bars easing ~3.6%
    ranges = [c * 0.03 for c in pole] + [c * 0.008 for c in drift]
    df = frame(pole + drift, ranges=ranges)
    f = pt.find_flag(df, CFG)
    assert f is not None
    assert f.pole_end == 8 and f.flag_start == 9 and f.flag_end == len(df) - 1
    assert f.pole_gain_pct > 0.10
    assert 0 < f.retrace < 0.5
    assert f.breakout_level == f.flag_high
    assert f.measured_target == pytest.approx(f.flag_high + (f.pole_high - f.pole_low))


def test_find_flag_rejects_new_high_or_deep_retrace() -> None:
    pole = [100 * (1 + 0.025 * i) for i in range(9)]
    top = pole[-1]
    broke_out = pole + [top * 1.01, top * 1.02, top * 1.03]
    assert pt.find_flag(frame(broke_out), CFG) is None
    deep = pole + [top * (1 - 0.04 * (i + 1)) for i in range(5)]  # gives back the whole pole
    assert pt.find_flag(frame(deep), CFG) is None


def test_find_pullback_to_rising_ema20() -> None:
    up = trend(45, start=100, step_pct=0.012, seed=6)
    df_up = frame(up, volumes=[1_200_000] * 45)
    e = ema(df_up["close"], 20).iloc[-1]
    swing_high = up[-1]
    # Three bars stepping down to just above the EMA on lighter volume.
    steps = [swing_high * 0.985, swing_high * 0.965, max(e * 1.004, swing_high * 0.95)]
    ranges = [c * 0.012 for c in up] + [c * 0.01 for c in steps]
    vols = [1_200_000] * 45 + [600_000] * 3
    df = frame(up + steps, ranges=ranges, volumes=vols)
    df.iloc[-1, df.columns.get_loc("low")] = float(ema(df["close"], 20).iloc[-1]) * 1.002
    pb = pt.find_pullback(df, CFG)
    assert pb is not None
    assert pb.length == 3 and pb.swing_high_index == 44
    assert pb.volume_ratio < 1.0
    assert abs(pb.distance_to_ema_pct) <= CFG.pullback_touch_pct
    assert pb.trigger_level == df["high"].iloc[-1]


def test_find_pullback_rejects_breakdown_and_no_touch() -> None:
    up = trend(45, start=100, step_pct=0.012, seed=6)
    top = up[-1]
    shallow = up + [top * 0.99, top * 0.985, top * 0.98]  # never reaches the EMA
    assert pt.find_pullback(frame(shallow), CFG) is None
    breakdown = up + [top * 0.95, top * 0.88, top * 0.80]  # smashes through it
    assert pt.find_pullback(frame(breakdown), CFG) is None


def test_volatility_squeeze_flags() -> None:
    closes = [100] * 10
    ranges = [3, 3, 3, 3, 3, 3, 3, 3, 3, 0.8]  # last bar: narrowest of 7 and inside prior bar
    sq = pt.volatility_squeeze(frame(closes, ranges=ranges), CFG)
    assert sq.nr7 and sq.inside_day and sq.near_high
    wide = pt.volatility_squeeze(frame(closes, ranges=[3] * 9 + [5]), CFG)
    assert not wide.nr7 and not wide.inside_day
    far = pt.volatility_squeeze(frame([120] * 5 + [100] * 5, ranges=[1] * 10), CFG)
    assert not far.near_high and far.pct_from_high20 > 0.1


def test_support_double_bottom_and_higher_low() -> None:
    base = [110, 108, 105, 100, 103, 106, 108, 106, 103, 100.5, 104, 107, 109, 111]
    df = frame(base, ranges=[0.4] * len(base), open_is_prev_close=False)
    s = pt.support_confirmation(df, CFG)
    assert s is not None and s.kind is pt.SupportKind.DOUBLE_BOTTOM
    assert s.first.index == 3 and s.second.index == 9
    hl = [110, 108, 105, 100, 103, 106, 108, 106, 105, 104, 106, 108, 110, 112]
    s2 = pt.support_confirmation(frame(hl, ranges=[0.4] * len(hl), open_is_prev_close=False), CFG)
    assert s2 is not None and s2.kind is pt.SupportKind.HIGHER_LOW and s2.level > 100
    lower = [110, 108, 105, 100, 103, 106, 108, 104, 98, 96, 99, 101, 103, 104]
    df_lower = frame(lower, ranges=[0.4] * len(lower), open_is_prev_close=False)
    assert pt.support_confirmation(df_lower, CFG) is None


def test_overhead_supply_levels_and_blue_sky() -> None:
    closes = [100, 104, 110, 118, 112, 106, 102, 100, 103, 106, 108]  # pivot high at 118
    df = frame(closes, ranges=[0.5] * len(closes))
    o = pt.overhead_supply(df, price=108.0, cfg=CFG)
    assert o.nearest_level is not None and o.nearest_level == pytest.approx(df["high"].iloc[3])
    assert o.source in {"pivot_high", "52w_high"}
    assert o.distance_pct == pytest.approx((o.nearest_level - 108) / 108)
    sky = pt.overhead_supply(df, price=130.0, cfg=CFG)
    assert sky.nearest_level is None and sky.source is None and sky.levels == []


def test_pattern_geometry_is_serialisable() -> None:
    up = trend(40, start=100, step_pct=0.01, seed=1)
    df = frame(up + flat(15, up[-1] * 0.99, seed=2))
    b = pt.find_base(df, CFG)
    assert b is not None
    d = b.model_dump()
    assert set(d) >= {"start", "end", "high", "low", "breakout_level", "measured_target"}
    assert isinstance(pd.DataFrame([d]), pd.DataFrame)
