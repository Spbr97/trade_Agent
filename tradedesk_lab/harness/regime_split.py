"""Phase 4's regime split: trending / ranging / high-vol by MARKET CHARACTER - deliberately
NOT `engine.regime.classify_regime`'s RISK_ON/NEUTRAL/RISK_OFF, which is a different,
risk-sizing-oriented axis (benchmark trend + breadth + VIX) with no trending/ranging/
high-volatility concept at all (confirmed by reading it before writing this). Built from
indicators `engine.indicators.py` already computes (`adx14`, `atr_pct`) rather than a new
indicator pipeline.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd


class MarketRegime(StrEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"


def classify_market_regime(
    features: pd.DataFrame,
    *,
    adx_trend_threshold: float = 25.0,
    atr_pct_high_vol_percentile: float = 0.80,
) -> pd.Series:
    """One label per bar. HIGH_VOL takes priority over TRENDING: an expensive/volatile tape
    dominates trade cost and risk regardless of whether it's also trending, so it's checked
    first - the reverse ordering from `intraday_regime.classify_intraday_regime` (which checks
    breakout/trend before volatility for a different, documented reason: there a strong trend
    with elevated ATR is the trend itself, not noise). Here the two questions are independent:
    "how volatile" and "how directional" describe different things about the same bar, and
    volatility is the one that changes execution cost/risk regardless of the other answer."""
    missing = [c for c in ("adx14", "atr_pct") if c not in features.columns]
    if missing:
        raise ValueError(f"classify_market_regime needs columns {missing}")
    atr_cutoff = features["atr_pct"].quantile(atr_pct_high_vol_percentile)
    labels = np.where(
        features["atr_pct"] >= atr_cutoff,
        MarketRegime.HIGH_VOL.value,
        np.where(
            features["adx14"] >= adx_trend_threshold,
            MarketRegime.TRENDING.value,
            MarketRegime.RANGING.value,
        ),
    )
    return pd.Series(labels, index=features.index, name="regime")


def split_trades_by_regime(
    entry_regimes: list[MarketRegime | str], r_multiples: list[float]
) -> dict[str, dict[str, float | int]]:
    """Groups already-computed trade R-multiples by the regime of their entry bar and reports
    each bucket's trade count and mean R - "a strategy that only works in one regime needs a
    regime filter, or it needs discarding" per the template."""
    if len(entry_regimes) != len(r_multiples):
        raise ValueError("entry_regimes and r_multiples must be the same length")
    buckets: dict[str, list[float]] = {}
    for regime, r in zip(entry_regimes, r_multiples, strict=True):
        key = regime.value if isinstance(regime, MarketRegime) else regime
        buckets.setdefault(key, []).append(r)
    return {key: {"trades": len(rs), "mean_r": float(np.mean(rs))} for key, rs in buckets.items()}
