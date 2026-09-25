"""Causal AEM v2 opportunity reconstruction and conservative M1 outcomes.

This is an isolated research engine.  It does not rank opportunities, train a
model, create a production signal, or place an order.  A prediction exists only
after its signal bar has closed; fills and outcomes are evaluated on later M1
bars under an explicitly adverse ambiguity policy.
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
from tradedesk_lab.aem_v2_contract import (
    DEFAULT_AEM_V2_CONTRACT,
    AemV2Geometry,
)

IST = "Asia/Kolkata"
M1 = pd.Timedelta(minutes=1)
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class AemV2EventEngineContract:
    """Versioned M1 ordering and execution rules for Milestone 1.

    These are opportunity-generator hypotheses, not fitted parameters and not a
    performance claim.  Later experiments must consume this fingerprint.
    """

    version: str = "aem-v2-event-engine-v1"
    session_open: str = "09:15"
    earliest_decision_time: str = "09:20"
    latest_decision_time: str = "11:00"
    bar_minutes: int = 1
    causal_level_lookback_bars: int = 5
    impulse_lookback_bars: int = 3
    structure_lookback_bars: int = 8
    minimum_impulse_return: float = 0.001
    minimum_volume_acceleration: float = 1.20
    maximum_level_distance: float = 0.0075
    vwap_hold_tolerance: float = 0.0010
    retest_tolerance: float = 0.0015
    maximum_chase_pct: float = 0.0015
    entry_valid_minutes: int = 3
    tick_size: float = 0.05
    ambiguity_policy: str = "adverse_stop_first"

    def __post_init__(self) -> None:
        if self.bar_minutes != 1:
            raise ValueError("AEM v2 event reconstruction is frozen to M1 bars")
        if not self.session_open < self.earliest_decision_time <= self.latest_decision_time:
            raise ValueError("invalid event decision window")
        if min(
            self.causal_level_lookback_bars,
            self.impulse_lookback_bars,
            self.structure_lookback_bars,
            self.entry_valid_minutes,
        ) < 1:
            raise ValueError("event lookbacks and entry validity must be positive")
        proportions = (
            self.minimum_impulse_return,
            self.maximum_level_distance,
            self.vwap_hold_tolerance,
            self.retest_tolerance,
            self.maximum_chase_pct,
        )
        if any(not math.isfinite(value) or value <= 0 for value in proportions):
            raise ValueError("event thresholds must be positive finite proportions")
        if self.minimum_volume_acceleration <= 1:
            raise ValueError("volume acceleration must exceed one")
        if self.tick_size <= 0 or not math.isfinite(self.tick_size):
            raise ValueError("tick size must be positive and finite")
        if self.ambiguity_policy != "adverse_stop_first":
            raise ValueError("M1 ambiguity must fail adversely")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


DEFAULT_EVENT_ENGINE_CONTRACT = AemV2EventEngineContract()


@dataclass(frozen=True)
class AemV2Opportunity:
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
    causal_level: float
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
    """Return validated M1 bars, optionally ignoring every later row."""

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
    result = (typical * bars.volume).cumsum() / cumulative_volume.replace(0, np.nan)
    return result.ffill()


def _impulse_rows(bars: pd.DataFrame, contract: AemV2EventEngineContract) -> list[int]:
    result: list[int] = []
    for position in range(contract.impulse_lookback_bars, len(bars)):
        start = position - contract.impulse_lookback_bars
        before = float(bars.close.iloc[start])
        change = float(bars.close.iloc[position]) / before - 1
        prior_volume = float(bars.volume.iloc[start:position].median())
        acceleration = float(bars.volume.iloc[position]) / prior_volume if prior_volume > 0 else 0.0
        row = bars.iloc[position]
        bullish = float(row.close) > float(row.open)
        if (
            change >= contract.minimum_impulse_return
            and acceleration >= contract.minimum_volume_acceleration
            and bullish
        ):
            result.append(position)
    return result


def opportunities_at(
    frame: pd.DataFrame,
    *,
    at: pd.Timestamp,
    scrip_code: str,
    symbol: str,
    event_contract: AemV2EventEngineContract = DEFAULT_EVENT_ENGINE_CONTRACT,
) -> tuple[AemV2Opportunity, ...]:
    """Evaluate the three registered entry modes at one completed-bar boundary.

    ``at`` is the close time of the signal bar and the prediction timestamp.  A
    row whose open timestamp is ``at`` is deliberately excluded.
    """

    when = pd.Timestamp(at)
    when = when.tz_localize(IST) if when.tz is None else when.tz_convert(IST)
    if when != when.floor("min"):
        raise ValueError("unaligned_decision_timestamp")
    latest_open = when - M1
    bars = _validated_bars(frame, through_open=latest_open)
    if bars.index[-1] + M1 != when:
        raise ValueError("stale_signal_bar")
    clock = when.strftime("%H:%M")
    if not event_contract.earliest_decision_time <= clock <= event_contract.latest_decision_time:
        return ()
    minimum = max(
        event_contract.causal_level_lookback_bars + 1,
        event_contract.impulse_lookback_bars + 2,
    )
    if len(bars) < minimum:
        return ()

    current_position = len(bars) - 1
    current = bars.iloc[current_position]
    level_start = max(0, current_position - event_contract.causal_level_lookback_bars)
    causal_level = float(bars.high.iloc[level_start:current_position].max())
    level_distance = causal_level / float(current.close) - 1
    vwap = _vwap(bars)
    impulses = _impulse_rows(bars, event_contract)
    recent_start = max(0, current_position - event_contract.structure_lookback_bars)
    recent_impulses = [p for p in impulses if recent_start <= p < current_position]
    three_start = max(0, current_position - event_contract.impulse_lookback_bars)
    return_3m = float(current.close) / float(bars.close.iloc[three_start]) - 1
    prior_volume = float(bars.volume.iloc[three_start:current_position].median())
    volume_acceleration = float(current.volume) / prior_volume if prior_volume > 0 else 0.0
    modes: list[tuple[str, int]] = []

    # Developing acceleration remains below a previously observed level with
    # enough causal room for at least the smallest registered target.
    minimum_room = min(g.target_pct for g in DEFAULT_AEM_V2_CONTRACT.geometries)
    anticipatory = bool(
        return_3m >= event_contract.minimum_impulse_return
        and volume_acceleration >= event_contract.minimum_volume_acceleration
        and float(current.close) > float(current.open)
        and minimum_room <= level_distance <= event_contract.maximum_level_distance
    )
    if anticipatory:
        modes.append(("anticipatory_impulse", three_start))

    # A prior completed impulse, then a completed VWAP-holding retracement,
    # then the current completed resumption bar.
    for impulse_position in reversed(recent_impulses):
        pullbacks = bars.iloc[impulse_position + 1 : current_position]
        if pullbacks.empty:
            continue
        pullback_positions = range(impulse_position + 1, current_position)
        held = [
            p
            for p in pullback_positions
            if float(bars.low.iloc[p])
            >= float(vwap.iloc[p]) * (1 - event_contract.vwap_hold_tolerance)
            and float(bars.close.iloc[p]) >= float(vwap.iloc[p])
            and float(bars.close.iloc[p]) < float(bars.close.iloc[impulse_position])
        ]
        if held and float(current.close) > float(bars.high.iloc[current_position - 1]):
            modes.append(("confirmed_pullback", impulse_position))
            break

    # Breakout, later retest/hold, and still-later continuation must be three
    # separately completed observations in that order.
    breakout_start = max(event_contract.causal_level_lookback_bars, recent_start)
    for breakout_position in range(breakout_start, current_position - 1):
        prior = bars.iloc[
            breakout_position - event_contract.causal_level_lookback_bars : breakout_position
        ]
        breakout_level = float(prior.high.max())
        if float(bars.close.iloc[breakout_position]) <= breakout_level:
            continue
        retests = [
            p
            for p in range(breakout_position + 1, current_position)
            if float(bars.low.iloc[p]) <= breakout_level * (1 + event_contract.retest_tolerance)
            and float(bars.low.iloc[p]) >= breakout_level * (1 - event_contract.retest_tolerance)
            and float(bars.close.iloc[p]) >= breakout_level
        ]
        if (
            retests
            and float(current.close) > float(bars.high.iloc[current_position - 1])
            and float(current.close) > breakout_level
        ):
            modes.append(("breakout_retest", breakout_position))
            break

    opportunities = []
    for mode, state_position in modes:
        intended = _ceil_tick(float(current.close), event_contract.tick_size)
        limit = _ceil_tick(
            intended * (1 + event_contract.maximum_chase_pct), event_contract.tick_size
        )
        decision = when.isoformat()
        identifier = f"AEM_v2:{scrip_code}:{when.date()}:{mode}:{when.strftime('%H%M')}"
        opportunities.append(
            AemV2Opportunity(
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
                causal_level=causal_level,
                decision_values={
                    "signal_close": float(current.close),
                    "causal_level": causal_level,
                    "level_distance": level_distance,
                    "return_3m": return_3m,
                    "volume_acceleration": volume_acceleration,
                    "vwap": float(vwap.iloc[-1]),
                },
                strategy_contract_sha256=DEFAULT_AEM_V2_CONTRACT.sha256,
                event_contract_sha256=event_contract.sha256,
            )
        )
    return tuple(opportunities)


def reconstruct_session_opportunities(
    frame: pd.DataFrame,
    *,
    scrip_code: str,
    symbol: str,
    event_contract: AemV2EventEngineContract = DEFAULT_EVENT_ENGINE_CONTRACT,
) -> tuple[AemV2Opportunity, ...]:
    """Reconstruct every causal opportunity from one isolated M1 session."""

    raw_index = _ist_index(frame.index)
    if raw_index.empty:
        raise ValueError("missing_m1_bars")
    latest_signal_open = raw_index[0].normalize() + pd.Timedelta(hours=10, minutes=59)
    bars = _validated_bars(frame, through_open=latest_signal_open)
    result: list[AemV2Opportunity] = []
    for stamp in bars.index:
        at = stamp + M1
        if at.strftime("%H:%M") < event_contract.earliest_decision_time:
            continue
        if at.strftime("%H:%M") > event_contract.latest_decision_time:
            break
        result.extend(
            opportunities_at(
                bars,
                at=at,
                scrip_code=scrip_code,
                symbol=symbol,
                event_contract=event_contract,
            )
        )
    return tuple(result)


def _outcome_stub(
    opportunity: AemV2Opportunity,
    geometry: AemV2Geometry,
    *,
    status: str,
    outcome: str,
    reason: str,
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
        "ambiguity_policy": DEFAULT_EVENT_ENGINE_CONTRACT.ambiguity_policy,
    }


def resolve_opportunity(
    opportunity: AemV2Opportunity,
    frame: pd.DataFrame,
    geometry: AemV2Geometry,
    *,
    quantity: int,
    costs: CostModel,
    event_contract: AemV2EventEngineContract = DEFAULT_EVENT_ENGINE_CONTRACT,
) -> dict[str, Any]:
    """Apply conservative fill, barrier, deadline, slippage, and cost rules."""

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if opportunity.strategy_contract_sha256 != DEFAULT_AEM_V2_CONTRACT.sha256:
        raise ValueError("opportunity strategy contract differs")
    if opportunity.event_contract_sha256 != event_contract.sha256:
        raise ValueError("opportunity event contract differs")
    if geometry not in DEFAULT_AEM_V2_CONTRACT.geometries:
        raise ValueError("geometry is not in the frozen AEM v2 registry")
    decision_at = pd.Timestamp(opportunity.decision_at)
    decision_at = (
        decision_at.tz_localize(IST) if decision_at.tz is None else decision_at.tz_convert(IST)
    )
    expiry = decision_at + pd.Timedelta(minutes=event_contract.entry_valid_minutes)
    try:
        bars = _validated_bars(frame, through_open=expiry - M1)
    except ValueError as exc:
        return _outcome_stub(
            opportunity, geometry, status="unresolved", outcome="data_failure", reason=str(exc)
        )
    if bars.index[-1] < decision_at:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="no_post_decision_bar",
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
        )
    deadline = fill_at + pd.Timedelta(minutes=geometry.max_hold_minutes)
    expected_holding_slots = pd.date_range(fill_at, deadline - M1, freq="1min")
    try:
        bars = _validated_bars(frame, through_open=deadline - M1)
    except ValueError as exc:
        result = _outcome_stub(
            opportunity, geometry, status="unresolved", outcome="data_failure", reason=str(exc)
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
            # For an intrabar long trigger, a higher target cannot occur before
            # price crosses the entry trigger.  It is therefore causally usable.
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
