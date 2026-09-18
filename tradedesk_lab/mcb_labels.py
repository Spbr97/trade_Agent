"""Same-session, cost-adjusted labels for frozen MCB decisions."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from tradedesk.config.models import RiskConfig
from tradedesk.markets.costs import CostModel
from tradedesk.models import Side, TradeType, price_decimal
from tradedesk.risk.sizing import SizeInputs, position_size
from tradedesk_lab.mcb_contract import DEFAULT_MCB_CONTRACT, McbContract
from tradedesk_lab.mcb_detector import McbCandidate, McbDecision


def _excursions(bars: pd.DataFrame, entry: float, limit: int | None) -> tuple[float, float]:
    sample = bars if limit is None else bars.iloc[:limit]
    if sample.empty:
        return np.nan, np.nan
    return float(sample.high.max() / entry - 1), float(sample.low.min() / entry - 1)


def label_trade(
    candidate: McbCandidate,
    decision: McbDecision,
    session: pd.DataFrame,
    risk: RiskConfig,
    costs: CostModel,
    contract: McbContract = DEFAULT_MCB_CONTRACT,
) -> dict:
    if decision.decision != "TRADE" or decision.entry_trigger is None or decision.stop is None:
        return {"status": "not_triggered", "strict_success": None, "label": None}
    if decision.contract_sha256 != contract.sha256 or candidate.contract_sha256 != contract.sha256:
        raise ValueError("label and decision contracts differ")
    idx = pd.DatetimeIndex(session.index)
    available = pd.Timestamp(decision.available_at)
    if available.tz is None and idx.tz is not None:
        available = available.tz_localize(idx.tz)
    after = session.loc[idx >= available]
    if after.empty:
        return {"status": "no_executable_bar", "strict_success": None, "label": None}
    slip = float(costs.slippage_pct)
    entry = float(after.iloc[0].open) * (1 + slip)
    if entry > decision.entry_trigger + contract.chased_atr * candidate.daily_atr:
        return {"status": "chased", "strict_success": None, "label": None}
    stop = float(decision.stop)
    if stop >= entry:
        return {"status": "invalid_geometry", "strict_success": None, "label": None}
    target = entry + contract.target_r * (entry - stop)
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
        elif stamp == after.index[-1]:
            outcome, exit_price = "time_exit", float(row.close) * (1 - slip)
        if exit_price is not None:
            bars_held = number
            exit_at = pd.Timestamp(stamp)
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
    excursions = {}
    for name, limit in contract.horizons_bars:
        mfe, mae = _excursions(after, entry, limit)
        excursions[f"mfe_{name}"] = mfe
        excursions[f"mae_{name}"] = mae
    return {
        "status": "resolved",
        "label": int(strict),
        "strict_success": strict,
        "target_hit": target_hit,
        "outcome": outcome,
        "entry_at": str(pd.Timestamp(after.index[0])),
        "exit_at": str(exit_at),
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "qty": size.qty,
        "bars_held": bars_held,
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "costs": float(charges),
        "gross_r": float(gross / initial_risk),
        "net_r": float(net / initial_risk),
        **excursions,
    }
