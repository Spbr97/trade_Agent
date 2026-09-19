"""Causal daily and five-minute features for MCB research."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.engine.indicators import daily_features, intraday_features
from tradedesk_lab.mcb_contract import McbContract

BAR = pd.Timedelta(minutes=5)


def _ist_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        return idx.tz_localize("Asia/Kolkata")
    return idx.tz_convert("Asia/Kolkata")


def time_of_day_rvol(
    frame: pd.DataFrame,
    *,
    lookback_sessions: int = 20,
    min_sessions: int = 5,
) -> pd.Series:
    """Volume divided by the prior-session median for the same five-minute slot.

    The current bar is shifted out before rolling, so later volume and the current bar's
    own volume can never influence its denominator.
    """
    if lookback_sessions < min_sessions or min_sessions < 1:
        raise ValueError("invalid RVOL history")
    idx = _ist_index(frame)
    slot = pd.Series(idx.strftime("%H:%M"), index=frame.index)
    volume = frame.volume.astype(float)
    expected = volume.groupby(slot, sort=False).transform(
        lambda values: values.shift(1).rolling(lookback_sessions, min_periods=min_sessions).median()
    )
    return volume / expected.replace(0, np.nan)


def daily_snapshot(
    frame: pd.DataFrame,
    on: date | pd.Timestamp,
    contract: McbContract,
) -> dict[str, Any]:
    """Daily MCB evidence known at the close of ``on``."""
    cutoff = pd.Timestamp(on).date()
    idx = pd.DatetimeIndex(frame.index)
    dates = idx.tz_convert("Asia/Kolkata").date if idx.tz is not None else idx.date
    history = frame.loc[dates <= cutoff]
    if history.empty or pd.Timestamp(history.index[-1]).date() != cutoff:
        raise ValueError("missing_daily_close")
    needed = max(
        contract.daily_prior_momentum_sessions + contract.daily_compression_sessions + 55,
        contract.daily_liquidity_sessions,
    )
    if len(history) < needed:
        raise ValueError("insufficient_daily_warmup")
    feats = history if {"ema20", "ema50", "atr14"}.issubset(history) else daily_features(history)
    last = feats.iloc[-1]
    atr = float(last.atr14)
    if not np.isfinite(atr) or atr <= 0:
        raise ValueError("invalid_daily_atr")
    n = contract.daily_compression_sessions
    compression = feats.iloc[-n:]
    prior_end = len(feats) - n
    prior_start = prior_end - contract.daily_prior_momentum_sessions
    prior = feats.iloc[prior_start:prior_end]
    if len(prior) != contract.daily_prior_momentum_sessions:
        raise ValueError("insufficient_prior_momentum")
    breakout_level = float(compression.high.max())
    compression_low = float(compression.low.min())
    width_atr = (breakout_level - compression_low) / atr
    prior_return = float(prior.close.iloc[-1] / prior.close.iloc[0] - 1)
    prior_volume = feats.volume.iloc[max(0, prior_start - 20) : prior_start]
    if len(prior_volume) < 10 or float(prior_volume.mean()) <= 0:
        raise ValueError("insufficient_prior_volume")
    volume_contraction = float(compression.volume.mean() / prior_volume.mean())
    recent = feats.iloc[-contract.daily_liquidity_sessions :]
    median_turnover = float((recent.close * recent.volume).median())
    proximity = max(0.0, breakout_level / float(last.close) - 1)
    extension_atr = (float(last.close) - float(last.ema20)) / atr
    trend_pass = bool(
        last.close > last.ema20 > last.ema50 and last.ema20_slope > 0 and last.ema50_slope > 0
    )
    checks = {
        "trend": trend_pass,
        "prior_momentum": prior_return >= contract.daily_prior_momentum_min,
        "compression": width_atr <= contract.daily_compression_atr_max,
        "volume_contraction": volume_contraction <= contract.daily_volume_contraction_max,
        "breakout_proximity": proximity <= contract.daily_breakout_proximity_max,
        "not_extended": extension_atr <= contract.daily_max_extension_atr,
        "liquidity": median_turnover >= contract.daily_min_median_turnover_inr,
    }
    return {
        "as_of": str(cutoff),
        "close": float(last.close),
        "ema20": float(last.ema20),
        "ema50": float(last.ema50),
        "ema20_slope": float(last.ema20_slope),
        "ema50_slope": float(last.ema50_slope),
        "daily_atr": atr,
        "prior_return": prior_return,
        "compression_width_atr": width_atr,
        "volume_contraction": volume_contraction,
        "breakout_proximity": proximity,
        "extension_atr": extension_atr,
        "median_turnover_inr": median_turnover,
        "breakout_level": breakout_level,
        "compression_low": compression_low,
        "checks": checks,
        "eligible": all(checks.values()),
    }


def intraday_snapshot(
    frame: pd.DataFrame,
    *,
    at: pd.Timestamp,
    breakout_level: float,
    daily_atr: float,
    contract: McbContract,
) -> dict[str, Any]:
    """MCB trigger evidence from bars fully closed by ``at``."""
    enriched = frame
    if not {"vwap", "atr14", "session_bar"}.issubset(enriched):
        enriched = intraday_features(enriched)
    if "tod_rvol" not in enriched:
        enriched = enriched.copy()
        enriched["tod_rvol"] = time_of_day_rvol(
            enriched,
            lookback_sessions=contract.rvol_lookback_sessions,
            min_sessions=contract.rvol_min_sessions,
        )
    idx = _ist_index(enriched)
    when = pd.Timestamp(at)
    if when.tz is None:
        when = when.tz_localize("Asia/Kolkata")
    else:
        when = when.tz_convert("Asia/Kolkata")
    eligible = enriched.loc[idx + BAR <= when]
    if eligible.empty:
        raise ValueError("no_closed_intraday_bar")
    session_date = when.date()
    eligible_idx = _ist_index(eligible)
    session = eligible.loc[eligible_idx.date == session_date]
    if len(session) < contract.opening_range_bars:
        raise ValueError("opening_range_incomplete")
    last = session.iloc[-1]
    opening = session.iloc[: contract.opening_range_bars]
    session_high = float(session.high.max())
    session_low = float(session.low.min())
    travelled = max(0.0, session_high - session_low)
    remaining = max(daily_atr - travelled, 0.0) / daily_atr if daily_atr > 0 else 0.0
    bar_range = float(last.high - last.low)
    body_ratio = (float(last.close) - float(last.open)) / bar_range if bar_range > 0 else 0.0
    previous_close = float(session.close.iloc[-2]) if len(session) > 1 else float(last.open)
    vwap_slope = float(last.vwap - session.vwap.iloc[-2]) if len(session) > 1 else 0.0
    trigger = max(breakout_level, float(opening.high.max()))
    clock = when.strftime("%H:%M")
    rvol = float(last.tod_rvol) if pd.notna(last.tod_rvol) else np.nan
    checks = {
        "decision_window": contract.earliest_decision_time
        <= clock
        <= contract.latest_decision_time,
        "fresh_cross": previous_close <= trigger < float(last.close),
        "above_vwap": float(last.close) > float(last.vwap),
        "vwap_rising": vwap_slope > 0,
        "rvol": np.isfinite(rvol) and rvol >= contract.rvol_min,
        "bullish_body": body_ratio >= contract.bullish_body_ratio_min,
        "remaining_atr": remaining >= contract.remaining_atr_min,
    }
    return {
        "available_at": when.isoformat(),
        "bar_open": pd.Timestamp(session.index[-1]).isoformat(),
        "trigger": trigger,
        "opening_range_high": float(opening.high.max()),
        "opening_range_low": float(opening.low.min()),
        "session_high": session_high,
        "session_low": session_low,
        "remaining_atr": remaining,
        "tod_rvol": rvol,
        "vwap": float(last.vwap),
        "vwap_slope": vwap_slope,
        "intraday_atr": float(last.atr14),
        "body_ratio": body_ratio,
        "bar_low": float(last.low),
        "bar_close": float(last.close),
        "checks": checks,
        "eligible": all(checks.values()),
    }
