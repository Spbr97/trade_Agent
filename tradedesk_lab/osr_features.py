"""Decision-time features for the frozen OSR failed-auction hypothesis.

The feature layer exists to distinguish high-quality opening reclaims from weak
ones; it makes no trade decision itself. Every value is computed from completed
bars at ``opportunity.decision_at``, prior completed sessions, or other symbols'
completed bars at the same timestamp. Missing or non-causal inputs fail closed.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.engine.indicators import daily_features
from tradedesk.markets.costs import CostModel
from tradedesk.models import TradeType, price_decimal
from tradedesk_lab.mcb_features import time_of_day_rvol
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT, OsrContract
from tradedesk_lab.osr_events import (
    DEFAULT_OSR_EVENT_CONTRACT,
    IST,
    M1,
    OsrEventEngineContract,
    OsrOpportunity,
    _validated_bars,
    _vwap,
)

MINIMUM_BARS_REQUIRED = 15
MINIMUM_DAILY_SESSIONS = 60
MEDIAN_TURNOVER_SESSIONS = 20
MINIMUM_PEERS = 5
RVOL_LOOKBACK_SESSIONS = 20
RVOL_MIN_SESSIONS = 5
ROLLING_TURNOVER_BARS = 5


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _daily_context(daily_frame: pd.DataFrame, *, session_date) -> dict[str, float]:
    try:
        daily = daily_frame.loc[:, ["open", "high", "low", "close", "volume"]].copy()
    except (KeyError, TypeError) as exc:
        raise ValueError("missing_daily_columns") from exc
    idx = pd.DatetimeIndex(daily.index)
    dates = idx.tz_convert(IST).date if idx.tz is not None else idx.date
    if idx.hasnans or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError("invalid_daily_timestamps")
    if len(daily) < MINIMUM_DAILY_SESSIONS:
        raise ValueError("insufficient_daily_history")
    if dates[-1] >= session_date:
        raise ValueError("daily_frame_not_causal")
    try:
        raw = daily.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_daily_values") from exc
    if not np.isfinite(raw).all() or (raw[:, :4] <= 0).any() or (raw[:, 4] < 0).any():
        raise ValueError("invalid_daily_values")
    feats = daily_features(daily)
    last = feats.iloc[-1]
    atr = float(last.atr14)
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("invalid_daily_atr")
    recent = daily.iloc[-MEDIAN_TURNOVER_SESSIONS:]
    if len(recent) < MEDIAN_TURNOVER_SESSIONS:
        raise ValueError("insufficient_daily_turnover_history")
    prior = daily.iloc[-1]
    return {
        "prior_close": float(prior.close),
        "prior_low": float(prior.low),
        "prior_high": float(prior.high),
        "prior_atr": atr,
        "prior_ema20": float(last.ema20),
        "median_turnover_20d": float((recent.close * recent.volume).median()),
    }


def _validated_peers(
    universe_frames: dict[str, pd.DataFrame],
    *,
    exclude: str,
    at: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    peers: dict[str, pd.DataFrame] = {}
    for code, frame in universe_frames.items():
        if code == exclude:
            continue
        try:
            bars = _validated_bars(frame, through_open=at - M1)
        except ValueError:
            continue
        if bars.index[-1] + M1 != at or len(bars) < MINIMUM_BARS_REQUIRED:
            continue
        peers[code] = bars
    if len(peers) < MINIMUM_PEERS:
        raise ValueError("insufficient_peer_universe")
    return peers


def _percentile_against_peers(value: float, peers: np.ndarray) -> float:
    below = float(np.sum(peers < value))
    equal = float(np.sum(peers == value))
    return (below + 0.5 * equal) / len(peers)


def _tod_rvol(
    bars: pd.DataFrame,
    intraday_history: pd.DataFrame,
) -> float:
    if intraday_history.empty:
        raise ValueError("missing_tod_rvol_history")
    history_idx = pd.DatetimeIndex(intraday_history.index)
    history_idx = (
        history_idx.tz_convert(IST) if history_idx.tz is not None else history_idx.tz_localize(IST)
    )
    if history_idx.hasnans or history_idx.has_duplicates or not history_idx.is_monotonic_increasing:
        raise ValueError("invalid_intraday_history_timestamps")
    if history_idx.max() >= bars.index[0]:
        raise ValueError("intraday_history_overlaps_current_session")
    normalized_history = intraday_history.set_axis(history_idx)
    combined = pd.concat([normalized_history, bars])
    series = time_of_day_rvol(
        combined,
        lookback_sessions=RVOL_LOOKBACK_SESSIONS,
        min_sessions=RVOL_MIN_SESSIONS,
    )
    value = float(series.loc[bars.index[-1]])
    if not math.isfinite(value):
        raise ValueError("insufficient_rvol_history")
    return value


def compute_features(
    opportunity: OsrOpportunity,
    frame: pd.DataFrame,
    *,
    daily_frame: pd.DataFrame,
    universe_frames: dict[str, pd.DataFrame],
    intraday_history: pd.DataFrame,
    costs: CostModel,
    quantity: int,
    contract: OsrContract = DEFAULT_OSR_CONTRACT,
    event_contract: OsrEventEngineContract = DEFAULT_OSR_EVENT_CONTRACT,
) -> dict[str, Any]:
    """Compute the 30 frozen OSR features at the opportunity's decision time."""

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if opportunity.strategy_contract_sha256 != contract.sha256:
        raise ValueError("opportunity strategy contract differs")
    if opportunity.event_contract_sha256 != event_contract.sha256:
        raise ValueError("opportunity event contract differs")
    if opportunity.mode not in contract.entry_modes:
        raise ValueError("opportunity mode is not in the frozen OSR registry")

    at = pd.Timestamp(opportunity.decision_at)
    at = at.tz_localize(IST) if at.tz is None else at.tz_convert(IST)
    bars = _validated_bars(frame, through_open=at - M1)
    if bars.index[-1] + M1 != at:
        raise ValueError("stale_signal_bar")
    if len(bars) < MINIMUM_BARS_REQUIRED:
        raise ValueError("insufficient_signal_history")

    daily = _daily_context(daily_frame, session_date=at.date())
    recorded_prior_close = opportunity.decision_values.get("prior_close")
    recorded_prior_low = opportunity.decision_values.get("prior_low")
    if recorded_prior_close is None or recorded_prior_low is None:
        raise ValueError("opportunity_missing_prior_context")
    if not math.isclose(float(recorded_prior_close), daily["prior_close"], rel_tol=1e-9):
        raise ValueError("opportunity_prior_close_mismatch")
    if not math.isclose(float(recorded_prior_low), daily["prior_low"], rel_tol=1e-9):
        raise ValueError("opportunity_prior_low_mismatch")
    peers = _validated_peers(universe_frames, exclude=opportunity.scrip_code, at=at)

    close = bars.close.to_numpy(dtype=float)
    high = bars.high.to_numpy(dtype=float)
    low = bars.low.to_numpy(dtype=float)
    open_ = bars.open.to_numpy(dtype=float)
    volume = bars.volume.to_numpy(dtype=float)
    vwaps = _vwap(bars).to_numpy(dtype=float)
    n = len(bars)
    opening = bars.iloc[: contract.opening_range_minutes]
    opening_price = float(open_[0])
    opening_low = float(opening.low.min())
    opening_high = float(opening.high.max())
    session_low_position = int(np.argmin(low))
    session_low = float(low[session_low_position])

    # Time and opening auction.
    minutes_since_open = float(n)
    deadline_hour, deadline_minute = (
        int(value) for value in contract.latest_decision_time.split(":")
    )
    deadline = at.normalize() + pd.Timedelta(hours=deadline_hour, minutes=deadline_minute)
    time_remaining_minutes = max(0.0, (deadline - at).total_seconds() / 60)
    gap_from_prior_close = opening_price / daily["prior_close"] - 1
    gap_from_prior_low = opening_price / daily["prior_low"] - 1
    opening_range_width_atr = (opening_high - opening_low) / daily["prior_atr"]
    opening_return = close[-1] / opening_price - 1

    # Failed-auction geometry and reclaim quality.
    early_drawdown = max(0.0, (opening_price - session_low) / opening_price)
    if opportunity.mode == "opening_low_sweep_reclaim":
        post_range_lows = low[contract.opening_range_minutes : -1]
        if not post_range_lows.size:
            raise ValueError("missing_post_opening_range_sweep")
        sweep_depth = max(0.0, (opening_low - float(post_range_lows.min())) / opening_low)
    else:
        sweep_depth = early_drawdown
    recovery_from_low = close[-1] / session_low - 1
    recovery_speed_bars = float((n - 1) - session_low_position)
    current_range = high[-1] - low[-1]
    reclaim_body_ratio = _safe_ratio(close[-1] - open_[-1], current_range)
    reclaim_close_location = _safe_ratio(close[-1] - low[-1], current_range)
    reclaim_lower_wick_ratio = _safe_ratio(min(open_[-1], close[-1]) - low[-1], current_range)
    return_1m = close[-1] / close[-2] - 1
    return_3m = close[-1] / close[-4] - 1

    # Reclaim context and participation.
    price_to_open = close[-1] / opening_price - 1
    price_to_vwap = close[-1] / vwaps[-1] - 1
    vwap_slope_5m = vwaps[-1] / vwaps[-6] - 1
    vwap_reclaim_bars = 0
    for position in range(n - 1, -1, -1):
        if close[position] >= vwaps[position]:
            vwap_reclaim_bars += 1
        else:
            break
    tod_rvol = _tod_rvol(bars, intraday_history)
    volume_acceleration_3m = _safe_ratio(volume[-1], float(np.median(volume[-4:-1])))

    # Candidate-excluded cross-sectional state.
    peer_returns = np.array(
        [float(peer.close.iloc[-1] / peer.open.iloc[0] - 1) for peer in peers.values()],
        dtype=float,
    )
    peer_recoveries = np.array(
        [
            float(peer.close.iloc[-1] / peer.low.min() - 1)
            for peer in peers.values()
        ],
        dtype=float,
    )
    cross_sectional_return_rank = _percentile_against_peers(opening_return, peer_returns)
    cross_sectional_recovery_rank = _percentile_against_peers(
        recovery_from_low, peer_recoveries
    )
    cross_sectional_breadth = float(np.mean(peer_returns > 0))
    cross_sectional_dispersion = float(np.std(peer_returns, ddof=0))

    # Prior-day and execution context.
    prior_day_range_atr = (daily["prior_high"] - daily["prior_low"]) / daily["prior_atr"]
    daily_extension_atr = (close[-1] - daily["prior_ema20"]) / daily["prior_atr"]
    rolling_turnover = float(
        np.median(close[-ROLLING_TURNOVER_BARS:] * volume[-ROLLING_TURNOVER_BARS:])
    )
    impact_proxy = _safe_ratio(quantity * opportunity.intended_entry, rolling_turnover)
    smallest_geometry = min(contract.geometries, key=lambda geometry: geometry.target_pct)
    entry_decimal = price_decimal(opportunity.intended_entry)
    exit_decimal = price_decimal(
        opportunity.intended_entry * (1 + smallest_geometry.target_pct)
    )
    charges = costs.round_trip_cost(
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        entry_price=entry_decimal,
        exit_price=exit_decimal,
        dp_applies=False,
    ).total
    modeled_round_trip_cost_pct = float(charges / (entry_decimal * quantity))

    values = {
        "minutes_since_open": minutes_since_open,
        "time_remaining_minutes": float(time_remaining_minutes),
        "gap_from_prior_close": float(gap_from_prior_close),
        "gap_from_prior_low": float(gap_from_prior_low),
        "opening_range_width_atr": float(opening_range_width_atr),
        "opening_return": float(opening_return),
        "early_drawdown": float(early_drawdown),
        "sweep_depth": float(sweep_depth),
        "recovery_from_low": float(recovery_from_low),
        "recovery_speed_bars": recovery_speed_bars,
        "reclaim_body_ratio": float(reclaim_body_ratio),
        "reclaim_close_location": float(reclaim_close_location),
        "reclaim_lower_wick_ratio": float(reclaim_lower_wick_ratio),
        "return_1m": float(return_1m),
        "return_3m": float(return_3m),
        "price_to_open": float(price_to_open),
        "price_to_vwap": float(price_to_vwap),
        "vwap_slope_5m": float(vwap_slope_5m),
        "vwap_reclaim_bars": float(vwap_reclaim_bars),
        "tod_rvol": tod_rvol,
        "volume_acceleration_3m": float(volume_acceleration_3m),
        "cross_sectional_return_rank": float(cross_sectional_return_rank),
        "cross_sectional_recovery_rank": float(cross_sectional_recovery_rank),
        "cross_sectional_breadth": cross_sectional_breadth,
        "cross_sectional_dispersion": cross_sectional_dispersion,
        "prior_day_range_atr": float(prior_day_range_atr),
        "daily_extension_atr": float(daily_extension_atr),
        "median_turnover_20d": daily["median_turnover_20d"],
        "impact_proxy": float(impact_proxy),
        "modeled_round_trip_cost_pct": modeled_round_trip_cost_pct,
    }
    registered_names = {feature.name for feature in contract.features}
    if set(values) != registered_names:
        raise ValueError("computed features do not match the frozen OSR registry")
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("non_finite_feature_value")

    return {
        "opportunity_id": opportunity.identifier,
        "scrip_code": opportunity.scrip_code,
        "available_at": at.isoformat(),
        "feature_contract_sha256": contract.sha256,
        "event_contract_sha256": event_contract.sha256,
        "peer_count": len(peers),
        "values": values,
    }
