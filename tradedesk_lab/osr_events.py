"""Causal Opening Sweep-Reclaim opportunities and conservative M1 outcomes.

This research-only engine observes completed bars, predicts after a reclaim bar
closes, and evaluates fills only on later M1 bars.  It cannot rank candidates,
train a model, emit a live alert, or place an order.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.markets.costs import CostModel
from tradedesk.models import TradeType, price_decimal
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT, OsrContract, OsrGeometry

IST = "Asia/Kolkata"
M1 = pd.Timedelta(minutes=1)
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class OsrEventEngineContract:
    """Ordering and execution rules separate from the signal hypothesis."""

    version: str = "osr-event-engine-v1"
    session_open: str = "09:15"
    bar_minutes: int = 1
    tick_size: float = 0.05
    one_opportunity_per_mode_per_session: bool = True
    ambiguity_policy: str = "adverse_stop_first"

    def __post_init__(self) -> None:
        if self.bar_minutes != 1:
            raise ValueError("OSR event reconstruction is frozen to M1 bars")
        if self.session_open != "09:15":
            raise ValueError("OSR session open must remain 09:15")
        if not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("tick size must be positive and finite")
        if not self.one_opportunity_per_mode_per_session:
            raise ValueError("OSR must deduplicate each mode within a session")
        if self.ambiguity_policy != "adverse_stop_first":
            raise ValueError("M1 ambiguity must fail adversely")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


DEFAULT_OSR_EVENT_CONTRACT = OsrEventEngineContract()


@dataclass(frozen=True)
class OsrOpportunity:
    identifier: str
    scrip_code: str
    symbol: str
    session: str
    mode: str
    signal_bar_open: str
    decision_at: str
    state_started_at: str
    intended_entry: float
    entry_limit: float
    causal_reference: float
    decision_values: dict[str, float]
    strategy_contract_sha256: str
    event_contract_sha256: str


def _ist_index(index: pd.Index) -> pd.DatetimeIndex:
    try:
        result = pd.DatetimeIndex(index)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_m1_timestamps") from exc
    if result.tz is None:
        result = result.tz_localize(IST)
    else:
        result = result.tz_convert(IST)
    return result


def _validated_bars(
    frame: pd.DataFrame, *, through_open: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Validate one regular M1 session, optionally ignoring every later row."""

    try:
        bars = frame.loc[:, REQUIRED_COLUMNS].copy()
    except (KeyError, TypeError) as exc:
        raise ValueError("missing_m1_columns") from exc
    bars.index = _ist_index(bars.index)
    if through_open is not None:
        cutoff = pd.Timestamp(through_open)
        cutoff = cutoff.tz_localize(IST) if cutoff.tz is None else cutoff.tz_convert(IST)
        bars = bars.loc[bars.index <= cutoff]
    idx = pd.DatetimeIndex(bars.index)
    if bars.empty:
        raise ValueError("missing_m1_bars")
    if idx.hasnans or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError("invalid_m1_timestamps")
    if len({stamp.date() for stamp in idx}) != 1:
        raise ValueError("mixed_m1_sessions")
    expected_open = idx[0].normalize() + pd.Timedelta(hours=9, minutes=15)
    if idx[0] != expected_open or (len(idx) > 1 and not ((idx[1:] - idx[:-1]) == M1).all()):
        raise ValueError("incomplete_m1_sequence")
    try:
        values = bars.loc[:, REQUIRED_COLUMNS].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_m1_values") from exc
    prices, volume = values[:, :4], values[:, 4]
    if (
        not np.isfinite(values).all()
        or (prices <= 0).any()
        or (volume < 0).any()
        or (bars["low"].to_numpy() > prices[:, [0, 3]].min(axis=1)).any()
        or (bars["high"].to_numpy() < prices[:, [0, 3]].max(axis=1)).any()
        or (bars["high"].to_numpy() < bars["low"].to_numpy()).any()
    ):
        raise ValueError("invalid_m1_values")
    return bars


def _ceil_tick(value: float, tick_size: float) -> float:
    tick = Decimal(str(tick_size))
    ticks = (Decimal(str(value)) / tick).to_integral_value(rounding=ROUND_CEILING)
    return float(ticks * tick)


def _vwap(bars: pd.DataFrame) -> pd.Series:
    typical = (bars.high + bars.low + bars.close) / 3
    cumulative_volume = bars.volume.cumsum()
    return ((typical * bars.volume).cumsum() / cumulative_volume.replace(0, np.nan)).ffill()


