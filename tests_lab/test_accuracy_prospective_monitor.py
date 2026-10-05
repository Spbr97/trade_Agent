import json
from datetime import date, timedelta
from types import SimpleNamespace

import duckdb
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
