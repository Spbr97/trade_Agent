"""Phase 2's break-even gate: "compare required_move against the strategy's average target.
If the target is not comfortably larger, stop and change the rule set." A thin wrapper over
`markets.costs.CostModel` (fees/GST/TDS/spread already computed correctly per market there) -
no new cost math, just the ratio the template asks for, so this eliminates a bad rule set
before any backtest code runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradedesk.markets.costs import CostModel
from tradedesk.models import TradeType, price_decimal


@dataclass(frozen=True)
class BreakEvenResult:
    required_move_pct: float  # round-trip cost as a fraction of entry value
    strategy_avg_target_pct: float
    margin: float
    comfortably_clears: bool


def required_move_pct(
    costs: CostModel, *, trade_type: TradeType, qty: float, entry: float
) -> float:
    """The price move needed just to cover round-trip costs, isolated from any P&L by
    computing the cost of a round trip AT THE SAME PRICE (entry == exit)."""
    if qty <= 0 or entry <= 0:
        raise ValueError("qty and entry must be positive")
    rt = costs.round_trip_cost(
        trade_type=trade_type,
        qty=qty,
        entry_price=price_decimal(entry),
        exit_price=price_decimal(entry),
    )
    return float(rt.pct_of_entry_value)


def evaluate_break_even(
    required_move: float, strategy_avg_target_pct: float, *, margin: float = 1.5
) -> BreakEvenResult:
    """`margin` is how much bigger than the break-even move the average target must be to
    count as "comfortably" clearing it, not just barely - 1.5x by default, matching the
    template's own language ("not comfortably larger... stop and change the rule set")."""
    return BreakEvenResult(
        required_move_pct=required_move,
        strategy_avg_target_pct=strategy_avg_target_pct,
        margin=margin,
        comfortably_clears=strategy_avg_target_pct >= required_move * margin,
    )
