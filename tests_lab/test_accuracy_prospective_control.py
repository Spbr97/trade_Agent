from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from tradedesk_lab.accuracy_prospective_control import (
    N_COHORTS,
    _new_session,
    _verify_selected_outcome,
    _verify_session,
    freeze_assignments,
    summarize_control,
)


def _m8_record(signal_id: str, day: str, *, selected: bool) -> dict:
    return {
        "signal_id": signal_id,
        "scrip_code": f"NSE_{signal_id}",
        "symbol": signal_id,
        "armed_on": day,
        "atr": 2.0,
        "prediction_sha256": f"hash-{signal_id}",
        "selected": selected,
        "prospective_eligible": True,
        "score_deadline": f"{day}T23:59:00+00:00",
        "status": "pending",
    }


def test_assignments_are_deterministic_matched_and_without_replacement() -> None:
    ids = [f"signal-{index}" for index in range(10)]
    first = freeze_assignments(ids, call_count=2, armed_on="2026-10-05")
    second = freeze_assignments(list(reversed(ids)), call_count=2, armed_on="2026-10-05")

    assert first == second
    assert len(first) == N_COHORTS
    assert all(len(cohort) == len(set(cohort)) == 2 for cohort in first)
    assert all(set(cohort) <= set(ids) for cohort in first)


def test_registration_hash_detects_assignment_and_population_changes() -> None:
    day = "2026-10-05"
    records = [
        _m8_record("a", day, selected=True),
        _m8_record("b", day, selected=False),
        _m8_record("c", day, selected=False),
    ]
    session = _new_session(records, datetime(2026, 10, 5, 12, tzinfo=UTC))
    current = {row["signal_id"]: row for row in records}
    _verify_session(session, current)

    session["cohort_assignments"][0] = ["a", "b"]
    with pytest.raises(ValueError, match="assignment hash changed"):
        _verify_session(session, current)

    session = _new_session(records, datetime(2026, 10, 5, 12, tzinfo=UTC))
    current["d"] = _m8_record("d", day, selected=False)
    with pytest.raises(ValueError, match="candidate population changed"):
        _verify_session(session, current)


def test_selected_outcome_must_exactly_match_m8() -> None:
    candidate = {
        "signal_id": "a",
        "model_selected": True,
        "status": "resolved",
        "entry_date": "2026-10-06",
        "exit_date": "2026-10-07",
        "label": 1,
        "outcome": "target",
        "qty": 10,
        "fill_price": 100.0,
        "stop": 98.0,
        "target": 101.0,
        "exit_price": 101.0,
        "net_r": 0.4,
    }
    actual = dict(candidate)
    actual.pop("model_selected")
    assert _verify_selected_outcome(candidate, actual)

    actual["net_r"] = 0.3
    with pytest.raises(ValueError, match="net_r differs"):
        _verify_selected_outcome(candidate, actual)


def test_summary_passes_only_with_paired_sample_and_random_advantage() -> None:
    sessions = []
    actual_records = []
    start = date(2026, 1, 1)
    for offset in range(50):
        armed_on = (start + timedelta(days=offset)).isoformat()
        winner_ids = [f"{armed_on}-w1", f"{armed_on}-w2"]
        loser_ids = [f"{armed_on}-l1", f"{armed_on}-l2"]
        candidates = [
            {
                "signal_id": signal_id,
                "status": "resolved",
                "label": int(signal_id in winner_ids),
                "net_r": 0.5 if signal_id in winner_ids else -1.0,
            }
            for signal_id in winner_ids + loser_ids
        ]
        sessions.append(
            {
                "prospective_eligible": True,
                "model_call_count": 2,
                "model_signal_ids": winner_ids,
                "cohort_assignments": [loser_ids[:] for _ in range(N_COHORTS)],
                "candidates": candidates,
            }
        )
        actual_records.extend(
            {
                "signal_id": signal_id,
                "status": "resolved",
                "label": 1,
                "net_r": 0.5,
            }
            for signal_id in winner_ids
        )

    summary = summarize_control(
        {"sessions": sessions}, {"records": actual_records}
    )

    assert summary["status"] == "selection_control_pass"
    assert summary["resolved_model_calls"] == 100
    assert summary["mature_sessions"] == 50
    assert summary["model_accuracy"] == 1.0
    assert summary["selection_advantage_r"] == 1.5
    assert summary["p_value"] == pytest.approx(1 / (N_COHORTS + 1))
    assert all(summary["gate_checks"].values())
    assert summary["satisfies_broader_random_timing_gate"] is False
    assert summary["eligible_for_live"] is False
