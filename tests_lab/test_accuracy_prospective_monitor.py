import json
from datetime import date, timedelta
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest
import tradedesk_lab.accuracy_prospective_monitor as monitor_module
from tradedesk_lab.accuracy_prospective_monitor import (
    _append_events,
    _watchlist_availability,
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


def _availability_state() -> dict:
    return {
        "activation": {
            "forward_after": "2026-10-03",
            "activated_at": "2026-10-03T10:00:00+05:30",
            "candidate": {"setup": "trend_pullback"},
        },
        "records": [],
    }


def _stub_benchmark(monkeypatch, tmp_path, sessions: list[str]) -> None:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(data / "tradedesk.duckdb")):
        pass
    monkeypatch.setattr(
        monitor_module,
        "load_config",
        lambda _root: SimpleNamespace(universe=SimpleNamespace(benchmark="NIFTY 50")),
    )
    monkeypatch.setattr(
        monitor_module, "_index_codes", lambda _con: {"NIFTY 50": "NSE_26000"}
    )
    monkeypatch.setattr(
        monitor_module,
        "_load_bars",
        lambda _con, _code: pd.DataFrame(index=pd.to_datetime(sessions)),
    )


def _write_watchlist(
    tmp_path, on: str, entries: list[dict], *, generated_at: str | None = None
) -> None:
    directory = tmp_path / "data/watchlists"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{on}.json").write_text(
        json.dumps(
            {
                "on": on,
                "generated_at": generated_at or f"{on}T16:05:00+05:30",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )


def test_missing_whole_benchmark_session_is_not_hidden(monkeypatch, tmp_path) -> None:
    _stub_benchmark(monkeypatch, tmp_path, ["2026-10-03", "2026-10-05", "2026-10-06"])
    _write_watchlist(tmp_path, "2026-10-05", [])

    result = _watchlist_availability(tmp_path, _availability_state())

    assert result["expected_sessions"] == 2
    assert result["missing_watchlist_sessions"] == ["2026-10-06"]
    assert result["missing_collection_sessions"] == []
    assert result["sessions"][-1]["watchlist_available"] is False


def test_zero_candidate_watchlist_counts_without_fabricating_a_missing_collection(
    monkeypatch, tmp_path
) -> None:
    _stub_benchmark(monkeypatch, tmp_path, ["2026-10-05"])
    _write_watchlist(tmp_path, "2026-10-05", [])

    result = _watchlist_availability(tmp_path, _availability_state())

    assert result["missing_watchlist_sessions"] == []
    assert result["missing_collection_sessions"] == []
    assert result["zero_selected_sessions"] == 1


def test_present_candidates_without_m8_evaluation_fail_collection_completeness(
    monkeypatch, tmp_path
) -> None:
    _stub_benchmark(monkeypatch, tmp_path, ["2026-10-05"])
    _write_watchlist(
        tmp_path,
        "2026-10-05",
        [{"signal": {"setup": "trend_pullback"}}],
    )

    result = _watchlist_availability(tmp_path, _availability_state())

    assert result["missing_watchlist_sessions"] == []
    assert result["missing_collection_sessions"] == ["2026-10-05"]


def test_late_zero_candidate_watchlist_is_not_a_valid_zero_call_session(
    monkeypatch, tmp_path
) -> None:
    _stub_benchmark(monkeypatch, tmp_path, ["2026-10-05"])
    _write_watchlist(
        tmp_path,
        "2026-10-05",
        [],
        generated_at="2026-10-06T10:00:00+05:30",
    )

    state = _availability_state()
    result = _watchlist_availability(tmp_path, state)

    assert result["missing_watchlist_sessions"] == []
    assert result["late_watchlist_sessions"] == ["2026-10-05"]
    assert result["sessions"][0]["registration_valid"] is False


def test_stored_summary_compares_equal_to_numpy_recomputed_summary() -> None:
    from tradedesk_lab.accuracy_prospective_monitor import _canonical

    stored = {"gate_checks": {"session_target_rate": "True", "accuracy": True}}
    fresh = {"gate_checks": {"session_target_rate": np.True_, "accuracy": True}}
    assert _canonical(stored) == _canonical(fresh)
    assert _canonical({"a": 1}) != _canonical({"a": 2})


def test_quarantine_file_lists_sessions_and_defaults_to_none(tmp_path) -> None:
    from tradedesk_lab.accuracy_prospective_monitor import _quarantined_sessions

    assert _quarantined_sessions(tmp_path) == {}
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "evidence_quarantine.yaml").write_text(
        "quarantined_sessions:\n  - session: '2026-10-05'\n    reason: lost original\n",
        encoding="utf-8",
    )
    assert _quarantined_sessions(tmp_path) == {
        "2026-10-05": frozenset({"source_watchlist_hash"})
    }


