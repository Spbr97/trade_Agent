"""Deterministic daily-bar resolver for versioned prediction contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any

import pandas as pd

from tradedesk.config.models import ChargeSchedule, CryptoChargeSchedule
from tradedesk.evidence import CONTRACTS, ContractKind, OutcomeState, classify_outcome
from tradedesk.markets.costs import CryptoCostModel, EquityCostModel
from tradedesk.markets.tax import after_tax_r, tds_share_of_costs
from tradedesk.models import TradeType, price_decimal

_OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True)
class DeterministicOutcome:
    outcome: str
    outcome_state: str
    label: int | None
    entry_on: str | None = None
    entry_price: float | None = None
    exit_on: str | None = None
    exit_price: float | None = None
    gross_r: float | None = None
    execution_r: float | None = None
    net_r: float | None = None
    after_tax_r: float | None = None
    fees: float | None = None
    holding_sessions: int | None = None
    time_to_entry_sessions: int | None = None
    time_to_resolution_sessions: int | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    first_event: str | None = None
    resolution_rule: str = "stop_before_target"
    data_status: str = "complete"


def _dates(index: pd.Index) -> list[date]:
    values = pd.DatetimeIndex(index)
    if values.tz is not None:
        values = values.tz_convert("Asia/Kolkata")
    return list(values.date)


def _invalid(reason: str, *, on: date | None = None) -> DeterministicOutcome:
    return DeterministicOutcome(
        outcome="unavailable",
        outcome_state=OutcomeState.INVALID_CALL.value,
        label=None,
        exit_on=on.isoformat() if on else None,
        first_event=reason,
        data_status="invalid",
    )


def _cost_model(row: Any) -> EquityCostModel | CryptoCostModel | None:
    payload = row.prediction_payload or {}
    assumptions = ((payload.get("execution") or {}).get("assumptions") or {})
    schedule = assumptions.get("fee_tax_schedule")
    if not schedule:
        return None
    if row.market == "crypto":
        return CryptoCostModel(CryptoChargeSchedule.model_validate(schedule))
    if row.market in {"nse", "bse"}:
        return EquityCostModel(ChargeSchedule.model_validate(schedule))
    return None


def resolve_versioned_call(row: Any, bars: pd.DataFrame) -> DeterministicOutcome | None:
    """Resolve one sealed quick-profit/swing call, or return None while still immature.

    Only candles strictly after the arming session are eligible. Stop is checked before
    target on every bar. Missing/invalid observed data fails closed; merely insufficient
    future history remains pending.
    """

    if row.contract_version not in CONTRACTS:
        return _invalid("unknown_contract")
    contract = CONTRACTS[row.contract_version]
    if contract.kind is ContractKind.LEGACY:
        raise ValueError("legacy rows must use the legacy resolver")
    if row.prediction_payload is None:
        return _invalid("unsealed_versioned_prediction")
    if not (row.stop < row.entry):
        return _invalid("invalid_geometry")
    target = row.entry + float(contract.target_r) * (row.entry - row.stop)
    if target <= row.entry:
        return _invalid("invalid_geometry")
    if bars.empty:
        return None
    if any(column not in bars.columns for column in _OHLC):
        return _invalid("missing_ohlc_columns")
    if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        return _invalid("unordered_or_duplicate_candles")

    dates = _dates(bars.index)
    armed = date.fromisoformat(row.armed_on)
    eligible = [i for i, day in enumerate(dates) if day > armed]
    entry_valid = int(contract.entry_valid_sessions or 0)
    if entry_valid <= 0:
        return _invalid("invalid_entry_window")
    entry_candidates = eligible[:entry_valid]
    if not entry_candidates:
        return None

    payload = row.prediction_payload
    source_snapshot = payload.get("source_snapshot") or {}
    atr = float((source_snapshot.get("source_feature_row") or {}).get("atr", 0.0) or 0.0)
    chased_atr = float((payload.get("levels") or {}).get("chased_atr_multiple", 1.0))
    slippage = float(
        (((payload.get("execution") or {}).get("assumptions") or {}).get("slippage_pct"))
        or 0.0
    )

    entry_index: int | None = None
    raw_entry: float | None = None
    for index in entry_candidates:
        bar = bars.iloc[index]
        if any(pd.isna(bar[column]) for column in _OHLC):
            return _invalid("missing_ohlc_value", on=dates[index])
        open_ = float(bar["open"])
        high = float(bar["high"])
        close = float(bar["close"])
        if open_ > row.entry + chased_atr * atr:
            return DeterministicOutcome(
                outcome="chased",
                outcome_state=OutcomeState.NEVER_TRIGGERED.value,
                label=None,
                exit_on=dates[index].isoformat(),
                time_to_entry_sessions=entry_candidates.index(index),
                time_to_resolution_sessions=entry_candidates.index(index),
                first_event="chased_open",
            )
        if open_ >= row.entry:
            entry_index, raw_entry = index, open_
            break
        if high >= row.entry:
            entry_index, raw_entry = index, row.entry
            break
        if close < row.stop:
            return DeterministicOutcome(
                outcome="invalidated",
                outcome_state=OutcomeState.NEVER_TRIGGERED.value,
                label=None,
                exit_on=dates[index].isoformat(),
                time_to_entry_sessions=entry_candidates.index(index),
                time_to_resolution_sessions=entry_candidates.index(index),
                first_event="invalidated_before_entry",
            )
    if entry_index is None or raw_entry is None:
        if len(entry_candidates) < entry_valid:
            return None
        return DeterministicOutcome(
            outcome="never_triggered",
            outcome_state=OutcomeState.NEVER_TRIGGERED.value,
            label=None,
            exit_on=dates[entry_candidates[-1]].isoformat(),
            time_to_entry_sessions=entry_valid,
            time_to_resolution_sessions=entry_valid,
            first_event="entry_expired",
        )

    entry_fill = raw_entry * (1.0 + slippage)
    risk = entry_fill - row.stop
    if risk <= 0:
        return _invalid("nonpositive_actual_risk", on=dates[entry_index])
    max_hold = int(contract.max_hold_sessions or 0)
    if max_hold <= 0:
        return _invalid("invalid_holding_window", on=dates[entry_index])
    holding = list(range(entry_index, min(len(bars), entry_index + max_hold)))
    raw_exit: float | None = None
    exit_index: int | None = None
    event: str | None = None
    highs: list[float] = []
    lows: list[float] = []
    for held, index in enumerate(holding):
        bar = bars.iloc[index]
        if any(pd.isna(bar[column]) for column in _OHLC):
            return _invalid("missing_ohlc_value", on=dates[index])
        open_, high, low = (float(bar[name]) for name in ("open", "high", "low"))
        highs.append(high)
        lows.append(low)
        if held > 0 and open_ <= row.stop:
            raw_exit, exit_index, event = open_, index, "gap_stop"
            break
        if low <= row.stop:
            raw_exit, exit_index, event = row.stop, index, "stop"
            break
        if high >= target:
            raw_exit, exit_index, event = target, index, "target"
            break
    if event is None:
        if len(holding) < max_hold:
            return None
        exit_index = holding[-1]
        raw_exit = float(bars.iloc[exit_index]["close"])
        event = "timeout"

    assert raw_exit is not None and exit_index is not None
    exit_fill = raw_exit * (1.0 - slippage)
    signal_risk = row.entry - row.stop
    gross_r = (raw_exit - row.entry) / signal_risk
    execution_r = (exit_fill - entry_fill) / risk
    quantity = float(((payload.get("execution") or {}).get("quantity")) or 0.0)
    fees = 0.0
    model = _cost_model(row)
    if model is not None and quantity > 0:
        cost = model.round_trip_cost(
            trade_type=TradeType.DELIVERY,
            qty=quantity,
            entry_price=price_decimal(entry_fill),
            exit_price=price_decimal(exit_fill),
        )
        fees = float(cost.total)
    net_r = execution_r - (fees / (risk * quantity) if quantity > 0 else 0.0)
    reporting_after_tax: float | None = None
    if row.market == "crypto" and model is not None:
        assumptions = ((payload.get("execution") or {}).get("assumptions") or {})
        schedule = assumptions.get("fee_tax_schedule") or {}
        share = tds_share_of_costs(
            maker_taker_pct=float(schedule.get("maker_taker_pct", 0.0)),
            tds_pct=float(schedule.get("tds_pct", 0.0)),
            gst_pct=float(schedule.get("gst_pct", 0.0)),
            slippage_pct=slippage,
        )
        reporting_after_tax = after_tax_r(
            SimpleNamespace(gross_r=gross_r, net_r=net_r), share
        )
    held_sessions = exit_index - entry_index + 1
    return DeterministicOutcome(
        outcome=event,
        outcome_state=classify_outcome(event).value,
        label=int(event == "target" and net_r > 0),
        entry_on=dates[entry_index].isoformat(),
        entry_price=entry_fill,
        exit_on=dates[exit_index].isoformat(),
        exit_price=exit_fill,
        gross_r=gross_r,
        execution_r=execution_r,
        net_r=net_r,
        after_tax_r=reporting_after_tax,
        fees=fees,
        holding_sessions=held_sessions,
        time_to_entry_sessions=entry_candidates.index(entry_index),
        time_to_resolution_sessions=entry_candidates.index(entry_index) + held_sessions,
        mfe_r=(max(highs) - entry_fill) / risk,
        mae_r=(min(lows) - entry_fill) / risk,
        first_event=event,
    )
