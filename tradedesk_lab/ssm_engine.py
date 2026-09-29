"""Causal Same-Slot Micro-Momentum features, calls, and conservative outcomes.

The research engine predicts a fixed half-hour before that slot begins.  It uses
only completed earlier bars and prior sessions, excludes the candidate from peer
statistics, and resolves the next-slot-open paper fill under adverse ambiguity.
It cannot emit a live alert, place an order, or change production behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.markets.costs import CostModel
from tradedesk.models import Side, TradeType, price_decimal
from tradedesk_lab.ssm_contract import (
    DEFAULT_SSM_CONTRACT,
    SsmContract,
    SsmGeometry,
)

IST = "Asia/Kolkata"
M1 = pd.Timedelta(minutes=1)
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class SsmEngineContract:
    version: str = "ssm-engine-v1"
    session_open: str = "09:15"
    session_close: str = "15:30"
    bar_minutes: int = 1
    slot_minutes: int = 30
    decision_lead_minutes: int = 1
    tick_size: float = 0.05
    entry_policy: str = "next_slot_open_market_with_slippage"
    ambiguity_policy: str = "adverse_stop_first"

    def __post_init__(self) -> None:
        if self.session_open != "09:15" or self.session_close != "15:30":
            raise ValueError("SSM must use the regular NSE cash session")
        if self.bar_minutes != 1 or self.slot_minutes != 30:
            raise ValueError("SSM engine is frozen to M1 bars and 30-minute slots")
        if self.decision_lead_minutes != 1:
            raise ValueError("SSM prediction must precede entry by one minute")
        if not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("tick size must be positive and finite")
        if self.entry_policy != "next_slot_open_market_with_slippage":
            raise ValueError("SSM entry policy cannot change inside v1")
        if self.ambiguity_policy != "adverse_stop_first":
            raise ValueError("M1 ambiguity must fail adversely")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


DEFAULT_SSM_ENGINE_CONTRACT = SsmEngineContract()


@dataclass(frozen=True)
class SsmOpportunity:
    identifier: str
    scrip_code: str
    symbol: str
    session: str
    slot_start: str
    slot_end: str
    decision_at: str
    entry_at: str
    mode: str
    features: dict[str, float]
    peer_count: int
    history_sessions: int
    strategy_contract_sha256: str
    engine_contract_sha256: str


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


def _as_session(value: str | date | pd.Timestamp) -> date:
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_session") from exc


def _at(session: date, clock: str) -> pd.Timestamp:
    try:
        hour, minute = (int(item) for item in clock.split(":"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid_slot_start") from exc
    return pd.Timestamp(session, tz=IST) + pd.Timedelta(hours=hour, minutes=minute)


def _validate_values(frame: pd.DataFrame, *, error: str) -> pd.DataFrame:
    try:
        bars = frame.loc[:, REQUIRED_COLUMNS].copy()
    except (KeyError, TypeError) as exc:
        raise ValueError(error) from exc
    bars.index = _ist_index(bars.index)
    idx = pd.DatetimeIndex(bars.index)
    if bars.empty or idx.hasnans or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError(error)
    if not ((idx.second == 0) & (idx.microsecond == 0)).all():
        raise ValueError(error)
    try:
        values = bars.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc
    prices, volume = values[:, :4], values[:, 4]
    if (
        not np.isfinite(values).all()
        or (prices <= 0).any()
        or (volume < 0).any()
        or (bars.low.to_numpy() > prices[:, [0, 3]].min(axis=1)).any()
        or (bars.high.to_numpy() < prices[:, [0, 3]].max(axis=1)).any()
        or (bars.high.to_numpy() < bars.low.to_numpy()).any()
    ):
        raise ValueError(error)
    return bars


def _causal_m1(frame: pd.DataFrame, *, session: date, decision_at: pd.Timestamp) -> pd.DataFrame:
    """Discard the predicted slot and every later row before validating values."""

    try:
        source = frame.loc[:, REQUIRED_COLUMNS].copy()
    except (KeyError, TypeError) as exc:
        raise ValueError("missing_m1_columns") from exc
    source.index = _ist_index(source.index)
    idx = pd.DatetimeIndex(source.index)
    mask = np.array(
        [
            stamp.date() < session or (stamp.date() == session and stamp < decision_at)
            for stamp in idx
        ]
    )
    causal = source.loc[mask]
    return _validate_values(causal, error="invalid_causal_m1_history")


def _exact_window(
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp,
    minutes: int,
    error: str,
) -> pd.DataFrame:
    expected = pd.date_range(start, periods=minutes, freq="1min")
    selected = frame.loc[frame.index.isin(expected)]
    if not selected.index.equals(expected):
        raise ValueError(error)
    return selected


def _slot_record(frame: pd.DataFrame, *, session: date, clock: str) -> dict[str, float] | None:
    start = _at(session, clock)
    expected = pd.date_range(start, periods=30, freq="1min")
    selected = frame.loc[frame.index.isin(expected)]
    if not selected.index.equals(expected):
        return None
    slot_return = float(selected.close.iloc[-1] / selected.open.iloc[0] - 1)
    typical = (selected.high + selected.low + selected.close) / 3
    return {
        "return": slot_return,
        "volume": float(selected.volume.sum()),
        "turnover": float((typical * selected.volume).sum()),
    }


def _candidate_history(
    frame: pd.DataFrame,
    *,
    session: date,
    clock: str,
    contract: SsmContract,
) -> list[tuple[date, dict[str, float]]]:
    dates = sorted({stamp.date() for stamp in frame.index if stamp.date() < session})
    records = []
    for prior in dates:
        value = _slot_record(frame, session=prior, clock=clock)
        if value is not None:
            records.append((prior, value))
    return records[-contract.history_lookback_sessions :]


def _records_on_dates(
    frame: pd.DataFrame,
    *,
    dates: list[date],
    clock: str,
) -> list[dict[str, float]] | None:
    records = [_slot_record(frame, session=item, clock=clock) for item in dates]
    return None if any(item is None for item in records) else records  # type: ignore[return-value]


def _pre_slot_return(
    frame: pd.DataFrame,
    *,
    session: date,
    decision_at: pd.Timestamp,
) -> tuple[float, pd.DataFrame]:
    session_open = _at(session, "09:15")
    minutes = int((decision_at - session_open) / M1)
    if minutes < 1:
        raise ValueError("insufficient_pre_slot_history")
    bars = _exact_window(
        frame,
        start=session_open,
        minutes=minutes,
        error="incomplete_pre_slot_history",
    )
    return float(bars.close.iloc[-1] / bars.open.iloc[0] - 1), bars


def _rank(candidate: float, peers: list[float]) -> float:
    if not peers:
        raise ValueError("insufficient_peer_universe")
    return float(np.mean(np.asarray(peers, dtype=float) <= candidate))


def _daily_turnover(frame: pd.DataFrame, *, session: date) -> float:
    try:
        daily = frame.loc[:, REQUIRED_COLUMNS].copy()
    except (KeyError, TypeError) as exc:
        raise ValueError("missing_daily_columns") from exc
    idx = _ist_index(daily.index)
    daily.index = idx
    daily = daily.loc[[stamp.date() < session for stamp in idx]]
    daily = _validate_values(daily, error="invalid_prior_daily_history")
    dates = [stamp.date() for stamp in daily.index]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate_prior_daily_sessions")
    recent = daily.iloc[-20:]
    if len(recent) < 20:
        raise ValueError("insufficient_prior_daily_history")
    turnover = float((recent.close * recent.volume).median())
    if not math.isfinite(turnover) or turnover <= 0:
        raise ValueError("invalid_prior_daily_turnover")
    return turnover


def _round_trip_cost_pct(costs: CostModel, *, quantity: int, price: float) -> float:
    buy = costs.leg_cost(
        side=Side.BUY,
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        price=price_decimal(price),
    ).total
    sell = costs.leg_cost(
        side=Side.SELL,
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        price=price_decimal(price),
        dp_applies=False,
    ).total
    notional = Decimal(str(quantity)) * price_decimal(price)
    return float((buy + sell) / notional)


def compute_slot_features(
    *,
    scrip_code: str,
    session: str | date | pd.Timestamp,
    slot_start: str,
    frame: pd.DataFrame,
    daily_frame: pd.DataFrame,
    universe_frames: dict[str, pd.DataFrame],
    quantity: int,
    costs: CostModel,
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
    engine_contract: SsmEngineContract = DEFAULT_SSM_ENGINE_CONTRACT,
) -> dict[str, Any]:
    """Compute exactly the 24 frozen features before a predicted slot begins."""

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if slot_start not in contract.slot_starts:
        raise ValueError("slot start is not in the frozen registry")
    current_session = _as_session(session)
    entry_at = _at(current_session, slot_start)
    decision_at = entry_at - pd.Timedelta(minutes=engine_contract.decision_lead_minutes)
    candidate = _causal_m1(frame, session=current_session, decision_at=decision_at)
    history = _candidate_history(
        candidate,
        session=current_session,
        clock=slot_start,
        contract=contract,
    )
    if len(history) < contract.minimum_history_sessions:
        raise ValueError("insufficient_same_slot_history")
    history_dates = [item[0] for item in history]
    records = [item[1] for item in history]
    returns = np.asarray([item["return"] for item in records], dtype=float)
    volumes = np.asarray([item["volume"] for item in records], dtype=float)
    turnovers = np.asarray([item["turnover"] for item in records], dtype=float)
    pre_return, pre_bars = _pre_slot_return(
        candidate,
        session=current_session,
        decision_at=decision_at,
    )

    peer_values: list[dict[str, float]] = []
    for code, raw_peer in universe_frames.items():
        if str(code) == str(scrip_code):
            continue
        try:
            peer = _causal_m1(raw_peer, session=current_session, decision_at=decision_at)
            peer_records = _records_on_dates(
                peer,
                dates=history_dates,
                clock=slot_start,
            )
            if peer_records is None:
                continue
            peer_returns = np.asarray([item["return"] for item in peer_records], dtype=float)
            peer_pre_return, _ = _pre_slot_return(
                peer,
                session=current_session,
                decision_at=decision_at,
            )
        except ValueError:
            continue
        peer_values.append(
            {
                "lag1": float(peer_returns[-1]),
                "lag5": float(peer_returns[-5:].mean()),
                "lag20": float(peer_returns[-20:].mean()),
                "pre": peer_pre_return,
            }
        )
    if len(peer_values) < contract.minimum_cross_sectional_peers:
        raise ValueError("insufficient_peer_universe")

    lag1 = float(returns[-1])
    lag5 = float(returns[-5:].mean())
    lag20 = float(returns[-20:].mean())
    lag40 = float(returns.mean())
    peer_lag1 = [item["lag1"] for item in peer_values]
    peer_lag5 = [item["lag5"] for item in peer_values]
    peer_lag20 = [item["lag20"] for item in peer_values]
    peer_pre = [item["pre"] for item in peer_values]
    reference_price = float(pre_bars.close.iloc[-1])
    pre_turnover = float(
        (((pre_bars.high + pre_bars.low + pre_bars.close) / 3) * pre_bars.volume).sum()
    )
    median_turnover = _daily_turnover(daily_frame, session=current_session)
    values = {
        "slot_index": float(contract.slot_starts.index(slot_start)),
        "minutes_from_open": float((entry_at - _at(current_session, "09:15")) / M1),
        "lag1_same_slot_return": lag1,
        "lag2_same_slot_return": float(returns[-2]),
        "lag3_same_slot_return": float(returns[-3]),
        "lag5_mean_same_slot_return": lag5,
        "lag20_mean_same_slot_return": lag20,
        "lag40_mean_same_slot_return": lag40,
        "lag5_positive_share": float(np.mean(returns[-5:] > 0)),
        "lag20_positive_share": float(np.mean(returns[-20:] > 0)),
        "lag40_positive_share": float(np.mean(returns > 0)),
        "same_slot_return_std_40": float(np.std(returns, ddof=0)),
        "lag1_cross_sectional_rank": _rank(lag1, peer_lag1),
        "lag5_cross_sectional_rank": _rank(lag5, peer_lag5),
        "lag20_cross_sectional_rank": _rank(lag20, peer_lag20),
        "same_slot_cross_sectional_dispersion": float(np.std(peer_lag1, ddof=0)),
        "lag5_same_slot_volume_ratio": float(
            volumes[-5:].mean() / max(volumes[-20:].mean(), 1e-12)
        ),
        "lag20_same_slot_turnover": float(turnovers[-20:].mean()),
        "pre_slot_day_return": pre_return,
        "pre_slot_cross_sectional_rank": _rank(pre_return, peer_pre),
        "pre_slot_breadth": float(np.mean(np.asarray(peer_pre) > 0)),
        "median_turnover_20d": median_turnover,
        "impact_proxy": float(quantity * reference_price / max(pre_turnover, 1e-12)),
        "modeled_round_trip_cost_pct": _round_trip_cost_pct(
            costs,
            quantity=quantity,
            price=reference_price,
        ),
    }
    expected = {feature.name for feature in contract.features}
    if set(values) != expected or any(not math.isfinite(value) for value in values.values()):
        raise ValueError("invalid_frozen_feature_vector")
    return {
        "available_at": decision_at.isoformat(),
        "entry_at": entry_at.isoformat(),
        "slot_end": (entry_at + pd.Timedelta(minutes=contract.slot_minutes)).isoformat(),
        "history_sessions": len(history),
        "peer_count": len(peer_values),
        "values": values,
        "strategy_contract_sha256": contract.sha256,
        "engine_contract_sha256": engine_contract.sha256,
    }


def opportunities_for_slot(
    *,
    scrip_code: str,
    symbol: str,
    session: str | date | pd.Timestamp,
    slot_start: str,
    frame: pd.DataFrame,
    daily_frame: pd.DataFrame,
    universe_frames: dict[str, pd.DataFrame],
    quantity: int,
    costs: CostModel,
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
    engine_contract: SsmEngineContract = DEFAULT_SSM_ENGINE_CONTRACT,
) -> tuple[SsmOpportunity, ...]:
    snapshot = compute_slot_features(
        scrip_code=scrip_code,
        session=session,
        slot_start=slot_start,
        frame=frame,
        daily_frame=daily_frame,
        universe_frames=universe_frames,
        quantity=quantity,
        costs=costs,
        contract=contract,
        engine_contract=engine_contract,
    )
    values = snapshot["values"]
    modes = []
    if (
        values["lag1_same_slot_return"] > 0
        and values["lag1_cross_sectional_rank"] >= contract.transparent_rank_threshold
    ):
        modes.append("lag1_cross_sectional")
    if (
        values["lag20_mean_same_slot_return"] > 0
        and values["lag20_positive_share"] >= contract.minimum_positive_persistence_share
        and values["lag20_cross_sectional_rank"] >= contract.transparent_rank_threshold
    ):
        modes.append("multi_day_persistence")

    current_session = _as_session(session)
    result = []
    for mode in modes:
        identifier = f"SSM_v1:{scrip_code}:{current_session}:{slot_start.replace(':', '')}:{mode}"
        result.append(
            SsmOpportunity(
                identifier=identifier,
                scrip_code=scrip_code,
                symbol=symbol,
                session=str(current_session),
                slot_start=slot_start,
                slot_end=snapshot["slot_end"],
                decision_at=snapshot["available_at"],
                entry_at=snapshot["entry_at"],
                mode=mode,
                features=dict(values),
                peer_count=int(snapshot["peer_count"]),
                history_sessions=int(snapshot["history_sessions"]),
                strategy_contract_sha256=contract.sha256,
                engine_contract_sha256=engine_contract.sha256,
            )
        )
    return tuple(result)


def _ceil_tick(value: float, tick_size: float) -> float:
    tick = Decimal(str(tick_size))
    ticks = (Decimal(str(value)) / tick).to_integral_value(rounding=ROUND_CEILING)
    return float(ticks * tick)


def _floor_tick(value: float, tick_size: float) -> float:
    tick = Decimal(str(tick_size))
    ticks = (Decimal(str(value)) / tick).to_integral_value(rounding=ROUND_FLOOR)
    return float(ticks * tick)


def _outcome_stub(
    opportunity: SsmOpportunity,
    geometry: SsmGeometry,
    *,
    status: str,
    outcome: str,
    reason: str,
    engine_contract: SsmEngineContract,
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
        "ambiguity_policy": engine_contract.ambiguity_policy,
    }


def resolve_opportunity(
    opportunity: SsmOpportunity,
    frame: pd.DataFrame,
    geometry: SsmGeometry,
    *,
    quantity: int,
    costs: CostModel,
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
    engine_contract: SsmEngineContract = DEFAULT_SSM_ENGINE_CONTRACT,
) -> dict[str, Any]:
    """Resolve one fixed half-hour with conservative gaps, ambiguity, and costs."""

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if opportunity.strategy_contract_sha256 != contract.sha256:
        raise ValueError("opportunity strategy contract differs")
    if opportunity.engine_contract_sha256 != engine_contract.sha256:
        raise ValueError("opportunity engine contract differs")
    if opportunity.mode not in contract.predictor_modes:
        raise ValueError("opportunity mode is not in the frozen registry")
    if geometry not in contract.geometries:
        raise ValueError("geometry is not in the frozen SSM registry")
    entry_at = pd.Timestamp(opportunity.entry_at)
    entry_at = entry_at.tz_localize(IST) if entry_at.tz is None else entry_at.tz_convert(IST)
    decision_at = pd.Timestamp(opportunity.decision_at)
    decision_at = (
        decision_at.tz_localize(IST) if decision_at.tz is None else decision_at.tz_convert(IST)
    )
    if decision_at + pd.Timedelta(minutes=engine_contract.decision_lead_minutes) != entry_at:
        raise ValueError("opportunity decision does not precede entry by one minute")

    expected = pd.date_range(entry_at, periods=contract.slot_minutes, freq="1min")
    try:
        source = frame.loc[:, REQUIRED_COLUMNS].copy()
        source.index = _ist_index(source.index)
    except (KeyError, TypeError, ValueError):
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="missing_or_invalid_outcome_columns",
            engine_contract=engine_contract,
        )
    selected = source.loc[source.index.isin(expected)]
    if selected.empty or selected.index[0] != entry_at:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="missing_entry_bar",
            engine_contract=engine_contract,
        )
    try:
        selected = _validate_values(selected, error="invalid_outcome_window")
    except ValueError as exc:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason=str(exc),
            engine_contract=engine_contract,
        )
    slip = float(costs.slippage_pct)
    entry = _ceil_tick(float(selected.open.iloc[0]) * (1 + slip), engine_contract.tick_size)
    if float(selected.volume.iloc[0]) <= 0:
        return _outcome_stub(
            opportunity,
            geometry,
            status="unfilled",
            outcome="no_entry_liquidity",
            reason="entry_bar_has_zero_volume",
            engine_contract=engine_contract,
        )
    if not selected.index.equals(expected):
        result = _outcome_stub(
            opportunity,
            geometry,
            status="unresolved",
            outcome="data_failure",
            reason="missing_or_incomplete_outcome_window",
            engine_contract=engine_contract,
        )
        result.update(
            filled=True,
            entry_at=entry_at.isoformat(),
            entry=entry,
            qty=quantity,
        )
        return result

    stop = _floor_tick(entry * (1 - geometry.stop_pct), engine_contract.tick_size)
    target = _ceil_tick(entry * (1 + geometry.target_pct), engine_contract.tick_size)
    if not 0 < stop < entry < target:
        raise ValueError("invalid realized geometry")

    exit_price: float | None = None
    exit_at: pd.Timestamp | None = None
    outcome = "time_exit"
    reason = "slot_deadline"
    ambiguous = False
    bars_held = 0
    for number, (stamp, row) in enumerate(selected.iterrows(), start=1):
        open_price, high, low = float(row.open), float(row.high), float(row.low)
        if open_price <= stop:
            outcome, reason = "gap_stop", "opened_at_or_below_stop"
            exit_price = _floor_tick(open_price * (1 - slip), engine_contract.tick_size)
        elif open_price >= target:
            outcome, reason = "target", "favorable_gap_capped_at_target"
            exit_price = _floor_tick(target * (1 - slip), engine_contract.tick_size)
        elif low <= stop and high >= target:
            outcome, reason = "stop", "same_bar_target_stop_ambiguity"
            ambiguous = True
            exit_price = _floor_tick(stop * (1 - slip), engine_contract.tick_size)
        elif low <= stop:
            outcome, reason = "stop", "stop_touched"
            exit_price = _floor_tick(stop * (1 - slip), engine_contract.tick_size)
        elif high >= target:
            outcome, reason = "target", "target_touched"
            exit_price = _floor_tick(target * (1 - slip), engine_contract.tick_size)
        elif number == len(selected):
            exit_price = _floor_tick(float(row.close) * (1 - slip), engine_contract.tick_size)
        if exit_price is not None:
            exit_at = pd.Timestamp(stamp)
            bars_held = number
            break
    assert exit_price is not None and exit_at is not None

    buy = costs.leg_cost(
        side=Side.BUY,
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        price=price_decimal(entry),
    ).total
    sell = costs.leg_cost(
        side=Side.SELL,
        trade_type=TradeType.INTRADAY,
        qty=quantity,
        price=price_decimal(exit_price),
        dp_applies=False,
    ).total
    charges = buy + sell
    gross = (price_decimal(exit_price) - price_decimal(entry)) * Decimal(str(quantity))
    net = gross - charges
    initial_risk = (price_decimal(entry) - price_decimal(stop)) * Decimal(str(quantity))
    target_hit = outcome == "target"
    strict_success = target_hit and net > 0
    return {
        "opportunity_id": opportunity.identifier,
        "mode": opportunity.mode,
        "geometry_id": geometry.id,
        "status": "resolved",
        "outcome": outcome,
        "reason": reason,
        "filled": True,
        "strict_success": bool(strict_success),
        "target_hit": target_hit,
        "profitable_timeout": outcome == "time_exit" and net > 0,
        "entry_at": entry_at.isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "qty": quantity,
        "bars_held": bars_held,
        "costs": float(charges),
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "gross_r": float(gross / initial_risk),
        "net_r": float(net / initial_risk),
        "ambiguity_policy": engine_contract.ambiguity_policy,
        "ambiguous_bar_resolved_adversely": ambiguous,
        "decision_before_entry": decision_at < entry_at,
    }
