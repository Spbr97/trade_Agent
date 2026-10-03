from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from tradedesk_lab.accuracy_prospective_timing import (
    MAX_OFFSET,
    MIN_OFFSET,
    N_COHORTS,
    _new_record,
    _resolve_placebo,
    _verify_record,
    freeze_offsets,
    summarize_timing,
)
from tradedesk_lab.artifacts import ROOT


def _m8_record(signal_id: str, *, selected: bool = True) -> dict:
    return {
        "signal_id": signal_id,
        "scrip_code": "NSE_TEST",
        "symbol": "TEST",
        "armed_on": "2026-01-01",
        "score_deadline": "2026-01-01T23:59:00+00:00",
        "prospective_eligible": True,
        "prediction_sha256": f"hash-{signal_id}",
        "selected": selected,
        "status": "pending",
    }


def test_offsets_are_deterministic_and_inside_frozen_window() -> None:
    first = freeze_offsets("signal-a")
    second = freeze_offsets("signal-a")

    assert first == second
    assert len(first) == N_COHORTS
    assert min(first) >= MIN_OFFSET
    assert max(first) <= MAX_OFFSET
    assert len(set(first)) > 1


def test_registration_is_prospective_and_tamper_evident() -> None:
    source = _m8_record("signal-a")
    record = _new_record(source, datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert record["prospective_eligible"] is True
    _verify_record(record, source)

    record["cohort_offsets"][0] = MAX_OFFSET + 1
    with pytest.raises(ValueError, match="timing assignment changed"):
        _verify_record(record, source)

    late = _new_record(source, datetime(2026, 1, 2, tzinfo=UTC))
    assert late["prospective_eligible"] is False
    assert {row["status"] for row in late["placebos"]} == {"excluded_late"}


def test_placebo_waits_for_maturity_and_hashes_causal_source() -> None:
    stamps = pd.date_range("2025-12-01", periods=50, freq="B")
    bars = pd.DataFrame(
        {
            "open": [100.0] * len(stamps),
            "high": [102.0] * len(stamps),
            "low": [99.0] * len(stamps),
            "close": [101.0] * len(stamps),
        },
        index=stamps,
    )
    placebo = _new_record(
        _m8_record("signal-a"), datetime(2026, 1, 1, 12, tzinfo=UTC)
    )["placebos"][0]
    sessions = stamps[stamps >= pd.Timestamp("2026-01-01")]
    assert placebo["offset_sessions"] == MIN_OFFSET
    assert not _resolve_placebo(
        placebo,
        armed_on="2026-01-01",
        bars=bars,
        sessions=list(sessions[:5].date),
        root=ROOT,
    )
    assert placebo["status"] == "pending"

    assert _resolve_placebo(
        placebo,
        armed_on="2026-01-01",
        bars=bars,
        sessions=list(sessions.date),
        root=ROOT,
    )
    assert placebo["status"] == "resolved"
    assert placebo["label"] == 1
    assert placebo["source_sha256"]

    changed = bars.copy()
    changed.iloc[0, changed.columns.get_loc("close")] = 100.5
    with pytest.raises(ValueError, match="timing source changed"):
        _resolve_placebo(
            placebo,
            armed_on="2026-01-01",
            bars=changed,
            sessions=list(sessions.date),
            root=ROOT,
        )


def test_summary_requires_real_paired_random_timing_advantage() -> None:
    timing_records = []
    actual_records = []
    start = date(2026, 1, 1)
    for index in range(100):
        armed_on = (start + timedelta(days=index // 2)).isoformat()
        signal_id = f"signal-{index}"
        timing_records.append(
            {
                "signal_id": signal_id,
                "armed_on": armed_on,
                "prospective_eligible": True,
                "cohort_offsets": [MIN_OFFSET] * N_COHORTS,
                "placebos": [
                    {
                        "offset_sessions": offset,
                        "status": "resolved",
                        "label": 0,
                        "net_r": -1.0,
                    }
                    for offset in range(MIN_OFFSET, MAX_OFFSET + 1)
                ],
            }
        )
        actual_records.append(
            {
                "signal_id": signal_id,
                "armed_on": armed_on,
                "status": "resolved",
                "label": 1,
                "net_r": 0.5,
            }
        )

    summary = summarize_timing(
        {"records": timing_records}, {"records": actual_records}
    )

    assert summary["status"] == "random_timing_pass"
    assert summary["paired_resolved_calls"] == 100
    assert summary["active_sessions"] == 50
    assert summary["timing_advantage_r"] == 1.5
    assert summary["p_value"] == pytest.approx(1 / (N_COHORTS + 1))
    assert summary["is_broader_random_timing_control"] is True
    assert summary["random_timing_gate_passed"] is True
    assert summary["eligible_for_live"] is False
