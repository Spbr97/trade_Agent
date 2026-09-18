"""Cost-adjusted, short-horizon labels for AEM decisions."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from tradedesk.config.models import RiskConfig
from tradedesk.markets.costs import CostModel
from tradedesk.models import Side, TradeType, price_decimal
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT, AemContract
from tradedesk_lab.aem_detector import AemCandidate, AemDecision


def label_trade(
    candidate: AemCandidate,
    decision: AemDecision,
    session: pd.DataFrame,
    risk: RiskConfig,
    costs: CostModel,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
) -> dict:
    if decision.decision != "TRADE" or decision.signal_price is None:
        return {"status": "not_triggered", "strict_success": None, "label": None}
    if candidate.contract_sha256 != contract.sha256 or decision.contract_sha256 != contract.sha256:
        raise ValueError("label and decision contracts differ")
    idx = pd.DatetimeIndex(session.index)
    available = pd.Timestamp(decision.available_at)
    executable = session.loc[idx >= available]
    if executable.empty:
        return {"status": "no_executable_bar", "strict_success": None, "label": None}
    slip = float(costs.slippage_pct)
    entry_style = str(decision.features["entry_pattern"])
    vwap = float(decision.features.get("vwap", decision.signal_price))
    stretched_pullback = bool(
        entry_style == "impulse_pullback"
        and decision.signal_price / vwap - 1 > contract.pullback_market_distance_from_vwap
    )
    if stretched_pullback:
        entry_style = "impulse_pullback_vwap_limit"
        order_deadline = available + pd.Timedelta(minutes=contract.pullback_limit_wait_minutes)
        order_window = executable.loc[pd.DatetimeIndex(executable.index) < order_deadline]
        fill = None
        for stamp, row in order_window.iterrows():
            if float(row.open) <= vwap:
                fill = pd.Timestamp(stamp), float(row.open) * (1 + slip)
                break
            if float(row.low) <= vwap:
                fill = pd.Timestamp(stamp), vwap * (1 + slip)
                break
        if fill is None:
            return {"status": "no_pullback_fill", "strict_success": None, "label": None}
        entry_at, entry = fill
    else:
        entry_at = pd.Timestamp(executable.index[0])
        entry = float(executable.iloc[0].open) * (1 + slip)
        if entry > decision.signal_price * (1 + contract.max_chase_pct):
            return {"status": "chased", "strict_success": None, "label": None}
    deadline = entry_at + pd.Timedelta(minutes=contract.max_hold_minutes)
    after = session.loc[(idx >= entry_at) & (idx < deadline)]
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
        return {"status": "unsizeable", "strict_success": None, "label": None}
    outcome, exit_price, exit_at, bars_held = "time_exit", None, None, 0
    for number, (stamp, row) in enumerate(after.iterrows(), start=1):
        if float(row.open) <= stop:
            outcome, exit_price = "gap_stop", float(row.open) * (1 - slip)
        elif float(row.low) <= stop:
            outcome, exit_price = "stop", stop * (1 - slip)
        elif float(row.high) >= target:
            outcome, exit_price = "target", target * (1 - slip)
        elif number == len(after):
            outcome, exit_price = "time_exit", float(row.close) * (1 - slip)
        if exit_price is not None:
            exit_at, bars_held = pd.Timestamp(stamp), number
            break
    assert exit_price is not None and exit_at is not None
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
        "entry_at": str(pd.Timestamp(after.index[0])),
        "entry_style": entry_style,
        "exit_at": str(exit_at),
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "qty": size.qty,
        "bars_held": bars_held,
        "minutes_held": float((exit_at - pd.Timestamp(after.index[0])).total_seconds() / 60),
        "execution_interval_minutes": int(
            (pd.Timestamp(after.index[1]) - pd.Timestamp(after.index[0])).total_seconds() / 60
        )
        if len(after) > 1
        else None,
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "costs": float(charges),
        "gross_r": float(gross / initial_risk),
        "net_r": float(net / initial_risk),
    }
