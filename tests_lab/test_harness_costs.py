from decimal import Decimal

from tradedesk_lab.harness.costs import evaluate_break_even, required_move_pct

from tradedesk.config.models import RiskConfig
from tradedesk.markets.costs import EquityCostModel
from tradedesk.models import TradeType


def test_required_move_pct_is_the_round_trip_cost_at_the_same_price():
    costs = EquityCostModel(RiskConfig(trading_capital=Decimal("100000")).costs)
    pct = required_move_pct(costs, trade_type=TradeType.DELIVERY, qty=10, entry=100.0)
    # A same-price round trip has zero P&L, so the whole cost IS the required move.
    rt = costs.round_trip_cost(
        trade_type=TradeType.DELIVERY, qty=10, entry_price=Decimal("100"), exit_price=Decimal("100")
    )
    assert pct == float(rt.pct_of_entry_value)
    assert pct > 0


def test_evaluate_break_even_requires_the_target_to_comfortably_clear_the_gate():
    barely = evaluate_break_even(0.01, 0.012, margin=1.5)  # 1.2x, not 1.5x
    comfortable = evaluate_break_even(0.01, 0.02, margin=1.5)  # 2x
    assert not barely.comfortably_clears
    assert comfortable.comfortably_clears
