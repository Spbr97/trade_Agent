"""Milestone 2: the causal AEM v2 feature registry, computed at decision time only.

Every value returned by :func:`compute_features` must be derivable from bars that have
already closed by ``opportunity.decision_at`` - the same causal boundary
``aem_v2_events.opportunities_at`` uses to generate the opportunity itself. Nothing here
reads an outcome field, a later bar, or today's own daily candle (which cannot exist
before the session closes).

Several formulas are deliberate, disclosed approximations of the prose feature table in
``docs/plan-aem-v2-50pct-baseline.md`` section 5.2, not a literal transcription - the plan
itself only names feature groups and intents, not exact math. Each approximation is noted
inline. Where a tested primitive already exists (``time_of_day_rvol`` from same-time
relative-volume research, ``daily_features`` for EMA/ATR/52-week levels, ``CostModel`` for
round-trip cost), it is reused rather than re-derived, per this project's own discipline.

Fails closed: any missing bar, insufficient warmup, non-causal daily frame, or an
undersized peer universe raises ``ValueError`` rather than returning a partial or
best-effort feature set. A veto layer and a model are Milestone 3, not this module - this
module only ever answers "what could a model see", never "should a model act".
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.engine.indicators import daily_features
from tradedesk.markets.costs import CostModel
from tradedesk.models import TradeType, price_decimal
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import (
    DEFAULT_EVENT_ENGINE_CONTRACT,
    IST,
    M1,
    AemV2EventEngineContract,
    AemV2Opportunity,
    _validated_bars,
    _vwap,
)
from tradedesk_lab.mcb_features import time_of_day_rvol

# Local, disclosed constants. None of these are fitted parameters; they are the minimum
# causal history each formula needs and are frozen alongside this module.
MINIMUM_BARS_REQUIRED = 11  # range_expansion's 10-bar lookback + the current bar
OPENING_RANGE_BARS = 5
ROLLING_TURNOVER_BARS = 5
CHASE_LOOKBACK_BARS = 5
MINIMUM_DAILY_SESSIONS = 60
MEDIAN_TURNOVER_SESSIONS = 20
MINIMUM_PEERS = 5
RVOL_LOOKBACK_SESSIONS = 20
RVOL_MIN_SESSIONS = 5


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _daily_context(daily_frame: pd.DataFrame, *, session_date) -> dict[str, float]:
    idx = pd.DatetimeIndex(daily_frame.index)
    dates = idx.tz_convert(IST).date if idx.tz is not None else idx.date
    if idx.hasnans or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError("invalid_daily_timestamps")
    if len(daily_frame) < MINIMUM_DAILY_SESSIONS:
        raise ValueError("insufficient_daily_history")
    if dates[-1] >= session_date:
        raise ValueError("daily_frame_not_causal")
    feats = daily_features(daily_frame)
    last = feats.iloc[-1]
    atr = float(last.atr14)
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("invalid_daily_atr")
    recent = daily_frame.iloc[-MEDIAN_TURNOVER_SESSIONS:]
    if len(recent) < MEDIAN_TURNOVER_SESSIONS:
        raise ValueError("insufficient_daily_turnover_history")
    return {
        "prior_close": float(last.close),
        "prior_atr": atr,
        "prior_ema20": float(last.ema20),
        "prior_high20": float(last.high20),
        "median_turnover_20d": float((recent.close * recent.volume).median()),
    }


def _validated_peers(
    universe_frames: dict[str, pd.DataFrame], *, exclude: str, at: pd.Timestamp
) -> dict[str, pd.DataFrame]:
    peers: dict[str, pd.DataFrame] = {}
    for code, pframe in universe_frames.items():
        if code == exclude:
            continue
        try:
            pbars = _validated_bars(pframe, through_open=at - M1)
        except ValueError:
            continue
        if pbars.index[-1] + M1 != at or len(pbars) < MINIMUM_BARS_REQUIRED:
            continue
        peers[code] = pbars
    if len(peers) < MINIMUM_PEERS:
        raise ValueError("insufficient_peer_universe")
    return peers


def compute_features(
    opportunity: AemV2Opportunity,
    frame: pd.DataFrame,
    *,
    daily_frame: pd.DataFrame,
    universe_frames: dict[str, pd.DataFrame],
    intraday_history: pd.DataFrame,
    costs: CostModel,
    quantity: int,
    event_contract: AemV2EventEngineContract = DEFAULT_EVENT_ENGINE_CONTRACT,
) -> dict[str, Any]:
    """Compute the frozen AEM v2 feature registry at ``opportunity.decision_at``.

    ``universe_frames`` must map peer scrip codes to that peer's own-session M1 bars
    through the same decision time (self may be included or omitted; it is excluded from
    every cross-sectional statistic either way). ``intraday_history`` must be this code's
    M1 bars for prior sessions only, strictly before today's open - it exists solely to
    give ``tod_rvol`` a same-time-of-day baseline and is never allowed to reach into today.
    """

    if opportunity.strategy_contract_sha256 != DEFAULT_AEM_V2_CONTRACT.sha256:
        raise ValueError("opportunity strategy contract differs")
    if opportunity.event_contract_sha256 != event_contract.sha256:
        raise ValueError("opportunity event contract differs")

    at = pd.Timestamp(opportunity.decision_at)
    at = at.tz_localize(IST) if at.tz is None else at.tz_convert(IST)
    bars = _validated_bars(frame, through_open=at - M1)
    if bars.index[-1] + M1 != at:
        raise ValueError("stale_signal_bar")
    if len(bars) < MINIMUM_BARS_REQUIRED:
        raise ValueError("insufficient_signal_history")

    session_date = at.date()
    daily = _daily_context(daily_frame, session_date=session_date)
    peers = _validated_peers(universe_frames, exclude=opportunity.scrip_code, at=at)

    n = len(bars)
    close = bars.close.to_numpy(dtype=float)
    high = bars.high.to_numpy(dtype=float)
    low = bars.low.to_numpy(dtype=float)
    open_ = bars.open.to_numpy(dtype=float)
    volume = bars.volume.to_numpy(dtype=float)
    vwap = _vwap(bars).to_numpy(dtype=float)
    lookback = event_contract.causal_level_lookback_bars
    structure = event_contract.structure_lookback_bars

    # --- Time and opening structure -------------------------------------------------
    minutes_since_open = float(n)
    opening = bars.iloc[:OPENING_RANGE_BARS]
    orh, orl = float(opening.high.max()), float(opening.low.min())
    opening_range_position = 0.5 if orh == orl else (close[-1] - orl) / (orh - orl)
    opening_gap_atr = (open_[0] - daily["prior_close"]) / daily["prior_atr"]
    deadline = at.normalize() + pd.Timedelta(
        hours=int(event_contract.latest_decision_time[:2]),
        minutes=int(event_contract.latest_decision_time[3:]),
    )
    time_remaining_minutes = max(0.0, (deadline - at).total_seconds() / 60)

    # --- Impulse quality --------------------------------------------------------------
    return_1m = close[-1] / close[-2] - 1
    return_3m = close[-1] / close[-4] - 1
    return_5m = close[-1] / close[-6] - 1
    prior_return_3m = close[-4] / close[-7] - 1
    price_acceleration_3m = return_3m - prior_return_3m
    bar_range = high[-1] - low[-1]
    body_ratio = _safe_ratio(close[-1] - open_[-1], bar_range)
    upper_wick = high[-1] - max(open_[-1], close[-1])
    lower_wick = min(open_[-1], close[-1]) - low[-1]
    wick_balance = _safe_ratio(upper_wick - lower_wick, bar_range)
    prior_ranges = high[-11:-1] - low[-11:-1]
    range_expansion = _safe_ratio(bar_range, float(np.median(prior_ranges)))

    # --- Compression and breakout state -----------------------------------------------
    compression_window_high = high[-(structure + 1) : -1]
    compression_window_low = low[-(structure + 1) : -1]
    prior_compression = _safe_ratio(
        float(compression_window_high.max() - compression_window_low.min()), daily["prior_atr"]
    )
    intraday_level = float(high[-(lookback + 1) : -1].max())
    combined_level = min(intraday_level, daily["prior_high20"])
    causal_level_distance = combined_level / close[-1] - 1
    failed_break_count = 0
    retest_depth = 0.0
    window_start = max(lookback, n - structure)
    for position in range(window_start, n - 1):
        level_at_position = float(high[position - lookback : position].max())
        if high[position] > level_at_position and close[position] <= level_at_position:
            failed_break_count += 1
    for position in range(n - 2, window_start - 1, -1):
        level_at_position = float(high[position - lookback : position].max())
        if close[position] > level_at_position:
            after_low = low[position + 1 : n - 1]
            if after_low.size:
                depth = level_at_position - float(after_low.min())
                retest_depth = max(0.0, depth / level_at_position)
            break

    # --- VWAP structure -----------------------------------------------------------------
    vwap_distance = close[-1] / vwap[-1] - 1
    vwap_slope_5m = _safe_ratio(vwap[-1] - vwap[-6], vwap[-6])
    vwap_hold_bars = 0
    for position in range(n - 1, -1, -1):
        if close[position] >= vwap[position]:
            vwap_hold_bars += 1
        else:
            break

    # --- Participation --------------------------------------------------------------
    if intraday_history.empty:
        raise ValueError("missing_tod_rvol_history")
    history_idx = pd.DatetimeIndex(intraday_history.index)
    history_idx = (
        history_idx.tz_convert(IST) if history_idx.tz is not None else history_idx.tz_localize(IST)
    )
    if history_idx.max() >= bars.index[0]:
        raise ValueError("intraday_history_overlaps_current_session")
    # Normalize onto the SAME tz object as `bars` before concatenating: a source frame
    # built under a different (but equal) tz backend - e.g. zoneinfo vs pytz, both
    # legitimately "Asia/Kolkata" - makes pandas silently degrade the concatenated
    # index to `object` dtype, which then breaks downstream datetime parsing.
    normalized_history = intraday_history.set_axis(history_idx)
    combined = pd.concat([normalized_history, bars])
    rvol_series = time_of_day_rvol(
        combined, lookback_sessions=RVOL_LOOKBACK_SESSIONS, min_sessions=RVOL_MIN_SESSIONS
    )
    tod_rvol_value = float(rvol_series.loc[bars.index[-1]])
    if not math.isfinite(tod_rvol_value):
        raise ValueError("insufficient_rvol_history")
    prior_volume_3m = float(np.median(volume[-4:-1]))
    volume_acceleration_3m = _safe_ratio(volume[-1], prior_volume_3m)
    rolling_turnover_inr = float(
        np.median(close[-ROLLING_TURNOVER_BARS:] * volume[-ROLLING_TURNOVER_BARS:])
    )

    # --- Cross-sectional and regime (needs the peer universe) ------------------------
    peer_returns_5m = {
        code: float(p.close.iloc[-1] / p.close.iloc[-6] - 1) for code, p in peers.items()
    }
    values_5m = np.array(list(peer_returns_5m.values()), dtype=float)
    relative_strength_5m = return_5m - float(np.median(values_5m))
    cross_sectional_breadth = float(np.mean(values_5m > 0))
    cross_sectional_dispersion = float(np.std(values_5m, ddof=0))
    peer_vols = []
    for p in peers.values():
        peer_closes = p.close.to_numpy(dtype=float)[-11:]
        peer_rets = peer_closes[1:] / peer_closes[:-1] - 1
        peer_vols.append(float(np.std(peer_rets, ddof=0)))
    causal_volatility = float(np.median(peer_vols))

    # --- Remaining room -----------------------------------------------------------------
    resistance_distance = daily["prior_high20"] / close[-1] - 1
    daily_extension_atr = (close[-1] - daily["prior_ema20"]) / daily["prior_atr"]
    session_high, session_low = float(high.max()), float(low.min())
    realized_rate = _safe_ratio(session_high - session_low, minutes_since_open)
    achievable_move_before_deadline = _safe_ratio(
        realized_rate * time_remaining_minutes, daily["prior_atr"]
    )

    # --- Execution quality ---------------------------------------------------------------
    smallest_geometry = min(DEFAULT_AEM_V2_CONTRACT.geometries, key=lambda g: g.target_pct)
    entry_decimal = price_decimal(opportunity.intended_entry)
    exit_decimal = price_decimal(opportunity.intended_entry * (1 + smallest_geometry.target_pct))
    charges = costs.round_trip_cost(
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        entry_price=entry_decimal,
        exit_price=exit_decimal,
        dp_applies=False,
    ).total
    modeled_round_trip_cost_pct = float(charges / (entry_decimal * quantity))
    impact_proxy = _safe_ratio(quantity * opportunity.intended_entry, rolling_turnover_inr)
    limit_distance = opportunity.entry_limit / opportunity.intended_entry - 1
    gaps = np.abs(open_[1:] - close[:-1]) / close[:-1]
    chase_pct = float(np.median(gaps[-CHASE_LOOKBACK_BARS:])) if gaps.size else 0.0

    # --- Regime (trend age; volatility computed above with the peer universe) -----------
    vwap_side = np.sign(close - vwap)
    current_side = vwap_side[-1] if vwap_side[-1] != 0 else (vwap_side[-2] if n > 1 else 0.0)
    trend_age_bars = 0
    for value in vwap_side[::-1]:
        side = value if value != 0 else current_side
        if side != current_side:
            break
        trend_age_bars += 1

    values = {
        "minutes_since_open": minutes_since_open,
        "opening_range_position": float(opening_range_position),
        "opening_gap_atr": float(opening_gap_atr),
        "time_remaining_minutes": float(time_remaining_minutes),
        "return_1m": float(return_1m),
        "return_3m": float(return_3m),
        "return_5m": float(return_5m),
        "price_acceleration_3m": float(price_acceleration_3m),
        "body_ratio": float(body_ratio),
        "wick_balance": float(wick_balance),
        "range_expansion": float(range_expansion),
        "prior_compression": float(prior_compression),
        "causal_level_distance": float(causal_level_distance),
        "failed_break_count": float(failed_break_count),
        "retest_depth": float(retest_depth),
        "vwap_distance": float(vwap_distance),
        "vwap_slope_5m": float(vwap_slope_5m),
        "vwap_hold_bars": float(vwap_hold_bars),
        "tod_rvol": tod_rvol_value,
        "volume_acceleration_3m": float(volume_acceleration_3m),
        "rolling_turnover_inr": rolling_turnover_inr,
        "relative_strength_5m": float(relative_strength_5m),
        "cross_sectional_breadth": cross_sectional_breadth,
        "cross_sectional_dispersion": cross_sectional_dispersion,
        "resistance_distance": float(resistance_distance),
        "daily_extension_atr": float(daily_extension_atr),
        "achievable_move_before_deadline": float(achievable_move_before_deadline),
        "median_turnover_20d": daily["median_turnover_20d"],
        "impact_proxy": float(impact_proxy),
        "limit_distance": float(limit_distance),
        "chase_pct": chase_pct,
        "modeled_round_trip_cost_pct": modeled_round_trip_cost_pct,
        "causal_volatility": causal_volatility,
        "trend_age_bars": float(trend_age_bars),
    }
    registered_names = {feature.name for feature in DEFAULT_AEM_V2_CONTRACT.features}
    if set(values) != registered_names:
        raise ValueError("computed features do not match the frozen registry")
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("non_finite_feature_value")

    return {
        "opportunity_id": opportunity.identifier,
        "scrip_code": opportunity.scrip_code,
        "available_at": at.isoformat(),
        "feature_contract_sha256": DEFAULT_AEM_V2_CONTRACT.sha256,
        "event_contract_sha256": event_contract.sha256,
        "peer_count": len(peers),
        "values": values,
    }