def test_quarantine_names_the_checks_it_waives_and_refuses_unknown_ones(tmp_path) -> None:
    from tradedesk_lab.accuracy_prospective_monitor import _quarantined_sessions

    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "evidence_quarantine.yaml"
    path.write_text(
        "quarantined_sessions:\n"
        "  - session: '2026-10-06'\n    waives: [late_watchlist_registration]\n",
        encoding="utf-8",
    )
    assert _quarantined_sessions(tmp_path) == {
        "2026-10-06": frozenset({"late_watchlist_registration"})
    }
    path.write_text(
        "quarantined_sessions:\n  - session: '2026-10-06'\n    waives: [prediction_hash]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cannot waive"):
        _quarantined_sessions(tmp_path)


def _prediction_event(signal_id: str, sha: str, recorded_at: str) -> dict:
    return {
        "event_id": f"prediction:{signal_id}",
        "kind": "prediction",
        "recorded_at": recorded_at,
        "payload": {"signal_id": signal_id, "prediction_sha256": sha},
    }


def test_late_waiver_never_admits_a_prediction_registered_after_the_deadline() -> None:
    from tradedesk_lab.accuracy_prospective_monitor import _waived_late_sessions

    waive = frozenset({"late_watchlist_registration"})
    quarantined = {
        "2026-10-05": waive,  # counted rows, audit proves on-time registration -> waived
        "2026-10-06": waive,  # no counted rows -> waived
        "2026-10-07": waive,  # counted row with no audit event -> refused
        "2026-10-08": frozenset({"source_watchlist_hash"}),  # a different waiver -> refused
        "2026-10-12": waive,  # counted row, audit recorded after the deadline -> refused
        "2026-10-13": waive,  # counted row, audit holds a different prediction -> refused
    }
    records = [
        {"armed_on": "2026-10-05", "prospective_eligible": True, "signal_id": "a",
         "prediction_sha256": "h-a"},
        {"armed_on": "2026-10-06", "prospective_eligible": False, "signal_id": "b"},
        {"armed_on": "2026-10-07", "prospective_eligible": True, "signal_id": "c",
         "prediction_sha256": "h-c"},
        {"armed_on": "2026-10-08", "prospective_eligible": False, "signal_id": "d"},
        {"armed_on": "2026-10-12", "prospective_eligible": True, "signal_id": "e",
         "prediction_sha256": "h-e"},
        {"armed_on": "2026-10-13", "prospective_eligible": True, "signal_id": "f",
         "prediction_sha256": "h-f"},
    ]  # fmt: skip
    events = [
        _prediction_event("a", "h-a", "2026-10-05T17:08:28+00:00"),  # 22:38 IST, before 09:15
        _prediction_event("e", "h-e", "2026-10-13T04:00:00+00:00"),  # 09:30 IST, after 09:15
        _prediction_event("f", "h-other", "2026-10-13T12:00:00+00:00"),
    ]
    late = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"]
    assert _waived_late_sessions(late, quarantined, records, events) == [
        "2026-10-05",
        "2026-10-06",
    ]
    assert _waived_late_sessions(late, quarantined, records) == ["2026-10-06"]  # no audit


def test_repository_quarantine_config_is_valid_and_only_waives_known_checks() -> None:
    from pathlib import Path

    from tradedesk_lab.accuracy_prospective_monitor import _quarantined_sessions

    root = Path(monitor_module.__file__).resolve().parents[1]
    sessions = _quarantined_sessions(root)
    assert sessions["2026-10-05"] == frozenset(
        {"source_watchlist_hash", "late_watchlist_registration"}
    )
    for day in ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12"):
        assert sessions[day] == frozenset({"late_watchlist_registration"})


def test_audit_hash_keeps_the_compact_form_the_stored_chain_was_written_with():
    """Regression (2026-10-14): a second module-level `_canonical` with default separators
    silently replaced the first, so `_sha` stopped matching every stored audit link and the
    NSE pipeline failed at M9 with "audit chain link 1 has an invalid hash"."""
    import ast
    import hashlib
    from pathlib import Path

    event = {"b": 1, "a": [1, 2], "flag": np.bool_(True)}
    expected = hashlib.sha256(b'{"a":[1,2],"b":1,"flag":"True"}').hexdigest()
    assert monitor_module._sha(event) == expected

    source = Path(monitor_module.__file__).read_text(encoding="utf-8")
    names = [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in {"_canonical", "_sha"}
    ]
    assert sorted(names) == ["_canonical", "_sha"]  # each defined exactly once
