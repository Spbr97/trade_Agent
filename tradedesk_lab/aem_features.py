"""Causal features for anticipatory early-momentum research."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.engine.indicators import daily_features, intraday_features
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.mcb_features import _ist_index, time_of_day_rvol


def daily_snapshot(
    frame: pd.DataFrame,
    on: date | pd.Timestamp,
    contract: AemContract,
) -> dict[str, Any]:
    """Evidence known after the daily close, without requiring MCB compression."""
    cutoff = pd.Timestamp(on).date()
    idx = pd.DatetimeIndex(frame.index)
    if idx.hasnans or not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError("invalid_daily_timestamps")
    dates = idx.tz_convert("Asia/Kolkata").date if idx.tz is not None else idx.date
    history = frame.loc[dates <= cutoff]
    if history.empty or dates[dates <= cutoff][-1] != cutoff:
        raise ValueError("missing_daily_close")
    if len(history) < contract.minimum_daily_sessions:
        raise ValueError("insufficient_daily_warmup")
    feats = history if {"ema20", "ema50", "atr14"}.issubset(history) else daily_features(history)
    last = feats.iloc[-1]
    atr = float(last.atr14)
    if not np.isfinite(atr) or atr <= 0:
        raise ValueError("invalid_daily_atr")
    recent = feats.iloc[-contract.resistance_lookback_sessions :]
    resistance = float(recent.high.max())
    distance = max(0.0, resistance / float(last.close) - 1)
    extension = (float(last.close) - float(last.ema20)) / atr
    liquid = feats.iloc[-contract.liquidity_sessions :]
    median_turnover = float((liquid.close * liquid.volume).median())
    checks = {
        "trend": bool(
            last.close > last.ema20 > last.ema50 and last.ema20_slope > 0 and last.ema50_slope > 0
        ),
        "near_resistance": distance <= contract.maximum_resistance_distance,
        "not_extended": extension <= contract.maximum_extension_atr,
        "liquidity": median_turnover >= contract.minimum_median_turnover_inr,
    }
    return {
        "as_of": str(cutoff),
        "close": float(last.close),
        "ema20": float(last.ema20),
        "ema50": float(last.ema50),
        "daily_atr": atr,
        "resistance": resistance,
        "resistance_distance": distance,
        "extension_atr": extension,
        "median_turnover_inr": median_turnover,
        "checks": checks,
        "eligible": all(checks.values()),
    }


def intraday_snapshot(
    frame: pd.DataFrame,
    *,
    at: pd.Timestamp,
    resistance: float,
    contract: AemContract,
) -> dict[str, Any]:
    """Evaluate a fresh, completed M1 bar using the frozen signal interval."""
    when = pd.Timestamp(at)
    if pd.isna(when):
        raise ValueError("invalid_decision_timestamp")
    when = when.tz_localize("Asia/Kolkata") if when.tz is None else when.tz_convert("Asia/Kolkata")
    bar = pd.Timedelta(minutes=contract.signal_interval_minutes)
    if when != when.floor("min"):
        raise ValueError("unaligned_decision_timestamp")
    idx = _ist_index(frame)
    if idx.hasnans or not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError("invalid_signal_timestamps")
    # Discard all future candles before indicators or interval checks. Appending
    # sparse future data must never change which bar was closed at this decision.
    frame = frame.loc[idx + bar <= when]
    if frame.empty:
        raise ValueError("no_closed_intraday_bar")
    closed_idx = _ist_index(frame)
    session_idx = closed_idx[closed_idx.date == when.date()]
    if len(session_idx) < contract.opening_range_bars:
        raise ValueError("opening_range_incomplete")
    if session_idx[-1] + bar != when:
        raise ValueError("stale_signal_bar")
    if (
        session_idx[0] != when.normalize() + pd.Timedelta(hours=9, minutes=15)
        or not ((session_idx[1:] - session_idx[:-1]) == bar).all()
    ):
        raise ValueError("incomplete_signal_session")
    current = frame.loc[closed_idx.date == when.date()]
    try:
        values = current[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_signal_bar") from exc
    if (
        not np.isfinite(values).all()
        or (values[:, :4] <= 0).any()
        or (values[:, 4] < 0).any()
        or (values[:, 2] > np.minimum(values[:, 0], values[:, 3])).any()
        or (values[:, 1] < np.maximum(values[:, 0], values[:, 3])).any()
    ):
        raise ValueError("invalid_signal_bar")
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
    closed = enriched.loc[idx + bar <= when]
    session_idx = _ist_index(closed)
    session = closed.loc[session_idx.date == when.date()]
    last = session.iloc[-1]
    previous = session.iloc[-2] if len(session) > 1 else last
    close = float(last.close)
    bar_range = float(last.high - last.low)
    body_ratio = (close - float(last.open)) / bar_range if bar_range > 0 else 0.0
    vwap_slope = float(last.vwap - previous.vwap)
    rvol = float(last.tod_rvol) if pd.notna(last.tod_rvol) else np.nan
    distance = resistance / close - 1
    ignition = close > float(previous.high) and close > float(last.open)
    impulse = None
    prior_bars = session.iloc[-contract.impulse_lookback_bars - 1 : -1]
    for _, row in prior_bars.iloc[::-1].iterrows():
        row_range = float(row.high - row.low)
        row_body = (float(row.close) - float(row.open)) / row_range if row_range > 0 else 0.0
        row_rvol = float(row.tod_rvol) if pd.notna(row.tod_rvol) else np.nan
        if (
            row_body >= contract.bullish_body_ratio_min
            and np.isfinite(row_rvol)
            and row_rvol >= contract.rvol_min
            and float(row.close) > float(row.vwap)
        ):
            impulse = row
            break
    pullback = bool(
        impulse is not None
        and close > float(last.vwap)
        and float(last.low) >= float(last.vwap) * (1 - contract.vwap_hold_tolerance)
        and close <= float(impulse.close)
        and float(last.volume) < float(impulse.volume)
    )
    entry_pattern = "impulse_pullback" if pullback else "momentum_ignition" if ignition else None
    pattern_allowed = bool(
        pullback or (ignition and distance <= contract.momentum_entry_resistance_distance)
    )
    volume_confirmation = bool((np.isfinite(rvol) and rvol >= contract.rvol_min) or pullback)
    body_confirmation = body_ratio >= contract.bullish_body_ratio_min or pullback
    checks = {
        "decision_window": contract.earliest_decision_time
        <= when.strftime("%H:%M")
        <= contract.latest_decision_time,
        "anticipation_zone": (
            -contract.resistance_tolerance <= distance <= contract.maximum_resistance_distance
        ),
        "target_room": distance >= contract.target_pct,
        "entry_pattern": entry_pattern is not None and pattern_allowed,
        "above_vwap": close > float(last.vwap),
        "vwap_rising": vwap_slope > 0,
        "volume_confirmation": volume_confirmation,
        "body_confirmation": body_confirmation,
    }
    return {
        "available_at": when.isoformat(),
        "bar_open": pd.Timestamp(session.index[-1]).isoformat(),
        "signal_price": close,
        "resistance": resistance,
        "resistance_distance": distance,
        "tod_rvol": rvol,
        "vwap": float(last.vwap),
        "vwap_slope": vwap_slope,
        "body_ratio": body_ratio,
        "bar_low": float(last.low),
        "entry_pattern": entry_pattern,
        "checks": checks,
        "eligible": all(checks.values()),
    }
