"""Versioned research outcomes: valid fills, production exits, and after-cost success."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from tradedesk.backtest.fills import Bar, Fill, FillReason, Position, evaluate_exit
from tradedesk.engine.indicators import atr, ema
from tradedesk.engine.signals import Signal
from tradedesk.markets.costs import CostModel
from tradedesk.models import Side, TradeType, price_decimal, qty_decimal

CONTRACT_VERSION = "net-target-v2"


def geometry_error(entry: float, stop: float, target: float) -> str | None:
    if not all(math.isfinite(x) and x > 0 for x in (entry, stop, target)):
        return "nonfinite_or_nonpositive_price"
    if stop >= entry:
        return "stop_at_or_above_fill"
    if target <= entry:
        return "target_at_or_below_fill"
    return None


def simulate_outcome(
    signal: Signal,
    bars: pd.DataFrame,
    entry_date: date | str | pd.Timestamp,
    fill_price: float,
    qty: float,
    costs: CostModel,
    *,
    entry_at_open: bool = False,
) -> dict[str, Any]:
    """Use the existing exit engine, including time stops and gap/stop tie priority.

    Daily OHLC cannot establish whether the entry-day high occurred after an intraday
    fill. Unless entry-at-open is known, credit that day's target only when the CLOSE
    also reaches it. Entry-day stop ambiguity remains conservative. Entry slippage is
    already included in fill_price; exit slippage is applied exactly once by the engine.
    This is a candidate simulation, not a portfolio or evidence of real executions.
    """
    error = geometry_error(fill_price, signal.stop, signal.t1)
    if error or not math.isfinite(qty) or qty <= 0:
        return {
            "status": "rejected_geometry" if error else "untradeable",
            "exclusion_reason": error or "nonpositive_quantity",
            "label": None,
            "strict_success": None,
            "target_hit": None,
            "net_r": None,
            "gross_r": None,
            "net_pnl": None,
            "costs": None,
            "outcome": "excluded",
            "exit_price": None,
            "label_end_date": None,
            "contract_version": CONTRACT_VERSION,
        }
    data = bars.copy()
    if "ema10" not in data:
        data["ema10"] = ema(data.close, 10)
    if "atr14" not in data:
        data["atr14"] = atr(data, 14)
    start = pd.Timestamp(entry_date).date()
    future = data.loc[pd.DatetimeIndex(data.index).date >= start]
    pending = {
        "status": "triggered_pending",
        "entry_date": str(start),
        "fill_price": fill_price,
        "label": None,
        "strict_success": None,
        "target_hit": None,
        "gross_r": None,
        "net_r": None,
        "net_pnl": None,
        "costs": None,
        "exit_price": None,
        "outcome": "insufficient",
        "sessions_to_outcome": None,
        "label_end_date": None,
        "contract_version": CONTRACT_VERSION,
        "entry_day_ordering": "known_open" if entry_at_open else "conservative_close_confirmation",
    }
    if future.empty or pd.Timestamp(future.index[0]).date() != start:
        return pending
    position = Position(
        signal=signal,
        entry_date=start,
        entry_price=fill_price,
        qty_initial=qty,
        qty_open=qty,
        stop=signal.stop,
        fills=[Fill(start, fill_price, qty, FillReason.ENTRY)],
    )
    for session, (stamp, row) in enumerate(future.iterrows()):
        high = float(row.high)
        if session == 0 and not entry_at_open and float(row.close) < signal.t1:
            high = min(high, math.nextafter(signal.t1, -math.inf))
        bar = Bar(
            on=pd.Timestamp(stamp).date(),
            open=float(row.open),
            high=high,
            low=float(row.low),
            close=float(row.close),
            volume=int(row.volume),
            ema10=float(row.ema10),
            atr=float(row.atr14),
        )
        evaluate_exit(position, bar, float(costs.slippage_pct), entry_day=session == 0)
        if not position.closed:
            continue
        sells = [f for f in position.fills if f.is_sell]
        charges = Decimal("0")
        # Allocate buys to the same-day/delivery quantities rather than retrospectively
        # classifying all legs from the final exit date. One DP charge per sell date.
        for trade_type in (TradeType.INTRADAY, TradeType.DELIVERY):
            quantity = sum(
                f.qty
                for f in sells
                if (TradeType.INTRADAY if f.on == start else TradeType.DELIVERY) == trade_type
            )
            if quantity:
                charges += costs.leg_cost(
                    side=Side.BUY,
                    trade_type=trade_type,
                    qty=quantity,
                    price=price_decimal(fill_price),
                ).total
        dp_dates: set[date] = set()
        for fill in sells:
            trade_type = TradeType.INTRADAY if fill.on == start else TradeType.DELIVERY
            charges += costs.leg_cost(
                side=Side.SELL,
                trade_type=trade_type,
                qty=fill.qty,
                price=price_decimal(fill.price),
                dp_applies=fill.on not in dp_dates,
            ).total
            dp_dates.add(fill.on)
        proceeds = sum((price_decimal(f.price) * qty_decimal(f.qty) for f in sells), Decimal("0"))
        gross = proceeds - price_decimal(fill_price) * qty_decimal(qty)
        net = gross - charges
        initial_risk = (price_decimal(fill_price) - price_decimal(signal.stop)) * qty_decimal(qty)
        target_hit = any(f.reason == FillReason.PARTIAL for f in sells)
        success = target_hit and net > 0
        final_reason = sells[-1].reason
        return {
            **pending,
            "status": "resolved",
            "target_hit": target_hit,
            "strict_success": success,
            "label": int(success),
            "outcome": "target" if final_reason == FillReason.PARTIAL else final_reason.value,
            "net_profitable": net > 0,
            "gross_r": float(gross / initial_risk),
            "net_r": float(net / initial_risk),
            "net_pnl": float(net),
            "costs": float(charges),
            "exit_price": float(proceeds / qty_decimal(qty)),
            "sessions_to_outcome": session,
            "label_end_date": str(bar.on),
            "fills": [
                {"on": str(f.on), "price": f.price, "qty": f.qty, "reason": f.reason.value}
                for f in position.fills
            ],
        }
    return pending
