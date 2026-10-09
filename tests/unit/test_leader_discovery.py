from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.leader_discovery import (
    FEATURE_COLUMNS,
    OUTCOME_COLUMNS,
    build_audit_rows,
    load_latest_audit,
    run_missed_leader_audit,
    validate_audit_report,
)


def _frame(session: date = date(2026, 10, 5)) -> pd.DataFrame:
    rows = []
    for index, (code, symbol, leader, move) in enumerate(
        [
            ("NSE_1", "ONE", True, 0.12),
            ("NSE_2", "TWO", True, 0.09),
            ("NSE_3", "THREE", False, 0.01),
        ]
    ):
        row: dict[str, object] = {
            "session_date": session,
            "scrip_code": code,
            "symbol": symbol,
        }
        row.update({name: float(index + 1) / 100 for name in FEATURE_COLUMNS})
        row.update(
            {
                "forward_close_return_1": move / 3,
                "forward_close_return_3": move * 0.8,
                "forward_close_return_5": move,
                "forward_max_high_return_3": move,
                "forward_min_low_return_3": -0.01,
                "opportunity_percentile": 0.99 if leader else 0.30,
                "opportunity_label": leader,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _tracker() -> list[dict[str, object]]:
    return [
        {
            "market": "nse",
            "source": "live",
            "armed_on": "2026-10-05",
            "scrip_code": "NSE_1",
            "setup": "base_breakout",
            "evidence_class": "rejected_call",
            "shadow": False,
            "outcome_state": "never_triggered",
            "rejected_for": [
                "win rate 0.0% < required 80.0%",
                "regime risk_off: no new swing entries",
            ],
        }
    ]


def test_opportunity_labels_stay_separate_from_causal_features_and_performance() -> None:
    first = build_audit_rows(_frame(), market="nse", tracker_records=_tracker())
    changed = _frame()
    changed.loc[0, list(OUTCOME_COLUMNS)] = [0.5, 0.6, 0.7, 0.8, -0.2, 1.0, True]
    second = build_audit_rows(changed, market="nse", tracker_records=_tracker())

    assert first[0]["features"] == second[0]["features"]
    assert first[0]["opportunity"] != second[0]["opportunity"]
    assert first[0]["tracking"]["execution_state"] == "never_triggered"
    assert "label" not in first[0]["tracking"]


def test_audit_attributes_identified_and_no_pattern_leaders_without_pooling() -> None:
    rows = build_audit_rows(_frame(), market="nse", tracker_records=_tracker())

    assert rows[0]["tracking"]["identification_state"] == "rejected"
    assert rows[0]["tracking"]["rejection_buckets"] == [
        "insufficient_evidence",
        "market_context",
    ]
    assert rows[1]["tracking"]["identification_state"] == "no_setup_candidate"
    assert rows[1]["tracking"]["execution_state"] == "no_call"
    assert all(row["market"] == "nse" for row in rows)


def test_missing_tracker_session_is_not_misreported_as_no_setup() -> None:
    rows = build_audit_rows(_frame(), market="nse", tracker_records=[])
    assert {row["tracking"]["identification_state"] for row in rows} == {
        "tracker_not_observed"
    }


def test_artifact_is_hash_bound_and_tampering_is_not_a_pass(
    tmp_path: Path, monkeypatch
) -> None:
    from tradedesk import leader_discovery

    monkeypatch.setattr(
        leader_discovery,
        "extract_causal_universe",
        lambda *args, **kwargs: (
            _frame(),
            {
                "db_path": "test.duckdb",
                "latest_closed_session": "2026-10-08",
                "query_history_start": "2025-05-06",
                "mature_session_start": "2026-10-05",
                "mature_session_end": "2026-10-05",
                "mature_sessions": 1,
                "raw_rows_considered": 3,
            },
        ),
    )
    tracker = tmp_path / "tracker.jsonl"
    tracker.write_text(json.dumps(_tracker()[0]) + "\n", encoding="utf-8")
    output = tmp_path / "audit"
    report = run_missed_leader_audit(
        market="nse",
        db_path=tmp_path / "test.duckdb",
        tracker_path=tracker,
        output_root=output,
        now=datetime(2026, 10, 9, 12, tzinfo=IST),
        session_count=1,
    )

    validate_audit_report(report)
    assert report["markets_pooled"] is False
    assert report["eligible_for_live"] is False
    assert report["baseline_accuracy_improved"] is False
    assert report["latest_mature_audit"]["candidate_recall"] == 0.5
    assert load_latest_audit("nse", output_root=output)["status"] == "available"

    dataset = Path(report["dataset"]["path"])
    dataset.write_bytes(dataset.read_bytes() + b"tampered")
    invalid = load_latest_audit("nse", output_root=output)
    assert invalid["status"] == "invalid_or_unreadable"
    assert invalid["eligible_for_live"] is False
    assert "not a pass" in invalid["detail"]


def test_prospective_session_freeze_refuses_rewritten_causal_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    from tradedesk import leader_discovery

    current = _frame(date(2026, 10, 10))

    def extract(*args, **kwargs):
        return current.copy(), {
            "db_path": "test.duckdb",
            "latest_closed_session": "2026-10-13",
            "query_history_start": "2025-05-06",
            "mature_session_start": "2026-10-10",
            "mature_session_end": "2026-10-10",
            "mature_sessions": 1,
            "raw_rows_considered": 3,
        }

    monkeypatch.setattr(leader_discovery, "extract_causal_universe", extract)
    tracker_record = _tracker()[0] | {"armed_on": "2026-10-10"}
    tracker = tmp_path / "tracker.jsonl"
    tracker.write_text(json.dumps(tracker_record) + "\n", encoding="utf-8")
    output = tmp_path / "audit"
    first = run_missed_leader_audit(
        market="nse",
        db_path=tmp_path / "test.duckdb",
        tracker_path=tracker,
        output_root=output,
        now=datetime(2026, 10, 14, 12, tzinfo=IST),
        session_count=1,
    )
    assert len(first["prospective_session_freezes"]) == 1

    current.loc[0, "return_5"] = 9.9
    try:
        run_missed_leader_audit(
            market="nse",
            db_path=tmp_path / "test.duckdb",
            tracker_path=tracker,
            output_root=output,
            now=datetime(2026, 10, 14, 13, tzinfo=IST),
            session_count=1,
        )
    except ValueError as exc:
        assert "changed at immutable field causal_rows_sha256" in str(exc)
    else:
        raise AssertionError("rewritten prospective feature evidence was accepted")
