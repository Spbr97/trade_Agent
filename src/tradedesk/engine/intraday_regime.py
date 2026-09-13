"""Intraday market-state taxonomy (SDD section 6): STRONG/WEAK_TREND_UP/DOWN, RANGE,
HIGH/LOW_VOLATILITY, CHOPPY, BREAKOUT, BREAKDOWN. "Strategies should only operate in
regimes where their historical evidence supports them."

This is deliberately NOT a replacement for `engine/regime.py::classify_regime` - that
classifies the DAILY market (benchmark + breadth + VIX) and drives the size multiplier and
the risk_off swing-entry gate; it stays the hard outer gate, unchanged, and a daily
`risk_off` reading still means no intraday entries regardless of what this module reports.
This module classifies ONE INSTRUMENT's state on ONE intraday timeframe, bar by bar, purely
from that instrument's own `intraday_features` columns plus two rolling statistics computed
here (a trailing ATR-percentile rank and a range-expansion ratio) that only need the same
already-supplied intraday feature frame.

The thresholds below are starting values, in the same spirit as `engine/scoring.py::WEIGHTS`
- reasonable, not tuned against outcome data, and the ordering (checked top to bottom, first
match wins) is what actually encodes the taxonomy's structure. Expect to revisit both once a
real intraday backtest exists to check them against.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd


class IntradayRegime(StrEnum):
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    STRONG_TREND_UP = "strong_trend_up"
    STRONG_TREND_DOWN = "strong_trend_down"
    WEAK_TREND_UP = "weak_trend_up"
    WEAK_TREND_DOWN = "weak_trend_down"
    CHOPPY = "choppy"
    RANGE = "range"


# Thresholds - see module docstring: starting values, ordering is the taxonomy.
ADX_STRONG = 25.0
ADX_WEAK = 15.0
VOL_RANK_HIGH = 80.0
VOL_RANK_LOW = 20.0
EXPANSION_RATIO_BREAKOUT = 1.5
PRIOR_CONTRACTION_MAX = 0.7  # range_contraction must have been this tight just before
CHOPPY_RANGE_CONTRACTION_MIN = 1.1  # directionless AND the range is expanding, not contracting


def classify_intraday_regime(
    f: pd.DataFrame, *, atr_rank_window: int = 500, expansion_window: int = 20
) -> pd.Series:
    """One `IntradayRegime` per row of `f` (the output of `engine/indicators.py::
    intraday_features`, ascending, one timeframe).

    `atr_rank_window`/`expansion_window` are bar counts, not sessions - deliberately a
    parameter rather than a fixed constant, since "20 sessions" is a very different number
    of bars at 1-minute (~7,500) versus 60-minute (~120); the caller should size these to
    its own timeframe. Both statistics are ROLLING (a bar's own value and everything before
    it, nothing after), so this function inherits `intraday_features`'s look-ahead-free
    guarantee rather than needing its own leakage proof from scratch - the look-ahead test
    here only needs to confirm that inheritance holds, not re-derive it.
    """
    n = len(f)
    out = np.full(n, IntradayRegime.RANGE, dtype=object)

    atr_pct = f["atr_pct"].to_numpy(dtype=float)
    adx = f["adx14"].to_numpy(dtype=float)
    rc = f["range_contraction"].to_numpy(dtype=float)
    close = f["close"].to_numpy(dtype=float)
    ema9 = f["ema9"].to_numpy(dtype=float) if "ema9" in f else np.full(n, np.nan)
    ema20 = f["ema20"].to_numpy(dtype=float) if "ema20" in f else np.full(n, np.nan)
    ema50 = f["ema50"].to_numpy(dtype=float) if "ema50" in f else np.full(n, np.nan)

    atr_pct_rank = (
        pd.Series(atr_pct).rolling(atr_rank_window, min_periods=20).rank(pct=True) * 100
    ).to_numpy()

    bar_range = (f["high"] - f["low"]).to_numpy(dtype=float)
    avg_range = pd.Series(bar_range).rolling(expansion_window, min_periods=5).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        expansion_ratio = np.where(avg_range > 0, bar_range / avg_range, np.nan)
    prior_rc = pd.Series(rc).shift(1).to_numpy()  # "was tight JUST BEFORE this bar"

    for i in range(n):
        was_tight = prior_rc[i] is not None and not np.isnan(prior_rc[i]) and prior_rc[i] < PRIOR_CONTRACTION_MAX  # noqa: E501
        expanding_now = not np.isnan(expansion_ratio[i]) and expansion_ratio[i] > EXPANSION_RATIO_BREAKOUT  # noqa: E501
        if was_tight and expanding_now:
            out[i] = IntradayRegime.BREAKOUT if close[i] > f["open"].iat[i] else IntradayRegime.BREAKDOWN  # noqa: E501
            continue

        # Trend is checked BEFORE volatility on purpose: a strongly trending market
        # routinely has elevated relative ATR too (the move itself IS the volatility), and
        # that is normal, expected and highly informative - collapsing it into a bare
        # "high_volatility" label would throw away the more decision-relevant trend state.
        # HIGH/LOW_VOLATILITY are reserved for bars with NO clear trend, where "is it moving
        # a lot or not" is the most useful thing left to say about them.
        a = adx[i]
        stacked_up = not any(np.isnan(x) for x in (ema9[i], ema20[i], ema50[i])) and close[i] > ema9[i] > ema20[i] > ema50[i]  # noqa: E501
        stacked_down = not any(np.isnan(x) for x in (ema9[i], ema20[i], ema50[i])) and close[i] < ema9[i] < ema20[i] < ema50[i]  # noqa: E501
        if not np.isnan(a) and a > ADX_STRONG and stacked_up:
            out[i] = IntradayRegime.STRONG_TREND_UP
            continue
        if not np.isnan(a) and a > ADX_STRONG and stacked_down:
            out[i] = IntradayRegime.STRONG_TREND_DOWN
            continue
        if not np.isnan(a) and ADX_WEAK <= a <= ADX_STRONG and not np.isnan(ema20[i]) and close[i] > ema20[i]:  # noqa: E501
            out[i] = IntradayRegime.WEAK_TREND_UP
            continue
        if not np.isnan(a) and ADX_WEAK <= a <= ADX_STRONG and not np.isnan(ema20[i]) and close[i] < ema20[i]:  # noqa: E501
            out[i] = IntradayRegime.WEAK_TREND_DOWN
            continue

        # No clear trend (adx < ADX_WEAK, or adx unknown): volatility, then choppy/range.
        r = atr_pct_rank[i]
        if not np.isnan(r) and r > VOL_RANK_HIGH:
            out[i] = IntradayRegime.HIGH_VOLATILITY
        elif not np.isnan(r) and r < VOL_RANK_LOW:
            out[i] = IntradayRegime.LOW_VOLATILITY
        elif not np.isnan(rc[i]) and rc[i] > CHOPPY_RANGE_CONTRACTION_MIN:
            out[i] = IntradayRegime.CHOPPY
        else:
            out[i] = IntradayRegime.RANGE

    return pd.Series(out, index=f.index, name="intraday_regime")
