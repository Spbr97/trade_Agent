"""Phase 1 conformance: EquityCostModel must be byte-identical to risk/costs.py's free
functions for every input, not just typical ones. Property-based (same strategies as
tests/unit/test_costs_properties.py) plus the 5 ledger-verified golden notes routed
through the wrapper - if a real contract note passes through risk/costs.py directly but
fails through EquityCostModel (or vice versa), Phase 1 has already broken something.
"""

from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import CostModel, EquityCostModel
from tradedesk.models import Side, TradeType
from tradedesk.risk import costs as free

qtys = st.integers(min_value=1, max_value=5000)
prices = st.decimals(min_value=D("1"), max_value=D("50000"), places=2)
trade_types = st.sampled_from(list(TradeType))
sides = st.sampled_from(list(Side))


def test_equity_cost_model_satisfies_the_protocol(default_schedule: ChargeSchedule) -> None:
    model: CostModel = EquityCostModel(default_schedule)
    assert isinstance(model, CostModel)


@given(qty=qtys, price=prices, trade_type=trade_types, side=sides)
@hyp_settings(max_examples=300)
def test_leg_cost_matches_the_free_function_exactly(
    default_schedule: ChargeSchedule, qty: int, price: D, trade_type: TradeType, side: Side
) -> None:
    model = EquityCostModel(default_schedule)
    wrapped = model.leg_cost(side=side, trade_type=trade_type, qty=qty, price=price)
    direct = free.leg_cost(default_schedule, side=side, trade_type=trade_type, qty=qty, price=price)
    assert wrapped == direct


@given(qty=qtys, price=prices, trade_type=trade_types)
@hyp_settings(max_examples=150)
def test_round_trip_and_net_pnl_match_the_free_functions(
    default_schedule: ChargeSchedule, qty: int, price: D, trade_type: TradeType
) -> None:
    exit_price = price * D("1.05")
    model = EquityCostModel(default_schedule)
    wrapped_rt = model.round_trip_cost(
        trade_type=trade_type, qty=qty, entry_price=price, exit_price=exit_price
    )
    direct_rt = free.round_trip_cost(
        default_schedule, trade_type=trade_type, qty=qty, entry_price=price, exit_price=exit_price
    )
    assert wrapped_rt == direct_rt

    wrapped_pnl = model.net_pnl(
        trade_type=trade_type, qty=qty, entry_price=price, exit_price=exit_price
    )
    direct_pnl = free.net_pnl(
        default_schedule, trade_type=trade_type, qty=qty, entry_price=price, exit_price=exit_price
    )
    assert wrapped_pnl == direct_pnl


@given(qty=qtys, price=prices, trade_type=trade_types)
@hyp_settings(max_examples=150)
def test_net_r_multiple_and_net_reward_risk_match_the_free_functions(
    default_schedule: ChargeSchedule, qty: int, price: D, trade_type: TradeType
) -> None:
    stop = price * D("0.95")
    target = price * D("1.10")
    model = EquityCostModel(default_schedule)

    wrapped_r = model.net_r_multiple(
        trade_type=trade_type, qty=qty, entry=price, stop=stop, exit_price=target
    )
    direct_r = free.net_r_multiple(
        default_schedule, trade_type=trade_type, qty=qty, entry=price, stop=stop, exit_price=target
    )
    assert wrapped_r == direct_r

    wrapped_rr = model.net_reward_risk(
        trade_type=trade_type, qty=qty, entry=price, stop=stop, target=target
    )
    direct_rr = free.net_reward_risk(
        default_schedule, trade_type=trade_type, qty=qty, entry=price, stop=stop, target=target
    )
    assert wrapped_rr == direct_rr


# ------------------------------------------------------- golden notes, via the wrapper

NOTES_FILE = Path(__file__).resolve().parents[1] / "golden" / "contract_notes.yaml"
LINE_TOL = D("0.01")
TOTAL_TOL = D("0.05")


def _load_notes() -> list[dict[str, Any]]:
    data = yaml.safe_load(NOTES_FILE.read_text(encoding="utf-8")) or {}
    return [n for n in (data.get("notes") or []) if isinstance(n, dict)]


NOTES = _load_notes()


@pytest.mark.golden
@pytest.mark.skipif(not NOTES, reason="tests/golden/contract_notes.yaml has no notes yet")
@pytest.mark.parametrize("note", NOTES, ids=[str(n.get("id", i)) for i, n in enumerate(NOTES)])
def test_golden_notes_pass_through_the_cost_model_too(
    default_schedule: ChargeSchedule, note: dict[str, Any]
) -> None:
    """The same 5 real, ledger-verified contract notes that pin risk/costs.py, routed
    through EquityCostModel instead. If these ever diverge from
    tests/golden/test_contract_notes.py, the wrapper has drifted from the free functions."""
    model = EquityCostModel(default_schedule)
    trade_type = TradeType(note["trade_type"])
    qty = int(note["qty"])
    tol = D(str(note.get("tolerance", TOTAL_TOL)))
    legs = note.get("legs") or {}
    buy = sell = None
    if "buy" in legs or "combined" in note:
        buy = model.leg_cost(
            side=Side.BUY, trade_type=trade_type, qty=qty, price=D(str(note["buy_price"]))
        )
        if "buy" in legs:
            _assert_leg("buy", legs["buy"], buy, tol)
    if "sell" in legs or "combined" in note:
        sell = model.leg_cost(
            side=Side.SELL, trade_type=trade_type, qty=qty, price=D(str(note["sell_price"]))
        )
        if "sell" in legs:
            _assert_leg("sell", legs["sell"], sell, tol)
    if "combined" in note and buy is not None and sell is not None:
        expected = note["combined"]
        if "total" in expected:
            diff = abs((buy.total + sell.total) - D(str(expected["total"])))
            assert diff <= tol, f"combined total off by {diff}"


def _assert_leg(label: str, expected: dict[str, Any], leg: Any, tol: D) -> None:
    for key, exp in expected.items():
        if key == "total":
            continue
        diff = abs(getattr(leg, key) - D(str(exp)))
        assert diff <= LINE_TOL, f"{label}.{key}: expected {exp}, got {getattr(leg, key)}"
    if "total" in expected:
        diff = abs(leg.total - D(str(expected["total"])))
        assert diff <= tol, f"{label}.total: expected {expected['total']}, got {leg.total}"
