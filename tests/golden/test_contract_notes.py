"""Golden tests: the cost calculator must reproduce real INDmoney charges.

Entries live in contract_notes.yaml (numbers only). Per-line tolerance 0.01, total 0.05
unless the entry overrides `tolerance`.
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
LINES = ("brokerage", "stt", "exchange_txn", "ipft", "sebi_fee", "stamp_duty", "gst", "dp_charge")


def _load_notes() -> list[dict[str, Any]]:
    data = yaml.safe_load(NOTES_FILE.read_text(encoding="utf-8")) or {}
    notes = data.get("notes") or []
    return [n for n in notes if isinstance(n, dict)]


NOTES = _load_notes()


def _compare(
    label: str, expected: dict[str, Any], actual: dict[str, Decimal], total_tol: Decimal
) -> list[str]:
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
        listed = [k for k in expected if k != "total" and k in actual]
        # Only `total` given: compare the whole leg. Lines given: compare their sum, so an
        # omitted line (e.g. DP on the demat ledger) does not fail the total.
        computed_total = actual["total"] if not listed else sum(actual[k] for k in listed)
        diff = abs(computed_total - Decimal(str(expected["total"])))
        if diff > total_tol:
            problems.append(
                f"{label}.total: expected {expected['total']}, computed {computed_total}"
            )
    return problems


def _lines(leg: LegCost) -> dict[str, Decimal]:
    out = {k: getattr(leg, k) for k in LINES}
    out["total"] = leg.total
    return out


@pytest.mark.golden
@pytest.mark.skipif(not NOTES, reason="tests/golden/contract_notes.yaml has no notes yet")
@pytest.mark.parametrize("note", NOTES, ids=[str(n.get("id", i)) for i, n in enumerate(NOTES)])
def test_contract_note(schedule: ChargeSchedule, note: dict[str, Any]) -> None:
    trade_type = TradeType(note["trade_type"])
    qty = int(note["qty"])
    tol = Decimal(str(note.get("tolerance", TOTAL_TOL)))
    legs = note.get("legs") or {}
    problems: list[str] = []
    buy = sell = None
    if "buy" in legs or "combined" in note:
        buy = leg_cost(
            schedule,
            side=Side.BUY,
            trade_type=trade_type,
            qty=qty,
            price=Decimal(str(note["buy_price"])),
        )
        if "buy" in legs:
            problems += _compare("buy", legs["buy"], _lines(buy), tol)
    if "sell" in legs or "combined" in note:
        sell = leg_cost(
            schedule,
            side=Side.SELL,
            trade_type=trade_type,
            qty=qty,
            price=Decimal(str(note["sell_price"])),
        )
        if "sell" in legs:
            problems += _compare("sell", legs["sell"], _lines(sell), tol)
    if "combined" in note and buy is not None and sell is not None:
        combined = {k: _lines(buy)[k] + _lines(sell)[k] for k in (*LINES, "total")}
        problems += _compare("combined", note["combined"], combined, tol)
    if not legs and "combined" not in note:
        problems.append("note has neither `legs` nor `combined`")
    assert not problems, "\n".join(problems)


def test_golden_coverage_report() -> None:
    """M1 done-when asks for 5 intraday + 5 delivery notes; report what we have."""
    by_type = {
        t: sum(1 for n in NOTES if n.get("trade_type") == t) for t in ("intraday", "delivery")
    }
    if by_type["intraday"] < 5 or by_type["delivery"] < 5:
        pytest.skip(f"golden coverage {by_type}; M1 gate needs 5 + 5 current-plan notes")