def _prior_context(prior_close: float, prior_low: float) -> tuple[float, float]:
    try:
        close, low = float(prior_close), float(prior_low)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_prior_daily_context") from exc
    if not math.isfinite(close + low) or close <= 0 or low <= 0 or low > close:
        raise ValueError("invalid_prior_daily_context")
    return close, low


def _strong_reclaim(current: pd.Series, minimum_close_location: float) -> tuple[bool, float]:
    span = float(current.high) - float(current.low)
    if span <= 0:
        return False, 0.0
    close_location = (float(current.close) - float(current.low)) / span
    return (
        float(current.close) > float(current.open)
        and close_location >= minimum_close_location,
        close_location,
    )


def opportunities_at(
    frame: pd.DataFrame,
    *,
    at: pd.Timestamp,
    prior_close: float,
    prior_low: float,
    scrip_code: str,
    symbol: str,
    contract: OsrContract = DEFAULT_OSR_CONTRACT,
    event_contract: OsrEventEngineContract = DEFAULT_OSR_EVENT_CONTRACT,
) -> tuple[OsrOpportunity, ...]:
    """Evaluate both frozen OSR modes after the signal bar has closed.

    ``at`` is the decision timestamp. A row whose open is ``at`` is future
    information and is deliberately excluded.
    """

    prior_close, prior_low = _prior_context(prior_close, prior_low)
    when = pd.Timestamp(at)
    when = when.tz_localize(IST) if when.tz is None else when.tz_convert(IST)
    if when != when.floor("min"):
        raise ValueError("unaligned_decision_timestamp")
    latest_open = when - M1
    bars = _validated_bars(frame, through_open=latest_open)
    if bars.index[-1] + M1 != when:
        raise ValueError("stale_signal_bar")
    clock = when.strftime("%H:%M")
    if not contract.earliest_decision_time <= clock <= contract.latest_decision_time:
        return ()
    if len(bars) < contract.opening_range_minutes:
        return ()

    current_position = len(bars) - 1
    current = bars.iloc[current_position]
    previous = bars.iloc[current_position - 1]
    opening = bars.iloc[: contract.opening_range_minutes]
    opening_price = float(bars.iloc[0].open)
    opening_low = float(opening.low.min())
    opening_high = float(opening.high.max())
    gap_from_prior_close = opening_price / prior_close - 1
    gap_from_prior_low = opening_price / prior_low - 1
    vwaps = _vwap(bars)
    strong_reclaim, close_location = _strong_reclaim(
        current, contract.minimum_reclaim_close_location
    )

    detected: list[tuple[str, int, float, float]] = []

    # A gap-down session crosses back above both the session open and causal VWAP.
    # The lower extreme must predate the reclaim bar.
    early = bars.iloc[:current_position]
    if not early.empty:
        early_low = float(early.low.min())
        early_low_position = int(np.argmin(early.low.to_numpy(dtype=float)))
        initial_drawdown = max(0.0, (opening_price - early_low) / opening_price)
        recovery_from_low = float(current.close) / early_low - 1
        reference = max(opening_price, float(vwaps.iloc[-1]))
        prior_reference = max(opening_price, float(vwaps.iloc[-2]))
        gap_down_reclaim = bool(
            -contract.maximum_gap_down_pct
            <= gap_from_prior_close
            <= -contract.minimum_gap_down_pct
            and recovery_from_low >= contract.minimum_sweep_depth_pct
            and initial_drawdown <= contract.maximum_initial_drawdown_pct
            and strong_reclaim
            and float(previous.close) <= prior_reference
            and float(current.close) > reference
        )
        if gap_down_reclaim:
            detected.append(
                ("gap_down_reclaim", early_low_position, reference, initial_drawdown)
            )

    # The first 15 bars are fully completed before a later bar may sweep their
    # low. A still-later completed bar must cross back above that known level.
    sweep_positions = range(contract.opening_range_minutes, current_position)
    valid_sweeps = [
        position
        for position in sweep_positions
        if (opening_low - float(bars.low.iloc[position])) / opening_low
        >= contract.minimum_sweep_depth_pct
    ]
    if valid_sweeps:
        sweep_position = min(valid_sweeps, key=lambda position: float(bars.low.iloc[position]))
        sweep_low = float(bars.low.iloc[sweep_position])
        sweep_depth = (opening_low - sweep_low) / opening_low
        opening_low_reclaim = bool(
            sweep_depth <= contract.maximum_initial_drawdown_pct
            and strong_reclaim
            and float(previous.close) <= opening_low
            and float(current.close) > opening_low
        )
        if opening_low_reclaim:
            detected.append(
                ("opening_low_sweep_reclaim", sweep_position, opening_low, sweep_depth)
            )

    opportunities = []
    for mode, state_position, reference, sweep_depth in detected:
        intended = _ceil_tick(float(current.close), event_contract.tick_size)
        limit = _ceil_tick(intended * (1 + contract.maximum_chase_pct), event_contract.tick_size)
        decision = when.isoformat()
        identifier = f"OSR_v1:{scrip_code}:{when.date()}:{mode}:{when.strftime('%H%M')}"
        opportunities.append(
            OsrOpportunity(
                identifier=identifier,
                scrip_code=scrip_code,
                symbol=symbol,
                session=str(when.date()),
                mode=mode,
                signal_bar_open=pd.Timestamp(bars.index[-1]).isoformat(),
                decision_at=decision,
                state_started_at=pd.Timestamp(bars.index[state_position]).isoformat(),
                intended_entry=intended,
                entry_limit=limit,
                causal_reference=reference,
                decision_values={
                    "signal_close": float(current.close),
                    "session_open": opening_price,
                    "prior_close": prior_close,
                    "prior_low": prior_low,
                    "opening_range_high": opening_high,
                    "opening_range_low": opening_low,
                    "vwap": float(vwaps.iloc[-1]),
                    "gap_from_prior_close": gap_from_prior_close,
                    "gap_from_prior_low": gap_from_prior_low,
                    "sweep_depth": sweep_depth,
                    "recovery_from_session_low": float(current.close) / float(bars.low.min()) - 1,
                    "reclaim_close_location": close_location,
                },
                strategy_contract_sha256=contract.sha256,
                event_contract_sha256=event_contract.sha256,
            )
        )
    return tuple(opportunities)


