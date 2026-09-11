"""Golden tests: the cost calculator must reproduce real INDmoney contract notes.

Notes live in contract_notes.yaml (numbers only). Per-line tolerance 0.01, total 0.05.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from tradedesk.config.models import ChargeSchedule
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import LegCost, leg_cost

NOTES_FILE = Path(__file__).with_name("contract_notes.yaml")
LINE_TOL = Decimal("0.01")
TOTAL_TOL = Decimal("0.05")
LINES = ("brokerage", "stt", "exchange_txn", "sebi_fee", "stamp_duty", "gst", "dp_charge")


def _load_notes() -> list[dict[str, Any]]:
    data = yaml.safe_load(NOTES_FILE.read_text(encoding="utf-8")) or {}
    notes = data.get("notes") or []
    return [n for n in notes if isinstance(n, dict)]


NOTES = _load_notes()


def _compare(label: str, expected: dict[str, Any], actual: dict[str, Decimal]) -> list[str]:
    problems = []
    for key, exp in expected.items():
        if key == "total":
            continue
        if key not in actual:
            problems.append(f"{label}: unknown line {key!r}")
            continue
        diff = abs(actual[key] - Decimal(str(exp)))
        if diff > LINE_TOL:
            problems.append(f"{label}.{key}: expected {exp}, computed {actual[key]}")
    if "total" in expected:
        # Compare only the lines the note lists, so an omitted dp_charge doesn't fail the total.
        listed = [k for k in expected if k != "total"]
        computed_total = sum((actual[k] for k in listed if k in actual), Decimal("0"))
        diff = abs(computed_total - Decimal(str(expected["total"])))
        if diff > TOTAL_TOL:
            problems.append(
                f"{label}.total: expected {expected['total']}, computed {computed_total}"
            )
    return problems


def _lines(leg: LegCost) -> dict[str, Decimal]:
    return {k: getattr(leg, k) for k in LINES}


@pytest.mark.golden
@pytest.mark.skipif(not NOTES, reason="tests/golden/contract_notes.yaml has no notes yet")
@pytest.mark.parametrize("note", NOTES, ids=[str(n.get("id", i)) for i, n in enumerate(NOTES)])
def test_contract_note(schedule: ChargeSchedule, note: dict[str, Any]) -> None:
    trade_type = TradeType(note["trade_type"])
    qty = int(note["qty"])
    buy = leg_cost(
        schedule,
        side=Side.BUY,
        trade_type=trade_type,
        qty=qty,
        price=Decimal(str(note["buy_price"])),
    )
    sell = leg_cost(
        schedule,
        side=Side.SELL,
        trade_type=trade_type,
        qty=qty,
        price=Decimal(str(note["sell_price"])),
    )

    problems: list[str] = []
    legs = note.get("legs") or {}
    if "buy" in legs:
        problems += _compare("buy", legs["buy"], _lines(buy))
    if "sell" in legs:
        problems += _compare("sell", legs["sell"], _lines(sell))
    if "combined" in note:
        combined = {k: _lines(buy)[k] + _lines(sell)[k] for k in LINES}
        problems += _compare("combined", note["combined"], combined)
    if not legs and "combined" not in note:
        problems.append("note has neither `legs` nor `combined`")

    assert not problems, "\n".join(problems)


def test_golden_file_has_ten_notes_before_m1_is_signed_off() -> None:
    """M1 done-when: 5 intraday + 5 delivery notes. Informational until filled in."""
    if not NOTES:
        pytest.skip("no notes yet — M1 sign-off pending")
    by_type = {
        t: sum(1 for n in NOTES if n.get("trade_type") == t) for t in ("intraday", "delivery")
    }
    assert by_type["intraday"] >= 5 and by_type["delivery"] >= 5, by_type
