from decimal import Decimal

import pandas as pd
import pytest
from tradedesk_lab.aem_staged_validation import _CostStress, _portfolio_replay, _stress_stats

from tradedesk.config.models import ChargeSchedule, RiskConfig
from tradedesk.markets.costs import EquityCostModel
from tradedesk.models import Side, TradeType


def test_cost_stress_scales_frozen_pydantic_leg_without_mutating_base():
    base = EquityCostModel(ChargeSchedule())
    normal = base.leg_cost(
        side=Side.BUY, trade_type=TradeType.INTRADAY, qty=10, price=Decimal("100")
    )
    stressed = _CostStress(base, Decimal("1.5")).leg_cost(
        side=Side.BUY, trade_type=TradeType.INTRADAY, qty=10, price=Decimal("100")
    )
    assert stressed.total == normal.total * Decimal("1.5")
    assert (
        base.leg_cost(
            side=Side.BUY, trade_type=TradeType.INTRADAY, qty=10, price=Decimal("100")
        ).total
        == normal.total
    )


def test_stress_stats_keeps_no_fills_in_attempt_denominator():
    result = _stress_stats(
        [
            {"status": "resolved", "strict_success": True, "net_r": 0.8},
            {"status": "resolved", "strict_success": False, "net_r": -1.1},
            {"status": "missed_fill", "strict_success": None, "net_r": None},
        ]
    )
    assert result["attempts"] == 3
    assert result["resolved_fills"] == 2
    assert result["strict_success_rate"] == 0.5
    assert result["mean_net_r"] == pytest.approx(-0.15)
    assert result["mean_net_r_per_attempt"] == pytest.approx(-0.1)


def _event(identifier: str, minute: int, outcome: str = "target") -> dict:
    entry = 100.0
    stop = 99.4
    target = 100.8
    exit_price = target if outcome == "target" else stop
    return {
        "event_id": identifier,
        "scrip_code": f"NSE_{identifier}",
        "symbol": identifier,
        "session_date": "2026-09-18",
        "decision": "TRADE",
        "status": "resolved",
        "entry_at": f"2026-09-18 09:{minute:02d}:00+05:30",
        "exit_at": f"2026-09-18 09:{minute + 2:02d}:00+05:30",
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "target_hit": outcome == "target",
        "outcome": outcome,
    }


def test_portfolio_replay_enforces_no_entry_window_and_daily_cap():
    events = pd.DataFrame(
        [_event("early", 20), *[_event(f"s{index}", 30 + index * 3) for index in range(4)]]
    )
    risk = RiskConfig(trading_capital=Decimal("100000"))
    result = _portfolio_replay(
        events,
        risk=risk,
        costs=EquityCostModel(ChargeSchedule()),
        sectors={code: code for code in events.scrip_code},
        calendar=["2026-09-18"],
    )
    assert result["selected_fills"] == 3
    assert result["rejections"] == {"max_new_entries_per_day": 1, "no_entry_window": 1}
    assert result["mean_net_r_after_constraints"] is not None
    assert result["ending_capital"] > result["initial_capital"]
    assert "min_net_rr=2.0" in result["contract_exception"]