def reconstruct_session_opportunities(
    frame: pd.DataFrame,
    *,
    prior_close: float,
    prior_low: float,
    scrip_code: str,
    symbol: str,
    contract: OsrContract = DEFAULT_OSR_CONTRACT,
    event_contract: OsrEventEngineContract = DEFAULT_OSR_EVENT_CONTRACT,
) -> tuple[OsrOpportunity, ...]:
    """Reconstruct at most the first causal opportunity per OSR mode."""

    _prior_context(prior_close, prior_low)
    raw_index = _ist_index(frame.index)
    if raw_index.empty:
        raise ValueError("missing_m1_bars")
    hour, minute = (int(value) for value in contract.latest_decision_time.split(":"))
    latest_signal_open = raw_index[0].normalize() + pd.Timedelta(hours=hour, minutes=minute - 1)
    bars = _validated_bars(frame, through_open=latest_signal_open)
    # Necessary-condition prefilter only: the definitive detector remains
    # opportunities_at(). This avoids re-validating every minute of every flat
    # session while never discarding a bar that could satisfy either frozen mode.
    vwaps = _vwap(bars).to_numpy(dtype=float)
    closes = bars.close.to_numpy(dtype=float)
    lows = bars.low.to_numpy(dtype=float)
    opening_price = float(bars.open.iloc[0])
    opening_low = float(bars.low.iloc[: contract.opening_range_minutes].min())
    gap_from_prior_close = opening_price / float(prior_close) - 1
    gap_eligible = (
        -contract.maximum_gap_down_pct
        <= gap_from_prior_close
        <= -contract.minimum_gap_down_pct
    )
    candidate_positions: list[int] = []
    for position in range(contract.opening_range_minutes - 1, len(bars)):
        stamp = bars.index[position]
        at = stamp + M1
        if at.strftime("%H:%M") < contract.earliest_decision_time:
            continue
        if at.strftime("%H:%M") > contract.latest_decision_time:
            break
        strong_reclaim, _close_location = _strong_reclaim(
            bars.iloc[position], contract.minimum_reclaim_close_location
        )
        if not strong_reclaim or position == 0:
            continue
        possible_gap = bool(
            gap_eligible
            and closes[position - 1] <= max(opening_price, vwaps[position - 1])
            and closes[position] > max(opening_price, vwaps[position])
        )
        prior_post_range = lows[contract.opening_range_minutes : position]
        possible_sweep = bool(
            prior_post_range.size
            and float(prior_post_range.min())
            <= opening_low * (1 - contract.minimum_sweep_depth_pct)
            and closes[position - 1] <= opening_low
            and closes[position] > opening_low
        )
        if possible_gap or possible_sweep:
            candidate_positions.append(position)

    result: list[OsrOpportunity] = []
    seen_modes: set[str] = set()
    for position in candidate_positions:
        at = bars.index[position] + M1
        for opportunity in opportunities_at(
            bars,
            at=at,
            prior_close=prior_close,
            prior_low=prior_low,
            scrip_code=scrip_code,
            symbol=symbol,
            contract=contract,
            event_contract=event_contract,
        ):
            if opportunity.mode not in seen_modes:
                result.append(opportunity)
                seen_modes.add(opportunity.mode)
        if len(seen_modes) == len(contract.entry_modes):
            break
    return tuple(result)


