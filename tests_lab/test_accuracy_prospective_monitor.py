import json
from datetime import date, timedelta

import pytest
from tradedesk_lab.accuracy_prospective_monitor import (
    _append_events,
    clustered_lower_bound,
    read_audit,
)


def test_hash_chained_audit_is_idempotent_and_detects_tampering(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    additions = [
        {"event_id": "activation:x", "kind": "activation", "payload": {"cutoff": "x"}},
        {"event_id": "prediction:y", "kind": "prediction", "payload": {"p": 0.8}},
    ]
    first = _append_events(path, additions)
    second = _append_events(path, additions)
    assert len(first) == len(second) == 2
    assert second[1]["previous_sha256"] == second[0]["sha256"]

    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[1])
    event["payload"]["p"] = 0.1
    lines[1] = json.dumps(event)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid hash"):
        read_audit(path)


def _resolved(day: date, first: int, second: int) -> list[dict]:
    return [
        {
            "armed_on": day.isoformat(),
            "prospective_eligible": True,
            "selected": True,
            "status": "resolved",
            "label": first,
        },
        {
            "armed_on": day.isoformat(),
            "prospective_eligible": True,
            "selected": True,
            "status": "resolved",
            "label": second,
        },
    ]


def test_cluster_bounds_remain_unavailable_until_independent_groups_exist() -> None:
    start = date(2026, 1, 5)
    records = []
    for offset in range(9):
        records.extend(_resolved(start + timedelta(days=offset), 1, 1))
    assert clustered_lower_bound(records, grouping="session", resamples=200) is None
    assert clustered_lower_bound(records, grouping="week", resamples=200) is None


def test_session_and_week_bootstrap_use_whole_groups() -> None:
    start = date(2026, 1, 5)
    records = []
    for offset in range(35):
        records.extend(_resolved(start + timedelta(days=offset), 1, 1 if offset % 5 else 0))
    session = clustered_lower_bound(records, grouping="session", resamples=500)
    week = clustered_lower_bound(records, grouping="week", resamples=500)
    assert session is not None and 0.70 < session <= 1.0
    assert week is not None and 0.70 < week <= 1.0
