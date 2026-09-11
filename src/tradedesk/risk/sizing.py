"""Position sizing (PLAN.md 1.2): risk per trade, position-value cap, regime multiplier,
overnight-gap check. Pure and deterministic; every cap is reported so the trade card can
say why the quantity is what it is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SizeInputs:
    equity: float
    entry: float
    stop: float
    max_risk_pct: float  # fraction, e.g. 0.0025
    max_position_value_pct: float  # fraction, e.g. 0.25
    size_multiplier: float = 1.0  # regime: 1.0 / 0.5 / 0.0
    gap_risk_cap_pct: float | None = None  # fraction of equity a bad gap may cost
    gap95_pct: float | None = None  # this stock's 95th-percentile overnight gap-down, fraction
    available_heat_pct: float | None = None  # portfolio heat left, fraction of equity


@dataclass
class SizeResult:
    qty: int
    risk_amount: float
    position_value: float
    risk_pct: float
    caps: list[str] = field(default_factory=list)

    @property
    def viable(self) -> bool:
        return self.qty > 0


def position_size(x: SizeInputs) -> SizeResult:
    risk_per_share = x.entry - x.stop
    if risk_per_share <= 0 or x.entry <= 0 or x.equity <= 0:
        return SizeResult(0, 0.0, 0.0, 0.0, ["invalid entry/stop"])
    caps: list[str] = []
    risk_budget = x.equity * x.max_risk_pct * x.size_multiplier
    if x.size_multiplier < 1.0:
        caps.append(f"regime x{x.size_multiplier:g}")
    if x.available_heat_pct is not None:
        heat_budget = x.equity * max(0.0, x.available_heat_pct)
        if heat_budget < risk_budget:
            risk_budget = heat_budget
            caps.append("portfolio heat")
    qty = math.floor(risk_budget / risk_per_share)

    max_value = x.equity * x.max_position_value_pct
    if qty * x.entry > max_value:
        qty = math.floor(max_value / x.entry)
        caps.append("max position value")

    if x.gap_risk_cap_pct is not None and x.gap95_pct is not None and x.gap95_pct > 0:
        # A 95th-percentile gap-down from entry must not cost more than the cap.
        gap_loss_per_share = x.entry * x.gap95_pct
        gap_qty = math.floor(x.equity * x.gap_risk_cap_pct / gap_loss_per_share)
        if gap_qty < qty:
            qty = gap_qty
            caps.append("gap check")

    qty = max(qty, 0)
    risk_amount = qty * risk_per_share
    return SizeResult(
        qty=qty,
        risk_amount=risk_amount,
        position_value=qty * x.entry,
        risk_pct=risk_amount / x.equity,
        caps=caps,
    )


def gap95_pct(df: pd.DataFrame, lookback_sessions: int = 500) -> float | None:
    """95th percentile of overnight gap-downs (open vs previous close) as a fraction."""
    if len(df) < 30:
        return None
    window = df.iloc[-lookback_sessions:]
    gaps = (window["open"] / window["close"].shift(1) - 1).dropna()
    downs = -gaps[gaps < 0]
    if downs.empty:
        return 0.0
    return float(np.percentile(downs.to_numpy(), 95))


def to_decimal(x: float) -> Decimal:
    return Decimal(str(round(x, 2)))
