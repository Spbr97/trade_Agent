"""Cost-adjusted, short-horizon labels for AEM decisions."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from tradedesk.config.models import RiskConfig
from tradedesk.markets.costs import CostModel
from tradedesk.models import Side, TradeType, price_decimal
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT, AemContract
from tradedesk_lab.aem_detector import AemCandidate, AemDecision


def _unresolved(status: str) -> dict:
    return {"status": status, "strict_success": None, "label": None}


def _ist(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("invalid timestamp")
    return (
        stamp.tz_localize("Asia/Kolkata") if stamp.tz is None else stamp.tz_convert("Asia/Kolkata")
    )


def _valid_bar(row: pd.Series) -> bool:
    try:
        values = np.asarray([row.open, row.high, row.low, row.close], dtype=float)
    except (TypeError, ValueError):
        return False
    opening, high, low, close = values
    return bool(
        np.isfinite(values).all()
        and (values > 0).all()
        and low <= min(opening, close)
        and high >= max(opening, close)
    )


def label_trade(
    candidate: AemCandidate,
    decision: AemDecision,
    session: pd.DataFrame,
    risk: RiskConfig,
    costs: CostModel,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
) -> dict:
    """Resolve only observed execution paths, with conservative OHLC ordering.

    Candle timestamps are opens. Intrabar fills cannot be timed precisely:
    entry_at is the fill bar's lower bound, exit_at is the exit's observation
    time (bar close for an intrabar touch), and minutes_held is an upper bound.
    """
    if decision.decision != "TRADE" or decision.signal_price is None:
        return _unresolved("not_triggered")
    if candidate.contract_sha256 != contract.sha256 or decision.contract_sha256 != contract.sha256:
        raise ValueError("label and decision contracts differ")
    if decision.candidate_id != candidate.identifier:
        raise ValueError("label and decision candidates differ")
    try:
        available = _ist(decision.available_at)
        idx = pd.DatetimeIndex(session.index)
        idx = idx.tz_localize("Asia/Kolkata") if idx.tz is None else idx.tz_convert("Asia/Kolkata")
    except (TypeError, ValueError):
        return _unresolved("invalid_execution_timestamps")
    bar = pd.Timedelta(minutes=contract.execution_interval_minutes)
    if (
        idx.hasnans
        or idx.has_duplicates
        or not idx.is_monotonic_increasing
        or available != available.floor("min")
        or not (idx == idx.floor("min")).all()
    ):
        return _unresolved("invalid_execution_timestamps")
    session_open = available.normalize() + pd.Timedelta(hours=9, minutes=15)
    session_close = available.normalize() + pd.Timedelta(hours=15, minutes=30)
    if not session_open <= available < session_close:
        return _unresolved("invalid_execution_timestamps")
    if not {"open", "high", "low", "close"}.issubset(session):
        return _unresolved("invalid_execution_bar")
    executable = session.loc[(idx >= available) & (idx < session_close)].copy()
    executable.index = idx[(idx >= available) & (idx < session_close)]
    if executable.empty:
        return _unresolved("no_executable_bar")
    if executable.index[0] != available:
        return _unresolved("incomplete_execution_session")
    slip = float(costs.slippage_pct)
    if not np.isfinite(slip) or not 0 <= slip < 1:
        return _unresolved("invalid_slippage")
    if not np.isfinite(decision.signal_price) or decision.signal_price <= 0:
        return _unresolved("invalid_entry_geometry")
    entry_style = str(decision.features.get("entry_pattern", ""))
    vwap = float(decision.features.get("vwap", decision.signal_price))
    if not np.isfinite(vwap) or vwap <= 0:
        return _unresolved("invalid_entry_geometry")
    stretched_pullback = bool(
        entry_style == "impulse_pullback"
        and decision.signal_price / vwap - 1 > contract.pullback_market_distance_from_vwap
    )
    touched_limit = False
    if stretched_pullback:
        entry_style = "impulse_pullback_vwap_limit"
        order_deadline = available + pd.Timedelta(minutes=contract.pullback_limit_wait_minutes)
        order_window = executable.loc[pd.DatetimeIndex(executable.index) < order_deadline]
        fill = None
        expected = available
        for stamp, row in order_window.iterrows():
            if stamp != expected:
                return _unresolved("incomplete_execution_session")
            if not _valid_bar(row):
                return _unresolved("invalid_execution_bar")
            expected += bar
            if float(row.open) <= vwap:
                # Adverse price impact may consume gap improvement but can never
                # make a buy limit pay more than its submitted limit.
                fill = pd.Timestamp(stamp), min(float(row.open) * (1 + slip), vwap)
                break
            if float(row.low) <= vwap:
                fill = pd.Timestamp(stamp), vwap
                touched_limit = True
                break
        if fill is None:
            if expected < min(order_deadline, session_close):
                return _unresolved("insufficient_execution_horizon")
            return _unresolved("no_pullback_fill")
        entry_at, entry = fill
    else:
        if not _valid_bar(executable.iloc[0]):
            return _unresolved("invalid_execution_bar")
        entry_at = pd.Timestamp(executable.index[0])
        entry = float(executable.iloc[0].open) * (1 + slip)
        if entry > decision.signal_price * (1 + contract.max_chase_pct):
            return _unresolved("chased")
    deadline = min(entry_at + pd.Timedelta(minutes=contract.max_hold_minutes), session_close)
    after = executable.loc[(executable.index >= entry_at) & (executable.index < deadline)]
    stop = entry * (1 - contract.stop_pct)
    target = entry * (1 + contract.target_pct)
    size = position_size(
        SizeInputs(
            equity=float(risk.trading_capital),
            entry=entry,
            stop=stop,
            max_risk_pct=float(risk.max_risk_per_trade_pct),
            max_position_value_pct=float(risk.max_position_value_pct),
            gap_risk_cap_pct=float(risk.gap_risk_cap_pct),
            available_heat_pct=float(risk.max_portfolio_heat_pct),
        )
    )
    if not size.viable:
        return _unresolved("unsizeable")
    outcome, exit_price, exit_at, bars_held = "time_exit", None, None, 0
    exit_bar_open, exit_at_open = None, False
    expected = entry_at
    for number, (stamp, row) in enumerate(after.iterrows(), start=1):
        if stamp != expected:
            return _unresolved("incomplete_execution_session")
        if not _valid_bar(row):
            return _unresolved("invalid_execution_bar")
        expected += bar
        limit_fill_bar = touched_limit and number == 1
        if not limit_fill_bar and float(row.open) <= stop:
            outcome, exit_price = "gap_stop", float(row.open) * (1 - slip)
            exit_at_open = True
        elif not limit_fill_bar and float(row.open) >= target:
            # The opening print precedes this bar's later low: a target order
            # already filled at the open cannot subsequently be stopped out.
            outcome, exit_price = "target", float(row.open) * (1 - slip)
            exit_at_open = True
        elif float(row.low) <= stop:
            outcome, exit_price = "stop", stop * (1 - slip)
        elif (float(row.close) if limit_fill_bar else float(row.high)) >= target:
            # On a touched-limit fill the high may predate entry. Only a close
            # above target establishes that target was reached after the fill.
            outcome, exit_price = "target", target * (1 - slip)
        elif stamp + bar == deadline:
            outcome, exit_price = "time_exit", float(row.close) * (1 - slip)
        if exit_price is not None:
            exit_bar_open, bars_held = pd.Timestamp(stamp), number
            exit_at = exit_bar_open if exit_at_open else exit_bar_open + bar
            break
    if exit_price is None or exit_at is None:
        return _unresolved("insufficient_execution_horizon")
    buy = costs.leg_cost(
        side=Side.BUY,
        trade_type=TradeType.INTRADAY,
        qty=size.qty,
        price=price_decimal(entry),
    ).total
    sell = costs.leg_cost(
        side=Side.SELL,
        trade_type=TradeType.INTRADAY,
        qty=size.qty,
        price=price_decimal(exit_price),
        dp_applies=False,
    ).total
    charges = buy + sell
    gross = (price_decimal(exit_price) - price_decimal(entry)) * Decimal(str(size.qty))
    net = gross - charges
    initial_risk = (price_decimal(entry) - price_decimal(stop)) * Decimal(str(size.qty))
    target_hit = outcome == "target"
    strict = target_hit and net > 0
    return {
        "status": "resolved",
        "label": int(strict),
        "strict_success": strict,
        "target_hit": target_hit,
        "outcome": outcome,
        "entry_at": str(entry_at),
        "entry_time_precision": "within_bar" if touched_limit else "bar_open",
        "entry_observed_at": str(entry_at + bar if touched_limit else entry_at),
        "entry_style": entry_style,
        "exit_at": str(exit_at),
        "exit_bar_open": str(exit_bar_open),
        "exit_time_precision": "bar_open" if exit_at_open else "bar_close_observation",
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "qty": size.qty,
        "bars_held": bars_held,
        "minutes_held": float((exit_at - entry_at).total_seconds() / 60),
        "holding_time_precision": (
            "upper_bound"
            if touched_limit or not (exit_at_open or outcome == "time_exit")
            else "exact"
        ),
        "execution_interval_minutes": contract.execution_interval_minutes,
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "costs": float(charges),
        "gross_r": float(gross / initial_risk),
        "net_r": float(net / initial_risk),
    }