def _outcome_stub(
    opportunity: OsrOpportunity,
    geometry: OsrGeometry,
    *,
    status: str,
    outcome: str,
    reason: str,
    event_contract: OsrEventEngineContract,
) -> dict[str, Any]:
    return {
        "opportunity_id": opportunity.identifier,
        "mode": opportunity.mode,
        "geometry_id": geometry.id,
        "status": status,
        "outcome": outcome,
        "reason": reason,
        "filled": False,
        "strict_success": False,
        "target_hit": False,
        "profitable_timeout": False,
        "entry_at": None,
        "exit_at": None,
        "entry": None,
        "stop": None,
        "target": None,
        "exit": None,
        "qty": None,
        "costs": None,
        "gross_pnl": None,
        "net_pnl": None,
        "gross_r": None,
        "net_r": None,
        "ambiguity_policy": event_contract.ambiguity_policy,
    }


def resolve_opportunity(
    opportunity: OsrOpportunity,
    frame: pd.DataFrame,
    geometry: OsrGeometry,
    *,
    quantity: int,
    costs: CostModel,
    contract: OsrContract = DEFAULT_OSR_CONTRACT,
    event_contract: OsrEventEngineContract = DEFAULT_OSR_EVENT_CONTRACT,
) -> dict[str, Any]:
    """Apply conservative fill, barrier, deadline, slippage, and cost rules."""

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if opportunity.strategy_contract_sha256 != contract.sha256:
        raise ValueError("opportunity strategy contract differs")
    if opportunity.event_contract_sha256 != event_contract.sha256:
        raise ValueError("opportunity event contract differs")
    if geometry not in contract.geometries:
        raise ValueError("geometry is not in the frozen OSR registry")
    decision_at = pd.Timestamp(opportunity.decision_at)
    decision_at = (
        decision_at.tz_localize(IST) if decision_at.tz is None else decision_at.tz_convert(IST)
    )
    expiry = decision_at + pd.Timedelta(minutes=contract.entry_valid_minutes)
    try:
        bars = _validated_bars(frame, through_open=expiry - M1)
    except ValueError as exc:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason=str(exc),
            event_contract=event_contract,
        )
    if bars.index[-1] < decision_at:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="no_post_decision_bar",
            event_contract=event_contract,
        )

    expected_entry_slots = pd.date_range(decision_at, expiry - M1, freq="1min")
    entry_bars = bars.loc[bars.index.isin(expected_entry_slots)]
    if not entry_bars.index.equals(expected_entry_slots):
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="missing_entry_bar",
            event_contract=event_contract,
        )

    slip = float(costs.slippage_pct)
    fill_at: pd.Timestamp | None = None
    fill_price: float | None = None
    fill_kind: str | None = None
    for stamp, row in entry_bars.iterrows():
        if float(row.open) >= opportunity.intended_entry:
            candidate = _ceil_tick(float(row.open) * (1 + slip), event_contract.tick_size)
            fill_kind = "gap_open"
        elif float(row.high) >= opportunity.intended_entry:
            candidate = _ceil_tick(
                opportunity.intended_entry * (1 + slip), event_contract.tick_size
            )
            fill_kind = "intrabar_trigger"
        else:
            continue
        if candidate > opportunity.entry_limit:
            result = _outcome_stub(
                opportunity,
                geometry,
                status="unfilled",
                outcome="chase_rejected",
                reason="actual_fill_above_entry_limit",
                event_contract=event_contract,
            )
            result["entry_at"] = pd.Timestamp(stamp).isoformat()
            result["attempted_entry"] = candidate
            return result
        fill_at, fill_price = pd.Timestamp(stamp), candidate
        break
    if fill_at is None or fill_price is None or fill_kind is None:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unfilled",
            outcome="entry_expired",
            reason="entry_trigger_not_reached_before_expiry",
            event_contract=event_contract,
        )

    stop = _ceil_tick(fill_price * (1 - geometry.stop_pct), event_contract.tick_size)
    target = _ceil_tick(fill_price * (1 + geometry.target_pct), event_contract.tick_size)
    if not stop < fill_price < target:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="invalid_geometry",
            reason="tick_rounded_stop_fill_target_invalid",
            event_contract=event_contract,
        )
    deadline = fill_at + pd.Timedelta(minutes=geometry.max_hold_minutes)
    expected_holding_slots = pd.date_range(fill_at, deadline - M1, freq="1min")
    try:
        bars = _validated_bars(frame, through_open=deadline - M1)
    except ValueError as exc:
        result = _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason=str(exc),
            event_contract=event_contract,
        )
        result.update({"filled": True, "entry_at": fill_at.isoformat(), "entry": fill_price})
        return result
    holding = bars.loc[bars.index.isin(expected_holding_slots)]
    if not holding.index.equals(expected_holding_slots):
        result = _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="missing_or_incomplete_outcome_window",
            event_contract=event_contract,
        )
        result.update({"filled": True, "entry_at": fill_at.isoformat(), "entry": fill_price})
        return result

    outcome = "timeout"
    reason = "deadline_close"
    exit_at = deadline
    exit_price = float(holding.iloc[-1].close) * (1 - slip)
    ambiguity = False
    for number, (stamp, row) in enumerate(holding.iterrows()):
        is_fill_bar = number == 0
        gap_stop = float(row.open) <= stop
        gap_target = float(row.open) >= target
        stop_touch = float(row.low) <= stop
        target_touch = float(row.high) >= target
        if gap_stop:
            outcome, reason = "stop", "gap_through_stop"
            exit_price = float(row.open) * (1 - slip)
        elif gap_target:
            outcome, reason = "target", "gap_through_target_capped_at_target"
            exit_price = target * (1 - slip)
        elif stop_touch and target_touch:
            outcome, reason = "stop", "same_bar_target_stop_ambiguity"
            exit_price = stop * (1 - slip)
            ambiguity = True
        elif stop_touch:
            outcome = "stop"
            reason = (
                "fill_bar_adverse_path_ambiguity"
                if is_fill_bar and fill_kind == "intrabar_trigger"
                else "stop_touch"
            )
            exit_price = stop * (1 - slip)
            ambiguity = is_fill_bar and fill_kind == "intrabar_trigger"
        elif target_touch:
            outcome, reason = "target", "target_touch"
            exit_price = target * (1 - slip)
        else:
            continue
        exit_at = pd.Timestamp(stamp) + M1
        break

    entry_decimal, exit_decimal = price_decimal(fill_price), price_decimal(exit_price)
    charges = costs.round_trip_cost(
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        entry_price=entry_decimal,
        exit_price=exit_decimal,
        dp_applies=False,
    ).total
    gross = (exit_decimal - entry_decimal) * Decimal(quantity)
    net = gross - charges
    gross_risk = (entry_decimal - price_decimal(stop)) * Decimal(quantity)
    gross_r = gross / gross_risk
    net_r = net / gross_risk
    target_hit = outcome == "target"
    strict = target_hit and net > 0
    return {
        "opportunity_id": opportunity.identifier,
        "mode": opportunity.mode,
        "geometry_id": geometry.id,
        "status": "resolved",
        "outcome": outcome,
        "reason": reason,
        "filled": True,
        "strict_success": bool(strict),
        "target_hit": target_hit,
        "profitable_timeout": outcome == "timeout" and net > 0,
        "entry_at": fill_at.isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry": fill_price,
        "stop": stop,
        "target": target,
        "exit": float(exit_decimal),
        "qty": quantity,
        "costs": float(charges),
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "gross_r": float(gross_r),
        "net_r": float(net_r),
        "fill_kind": fill_kind,
        "ambiguous_bar_resolved_adversely": ambiguity,
        "ambiguity_policy": event_contract.ambiguity_policy,
        "decision_before_entry": decision_at <= fill_at,
        "entry_deadline_at": expiry.isoformat(),
        "exit_deadline_at": deadline.isoformat(),
        "strategy_contract_sha256": opportunity.strategy_contract_sha256,
        "event_contract_sha256": opportunity.event_contract_sha256,
    }
