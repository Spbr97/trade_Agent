"""Intraday regime taxonomy (SDD section 6).

Most tests build a synthetic FEATURES frame directly (not real OHLCV run through
intraday_features) so each branch of the priority order can be isolated deterministically -
constructing raw price series and hoping the derived indicators land in the right band is
exactly the approach that produced several false failures while writing this (a strong trend
built from a linear price ramp at constant absolute bar range makes atr_pct itself trend
monotonically, so the true state gets pre-empted by whichever volatility band the drift
happens to wander into - a fixture artifact, not a code bug, but one worth avoiding rather
than fighting). Two tests (`test_classify_intraday_regime_has_no_look_ahead` and
`test_full_pipeline_smoke`) deliberately DO run the real intraday_features pipeline, since
that wiring itself needs checking too.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradedesk.engine.indicators import intraday_features
from tradedesk.engine.intraday_regime import IntradayRegime, classify_intraday_regime

RANK_WINDOW = 25
EXP_WINDOW = 10


def _frame(baseline: dict, target: dict, n_baseline: int = RANK_WINDOW) -> pd.DataFrame:
    """`n_baseline` copies of `baseline`, then one `target` row. atr_pct_rank and
    expansion_ratio are computed by `classify_intraday_regime` itself from this frame's own
    high/low/atr_pct columns, so a baseline held constant makes the target row's percentile
    and expansion ratio land exactly where the test intends, deterministically."""
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=i) for i in range(n_baseline + 1)]  # noqa: E501
    )
    rows = [dict(baseline) for _ in range(n_baseline)] + [dict(target)]
    return pd.DataFrame(rows, index=idx)


# A "not tight, not stacked, not extreme" baseline: nothing here should ever trigger
# breakout (range_contraction 1.0 = not tight) or an extreme volatility percentile once the
# target's atr_pct sits mid-distribution.
BASE = dict(
    open=100.0, high=100.2, low=99.8, close=100.0,
    atr_pct=1.0, adx14=5.0, range_contraction=1.0,
    ema9=100.0, ema20=100.0, ema50=100.0,
)


def _mid_vol_target(**over: object) -> dict:
    """Overrides BASE with atr_pct held at the baseline's own value (so its rolling
    percentile among 25 identical baseline values plus itself is mid-pack, not a tie at the
    extreme), range_contraction 1.0 (not tight - never a breakout) unless overridden."""
    t = dict(BASE)
    t.update(over)
    return t


def test_strong_trend_up_beats_incidentally_high_relative_volatility() -> None:
    """The ordering fix this session made: a strongly trending bar with an ELEVATED atr_pct
    (which real trends routinely have) must still classify as the trend, not get pre-empted
    by the volatility bands - those are reserved for bars with no clear trend."""
    target = _mid_vol_target(
        adx14=30.0, ema9=103.0, ema20=102.0, ema50=101.0, close=104.0,
        atr_pct=5.0,  # far above the baseline's 1.0 - would be HIGH_VOLATILITY if checked first
    )
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.STRONG_TREND_UP


def test_strong_trend_down() -> None:
    target = _mid_vol_target(adx14=30.0, ema9=102.0, ema20=103.0, ema50=104.0, close=101.0)
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.STRONG_TREND_DOWN


def test_weak_trend_up() -> None:
    """adx between the weak and strong thresholds; only close-vs-ema20 decides direction,
    not a full EMA stack."""
    target = _mid_vol_target(adx14=20.0, ema20=100.0, close=101.0)
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.WEAK_TREND_UP


def test_weak_trend_down() -> None:
    target = _mid_vol_target(adx14=20.0, ema20=100.0, close=99.0)
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.WEAK_TREND_DOWN


def test_high_volatility_only_applies_with_no_clear_trend() -> None:
    target = _mid_vol_target(adx14=5.0, atr_pct=5.0)  # adx < ADX_WEAK: no trend branch taken
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.HIGH_VOLATILITY


def test_low_volatility() -> None:
    target = _mid_vol_target(adx14=5.0, atr_pct=0.01)  # far below the baseline's 1.0
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.LOW_VOLATILITY


def test_choppy_is_no_trend_mid_volatility_but_expanding_range() -> None:
    target = _mid_vol_target(adx14=5.0, atr_pct=1.0, range_contraction=1.3)
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.CHOPPY


def test_range_is_the_final_fallback() -> None:
    target = _mid_vol_target(adx14=5.0, atr_pct=1.0, range_contraction=0.9)
    f = _frame(BASE, target)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.RANGE


def test_breakout_needs_both_a_prior_squeeze_and_a_current_expansion() -> None:
    """Neither condition alone is enough: a wide bar after a normal (not tight) run is not a
    breakout, and a tight run with no follow-through expansion is not one either."""
    tight_before = dict(BASE, range_contraction=0.5)  # the bar immediately before target
    up_break = dict(BASE, open=100.0, close=112.0, high=112.2, low=99.8)  # big range, closes up

    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=i) for i in range(EXP_WINDOW + 2)]  # noqa: E501
    )
    rows = [dict(BASE) for _ in range(EXP_WINDOW)] + [tight_before, up_break]
    f = pd.DataFrame(rows, index=idx)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.BREAKOUT

    # Same wide bar, but the prior bar was NOT tight - must not be flagged as a breakout.
    rows_no_squeeze = [dict(BASE) for _ in range(EXP_WINDOW)] + [dict(BASE), up_break]
    f2 = pd.DataFrame(rows_no_squeeze, index=idx)
    reg2 = classify_intraday_regime(f2, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg2.iloc[-1] is not IntradayRegime.BREAKOUT


def test_breakdown_is_the_same_squeeze_with_a_down_close() -> None:
    tight_before = dict(BASE, range_contraction=0.5)
    down_break = dict(BASE, open=100.0, close=88.0, high=100.2, low=87.8)
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=i) for i in range(EXP_WINDOW + 2)]  # noqa: E501
    )
    rows = [dict(BASE) for _ in range(EXP_WINDOW)] + [tight_before, down_break]
    f = pd.DataFrame(rows, index=idx)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.BREAKDOWN


def test_breakout_takes_priority_over_a_simultaneous_trend_signal() -> None:
    """A transient breakout event outranks the steady-state trend/volatility labels, even
    when the target row would otherwise also qualify as a strong trend."""
    tight_before = dict(BASE, range_contraction=0.5)
    up_break_and_trending = dict(
        BASE, open=100.0, close=112.0, high=112.2, low=99.8,
        adx14=30.0, ema9=103.0, ema20=102.0, ema50=101.0,
    )
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=i) for i in range(EXP_WINDOW + 2)]  # noqa: E501
    )
    rows = [dict(BASE) for _ in range(EXP_WINDOW)] + [tight_before, up_break_and_trending]
    f = pd.DataFrame(rows, index=idx)
    reg = classify_intraday_regime(f, atr_rank_window=RANK_WINDOW, expansion_window=EXP_WINDOW)
    assert reg.iloc[-1] is IntradayRegime.BREAKOUT


def test_every_regime_state_is_a_declared_enum_member() -> None:
    expected = {
        "breakout", "breakdown", "high_volatility", "low_volatility",
        "strong_trend_up", "strong_trend_down", "weak_trend_up", "weak_trend_down",
        "choppy", "range",
    }  # fmt: skip
    assert {m.value for m in IntradayRegime} == expected


def test_classify_intraday_regime_has_no_look_ahead() -> None:
    """Runs the REAL intraday_features pipeline (unlike the isolated tests above), since a
    rolling stat computed with the wrong window alignment - e.g. a centred rolling window -
    would break this even though intraday_features itself is look-ahead free."""
    rng = np.random.default_rng(7)
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=15 * i) for i in range(150)]  # noqa: E501
    )
    closes = [100.0 + i * 0.1 + float(rng.normal(0, 1.0)) for i in range(150)]
    opens = [closes[0]] + closes[:-1]
    df = pd.DataFrame(
        [
            {"open": o, "high": max(o, c) + 0.1, "low": min(o, c) - 0.1, "close": c, "volume": 1000.0}  # noqa: E501
            for o, c in zip(opens, closes, strict=True)
        ],
        index=idx,
    )
    full = classify_intraday_regime(intraday_features(df), atr_rank_window=60, expansion_window=10)  # noqa: E501
    for k in (40, 80, 120, 148):
        partial = classify_intraday_regime(
            intraday_features(df.iloc[: k + 1]), atr_rank_window=60, expansion_window=10
        )
        assert full.iloc[k] == partial.iloc[-1], f"bar {k}: {full.iloc[k]} != {partial.iloc[-1]}"


def test_full_pipeline_smoke() -> None:
    """The real intraday_features -> classify_intraday_regime wiring runs end to end and
    produces exactly one valid label per row, on ordinary market-shaped data."""
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=15 * i) for i in range(60)]  # noqa: E501
    )
    closes = [100.0 + i * 0.3 for i in range(60)]
    opens = [closes[0]] + closes[:-1]
    df = pd.DataFrame(
        [
            {"open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "close": c, "volume": 1000.0}  # noqa: E501
            for o, c in zip(opens, closes, strict=True)
        ],
        index=idx,
    )
    reg = classify_intraday_regime(intraday_features(df))
    assert len(reg) == len(df)
    assert set(reg.unique()) <= set(IntradayRegime)


@pytest.mark.parametrize("n", [0, 1, 5])
def test_handles_short_frames_without_crashing(n: int) -> None:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=15 * i) for i in range(max(n, 1))]  # noqa: E501
    )
    df = pd.DataFrame(
        [{"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1000.0}] * max(n, 1),
        index=idx,
    )
    df = df.iloc[:n]
    if not len(df):
        return
    reg = classify_intraday_regime(intraday_features(df))
    assert len(reg) == len(df)
