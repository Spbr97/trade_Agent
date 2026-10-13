# ruff: noqa: E501
"""Probation desk: capped sizing, evidence-only rejections, sticky suspensions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk.probation import (
    Policy,
    evaluate_suspensions,
    evidence_only_rejections,
    run_probation,
    size_call,
)

SETUP = "r_test_trail"
DAY = date(2026, 10, 14)
POLICY = Policy(
    enabled=True,
    started_on=date(2026, 10, 13),
    approvals=((SETUP, "bse"),),
    max_new_calls_per_day=2,
    worst_allowed_grade="B",
    checkpoint_calls=3,
)


class _Costs:
    """Flat Rs 10 round-trip cost so the arithmetic is checkable by hand."""

    def net_pnl(self, *, trade_type: Any, qty: float, entry_price: Decimal, exit_price: Decimal) -> Decimal:
        return (exit_price - entry_price) * Decimal(str(qty)) - Decimal("10")


def _row(i: int, **over: Any) -> dict[str, Any]:
    base = {
        "signal_id": f"{SETUP}:BSE_{i}:2026-10-14", "scrip_code": f"BSE_{i}", "symbol": f"S{i}",
        "setup": SETUP, "grade": "B", "armed_on": "2026-10-14", "entry": 100.0, "stop": 95.0,
        "t1": 110.0, "t2": 120.0, "rejected_for": [], "source": "live",
        "outcome": None, "exit_price": None,
    }  # fmt: skip
    base.update(over)
    return base


def _run(rows: list[dict[str, Any]], tmp: Path, policy: Policy = POLICY) -> dict[str, Any]:
    return run_probation(
        "bse", rows=rows, cost_model=_Costs(), capital=100000.0, risk_pct=0.0025,
        max_value_pct=0.25, latest_session=DAY, policy=policy, state_dir=tmp,
        now=datetime(2026, 10, 14, 18, 0, tzinfo=IST),
    )  # fmt: skip


def test_sizing_uses_the_per_trade_risk_and_the_position_cap() -> None:
    assert size_call(100.0, 95.0, 100000.0, 0.0025, 0.25) == 50  # Rs 250 risk / Rs 5 per share
    assert size_call(1000.0, 999.0, 100000.0, 0.0025, 0.25) == 25  # capped by Rs 25k position
    assert size_call(100.0, 100.0, 100000.0, 0.0025, 0.25) == 0


def test_only_evidence_rejections_are_waived() -> None:
    assert evidence_only_rejections(["only 0 resolved trades, need 500", "never measured against a random-timing baseline"])
    assert not evidence_only_rejections(["only 0 resolved trades, need 500", "regime risk_off: no new swing entries"])
    assert not evidence_only_rejections(["net R:R 1.7 < 2.0 to final target"])


def test_calls_are_capped_per_day_graded_and_filtered(tmp_path: Path) -> None:
    rows = [
        _row(1), _row(2), _row(3),
        _row(4, grade="C"),
        _row(5, rejected_for=["regime risk_off: no new swing entries"]),
        _row(6, setup="other_setup"),
        _row(7, source="backfill"),
    ]  # fmt: skip
    out = _run(rows, tmp_path)
    assert len(out["calls_today"]) == 2
    assert {c["qty"] for c in out["calls_today"]} == {50}
    again = _run(rows, tmp_path)  # idempotent: a call is recorded once
    assert len(again["calls_today"]) == 2


def test_resolved_calls_are_graded_after_costs_and_a_loss_suspends_the_desk(tmp_path: Path) -> None:
    rows = [_row(1), _row(2)]
    _run(rows, tmp_path)
    for r in rows:  # both stop out: (95-100)*50 - 10 = -260 rupees, -1.04R each
        r.update(outcome="stop", exit_price=95.0)
    out = _run(rows, tmp_path)
    assert out["ledger"]["resolved_calls"] == 2
    assert out["ledger"]["net_rupees"] == -520.0
    assert out["state"] == "active"  # -2.08R: above the floors, before the checkpoint
    third = _row(3, outcome="stop", exit_price=95.0)
    rows.append(third)
    out = _run(rows, tmp_path, Policy(**{**POLICY.__dict__, "max_new_calls_per_day": 3}))
    assert out["state"] == "suspended"
    assert "money lost is not below money made" in out["suspended"]["desk"]["reason"]


def test_a_suspension_never_lifts_itself_and_blocks_new_calls(tmp_path: Path) -> None:
    rows = [_row(i, outcome="stop", exit_price=95.0) for i in range(1, 6)]
    policy = Policy(**{**POLICY.__dict__, "max_new_calls_per_day": 9, "checkpoint_calls": 99})
    _run(rows, tmp_path, policy)
    out = _run(rows, tmp_path, policy)
    assert out["state"] == "suspended"
    fresh = _run(rows + [_row(50)], tmp_path, policy)
    assert all(c["signal_id"] != _row(50)["signal_id"] for c in fresh["calls_today"])
    winner = [_row(i, outcome="target", exit_price=120.0) for i in range(60, 70)]
    still = _run(rows + winner, tmp_path, policy)
    assert still["state"] == "suspended"


def test_floor_rules_in_isolation() -> None:
    losing = [{"setup": "a", "net_r": -1.0, "net_rupees": -250.0}] * 3
    per_setup, desk = evaluate_suspensions(losing, Policy(enabled=True, checkpoint_calls=99))
    assert "a" in per_setup and desk is None
    winning = [{"setup": "a", "net_r": 0.5, "net_rupees": 125.0}] * 20
    assert evaluate_suspensions(winning, Policy(enabled=True)) == ({}, None)
