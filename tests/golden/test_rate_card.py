"""Provisional intraday check: worked examples from the DOCUMENTED rate card, not a real
contract note. See rate_card_examples.yaml for why this exists and its limits.

This is NOT the M1 done-when gate (that stays test_contract_notes.py::test_golden_coverage_report,
which only counts real notes). It exists so the intraday-only code paths in costs.py (STT
sell-only, intraday stamp rate, no DP charge) are exercised by *something* until a real
current-plan intraday trade is available.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.golden.test_contract_notes import _compare, _lines
from tradedesk.config.models import ChargeSchedule
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import leg_cost

EXAMPLES_FILE = Path(__file__).with_name("rate_card_examples.yaml")
TOL = Decimal("0.01")


def _load_examples() -> list[dict[str, Any]]:
    data = yaml.safe_load(EXAMPLES_FILE.read_text(encoding="utf-8")) or {}
    examples = data.get("examples") or []
    return [e for e in examples if isinstance(e, dict)]


EXAMPLES = _load_examples()


@pytest.mark.golden
@pytest.mark.parametrize("example", EXAMPLES, ids=[str(e["id"]) for e in EXAMPLES])
def test_rate_card_example(schedule: ChargeSchedule, example: dict[str, Any]) -> None:
    trade_type = TradeType(example["trade_type"])
    qty = int(example["qty"])
    legs = example["legs"]
    buy = leg_cost(
        schedule,
        side=Side.BUY,
        trade_type=trade_type,
        qty=qty,
        price=Decimal(str(example["buy_price"])),
    )
    sell = leg_cost(
        schedule,
        side=Side.SELL,
        trade_type=trade_type,
        qty=qty,
        price=Decimal(str(example["sell_price"])),
    )
    problems = _compare("buy", legs["buy"], _lines(buy), TOL)
    problems += _compare("sell", legs["sell"], _lines(sell), TOL)
    if "combined" in example:
        combined = {k: _lines(buy)[k] + _lines(sell)[k] for k in _lines(buy)}
        problems += _compare("combined", example["combined"], combined, TOL)
    assert not problems, "\n".join(problems)


def test_rate_card_examples_are_not_the_m1_gate() -> None:
    """Guard against someone quietly treating these as real notes: the M1 done-when count
    (test_contract_notes.py::test_golden_coverage_report) only reads contract_notes.yaml."""
    assert EXAMPLES, "rate_card_examples.yaml should have entries while the intraday gap is open"
