"""Position sizing (PLAN.md 1.2): risk per trade, position-value cap, regime multiplier,
overnight-gap check. Pure and deterministic; every cap is reported so the trade card can
say why the quantity is what it is.

`qty_step` (M13 follow-up) is what makes a market fractional. NSE trades whole shares, so
its step is 1.0 and every quantity here floors to an integer exactly as it always has.
Crypto's step is a fraction, which is the whole point: at BTC's ~Rs 77 lakh a Rs 250 risk
budget buys ~0.0032 BTC, and flooring that to a whole unit gave 0 - which is why every
BTC/ETH/BNB signal was silently skipped as "size 0" and those coins had never once been
backtested (found 2026-09-12 by checking which codes actually produced trades).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd


def quantize_qty(qty: float, step: float, *, rounding: str = ROUND_FLOOR) -> float:
    """Snap a quantity to a whole multiple of `step`, via Decimal so the result is a clean
    number rather than 0.0032200000000000003.

    ROUND_FLOOR (the default) never sizes above the budget that produced `qty`.
    ROUND_HALF_EVEN matches Python's built-in round(), which is what the partial-exit rule
    used before steps existed - with step 1.0 the two agree with the old code exactly.
    """
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    s = Decimal(str(step))
    n = (Decimal(str(qty)) / s).to_integral_value(rounding=rounding)
    return float(n * s)


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
    qty_step: float = 1.0  # smallest tradeable increment; 1.0 = whole units (NSE)
    min_notional: float = 0.0  # exchange minimum order value in INR (CoinDCX: 100)


@dataclass
class SizeResult:
    qty: float
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
    step = x.qty_step
    qty = quantize_qty(risk_budget / risk_per_share, step)

    max_value = x.equity * x.max_position_value_pct
    if qty * x.entry > max_value:
        qty = quantize_qty(max_value / x.entry, step)
        caps.append("max position value")

    if x.gap_risk_cap_pct is not None and x.gap95_pct is not None and x.gap95_pct > 0:
        # A 95th-percentile gap-down from entry must not cost more than the cap.
        gap_loss_per_share = x.entry * x.gap95_pct
        gap_qty = quantize_qty(x.equity * x.gap_risk_cap_pct / gap_loss_per_share, step)
        if gap_qty < qty:
            qty = gap_qty
            caps.append("gap check")

    qty = max(qty, 0.0)
    if qty > 0 and x.min_notional > 0 and qty * x.entry < x.min_notional:
        # Below the exchange's minimum order value: not sizeable at all, rather than
        # silently sizing up past the risk budget to reach it.
        qty = 0.0
        caps.append(f"below min notional {x.min_notional:g}")
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
