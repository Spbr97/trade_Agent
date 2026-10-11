from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tradedesk.broker.indstocks.models import IST
from tradedesk.pre_takeoff_evidence import (
    evaluate_pre_takeoff_evidence,
    load_pre_takeoff_evidence,
    pre_takeoff_collector_health,
    record_pre_takeoff_evidence,
)


def _resolved_plans(
    *, market: str, count: int, sessions: int, successes: int
) -> dict[str, dict[str, object]]:
    start = datetime(2026, 1, 1, 10, 0, tzinfo=IST)
    outcomes: list[bool] = []
    remaining_wins = successes
    for index in range(count):
        remaining = count - index
        success = remaining_wins > 0 and (index % 5 != 0 or remaining_wins == remaining)
        outcomes.append(success)
        remaining_wins -= int(success)
    assert sum(outcomes) == successes
    plans: dict[str, dict[str, object]] = {}
    for index, success in enumerate(outcomes):
        decision = start + timedelta(days=index % sessions)
        resolved = start + timedelta(days=index, hours=1)
        plan_id = f"plan-{index:03d}"
        plans[plan_id] = {
            "plan_id": plan_id,
            "market": market,
            "state": "target" if success else "stop",
            "armed_at": decision.isoformat(),
            "decision_bar": decision.isoformat(),
            "resolved_at": resolved.isoformat(),
            "strict_success": success,
            "net_r": 0.75 if success else -1.0,
        }
    return plans


def test_equity_first_look_rejection_is_terminal_and_immutable(tmp_path) -> None:
    gate_path = tmp_path / "nse" / "qualification.jsonl"
    bad = _resolved_plans(market="nse", count=20, sessions=10, successes=8)
    first = record_pre_takeoff_evidence(
        "nse",
        bad,
        gate_path=gate_path,
        observed_at=datetime(2026, 2, 1, tzinfo=IST),
    )
    assert first["status"] == "rejected"
    assert first["terminal"] is True
    assert "strict_accuracy" in first["blockers"]

    excellent = _resolved_plans(market="nse", count=60, sessions=40, successes=54)
    repeated = record_pre_takeoff_evidence(
        "nse",
        excellent,
        gate_path=gate_path,
        observed_at=datetime(2026, 5, 1, tzinfo=IST),
    )
    assert repeated["status"] == "rejected"
    assert len(gate_path.read_text(encoding="utf-8").splitlines()) == 1


def test_equity_passes_early_then_reaches_qualification_review(tmp_path) -> None:
    gate_path = tmp_path / "bse" / "qualification.jsonl"
    early = _resolved_plans(market="bse", count=20, sessions=10, successes=12)
    early_report = record_pre_takeoff_evidence(
        "bse",
        early,
        gate_path=gate_path,
        observed_at=datetime(2026, 2, 1, tzinfo=IST),
    )
    assert early_report["status"] == "collecting_qualification"
    assert early_report["terminal"] is False

    mature = _resolved_plans(market="bse", count=60, sessions=40, successes=48)
    qualified = record_pre_takeoff_evidence(
        "bse",
        mature,
        gate_path=gate_path,
        observed_at=datetime(2026, 5, 1, tzinfo=IST),
    )
    assert qualified["status"] == "qualified_for_review"
    assert qualified["terminal"] is True
    assert qualified["eligible_for_live"] is False
    assert qualified["baseline_accuracy_improved"] is False
    assert qualified["metrics"]["wilson_95_lower"] > 0.55
    assert len(gate_path.read_text(encoding="utf-8").splitlines()) == 2


def test_crypto_core_pass_remains_blocked_without_frozen_controls(tmp_path) -> None:
    mature = _resolved_plans(market="crypto", count=60, sessions=40, successes=48)
    report = record_pre_takeoff_evidence(
        "crypto",
        mature,
        gate_path=tmp_path / "crypto" / "qualification.jsonl",
        observed_at=datetime(2026, 5, 1, tzinfo=IST),
    )
    assert report["status"] == "controls_pending"
    assert report["terminal"] is False
    assert report["eligible_for_live"] is False
    assert report["blockers"] == [
        "matched_random_accuracy_tail_probability",
        "matched_random_net_r_tail_probability",
    ]


def test_plan_and_gate_hash_chains_fail_closed(tmp_path) -> None:
    plan_path = tmp_path / "crypto" / "plans.jsonl"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "tampered",
                "previous_sha256": "GENESIS",
                "event_sha256": "not-a-real-hash",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="plan hash-chain failure"):
        load_pre_takeoff_evidence("crypto", output_root=tmp_path)


def test_collector_health_is_schedule_aware_and_crypto_is_continuous() -> None:
    sunday = datetime(2026, 10, 11, 16, 0, tzinfo=IST)
    equity = pre_takeoff_collector_health(
        "nse",
        last_observed_at=datetime(2026, 10, 9, 15, 16, tzinfo=IST).isoformat(),
        latest_completed_bar=None,
        observed_at=sunday,
    )
    assert equity["status"] == "waiting_for_market_session"
    assert equity["healthy"] is True
    assert equity["next_expected_at"].startswith("2026-10-12T09:31")

    fresh_crypto = pre_takeoff_collector_health(
        "crypto",
        last_observed_at=datetime(2026, 10, 11, 15, 40, tzinfo=IST).isoformat(),
        latest_completed_bar=None,
        observed_at=sunday,
    )
    assert fresh_crypto["status"] == "healthy"

    stale_crypto = pre_takeoff_collector_health(
        "crypto",
        last_observed_at=datetime(2026, 10, 11, 15, 0, tzinfo=IST).isoformat(),
        latest_completed_bar=None,
        observed_at=sunday,
    )
    assert stale_crypto["status"] == "stale"
    assert stale_crypto["healthy"] is False


def test_empty_evidence_is_collecting_not_a_pass() -> None:
    report = evaluate_pre_takeoff_evidence("crypto", {})
    assert report["status"] == "collecting_qualification"
    assert report["metrics"]["resolved_trades"] == 0
    assert report["metrics"]["wilson_95_lower"] is None
    assert report["eligible_for_live"] is False
