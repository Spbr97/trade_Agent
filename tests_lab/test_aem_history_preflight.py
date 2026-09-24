import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from tradedesk_lab import aem_history_preflight as preflight

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 9, 24, 18, tzinfo=IST)


def _snapshot(*, processes=(), tasks=()):
    return {
        "processes": [
            {
                "pid": 10,
                "parent_pid": 9,
                "name": "python.exe",
                "command": "python -m tradedesk_lab aem-history-collect",
            },
            {
                "pid": 9,
                "parent_pid": 0,
                "name": "uv.exe",
                "command": "uv run python -m tradedesk_lab aem-history-collect",
            },
            *processes,
        ],
        "tasks": list(tasks),
    }


def _process(command, *, pid=20, name="python.exe"):
    return {"pid": pid, "parent_pid": 1, "name": name, "command": command}


def _task(command, *, state="Ready", next_run=None, name="tradedesk-test"):
    return {"name": name, "state": state, "command": command, "next_run": next_run}


@pytest.fixture
def mock_windows(monkeypatch):
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    monkeypatch.setattr(preflight.os, "getpid", lambda: 10)
    monkeypatch.setattr(preflight, "_windows_snapshot", _snapshot)


@pytest.mark.parametrize(
    "hour,minute,reason",
    [
        (8, 58, "protected_weekday_market_window_starting"),
        (9, 0, "protected_weekday_market_window"),
        (15, 59, "protected_weekday_market_window"),
    ],
)
def test_market_window_never_audits_or_reads_credentials(
    mock_windows, monkeypatch, hour, minute, reason
):
    monkeypatch.setattr(preflight, "_windows_snapshot", lambda: pytest.fail("audit not needed"))
    report = preflight.history_preflight(datetime(2026, 9, 24, hour, minute, tzinfo=IST))
    assert report["allowed"] is False
    assert report["reasons"] == [reason]
    assert report["audit_status"] == "not_run"


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 9, 24, 8, 57, tzinfo=IST),
        datetime(2026, 9, 24, 16, tzinfo=IST),
        datetime(2026, 9, 26, 10, tzinfo=IST),
    ],
)
def test_unguarded_times_still_require_successful_audit(mock_windows, now):
    report = preflight.history_preflight(now)
    assert report["allowed"] is True
    assert report["audit_status"] == "complete"
    assert report["limitations"]


def test_clock_and_platform_fail_closed(mock_windows, monkeypatch):
    assert preflight.history_preflight(datetime(2026, 9, 24, 18))["reasons"] == [
        "timezone_aware_clock_required"
    ]
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")
    assert preflight.history_preflight(NOW)["reasons"] == ["unsupported_operating_system"]


@pytest.mark.parametrize(
    "command",
    [
        "tradedesk live --no-dashboard --market bse",
        "tradedesk auth check --secret DO_NOT_REPORT",
        "tradedesk.exe data load",
        "tradedesk instruments refresh",
        "tradedesk quote NSE_1",
        "python scripts/bse_signal_tracker.py",
        "python scripts/options_snapshot.py",
        "python scripts/poll_close_finality.py",
        "python scripts/backfill_intraday.py",
        "python -m tradedesk_lab aem-history-collect",
        "python unknown_job.py",
        "",
    ],
)
def test_consumer_and_opaque_python_block_without_argument_leaks(command):
    report = preflight._assess_snapshot(_snapshot(processes=[_process(command)]), NOW, 10)
    assert report["allowed"] is False
    assert report["reasons"] == ["active_possible_broker_consumer"]
    encoded = json.dumps(report)
    assert "DO_NOT_REPORT" not in encoded
    assert "command" not in encoded


@pytest.mark.parametrize(
    "command",
    [
        '"C:\\venv\\tradedesk.exe" dashboard --port 8765',
        "python -m pytest tests_lab -q",
        "python -m ruff check tradedesk_lab",
        "python -m tradedesk_lab aem-benchmark",
        "python scripts/research_tracker.py",
        "python scripts/crypto_signal_tracker.py",
    ],
)
def test_known_non_broker_consumers_are_not_false_positives(command):
    report = preflight._assess_snapshot(_snapshot(processes=[_process(command)]), NOW, 10)
    assert report["allowed"] is True


def test_wrapper_dashboard_does_not_hide_another_broker_command():
    row = _process("tradedesk dashboard ; tradedesk data load", name="powershell.exe")
    assert not preflight._assess_snapshot(_snapshot(processes=[row]), NOW, 10)["allowed"]


@pytest.mark.parametrize(
    "state,next_run,blocks",
    [
        ("Running", None, True),
        ("Queued", None, True),
        ("Unknown", None, True),
        ("Ready", "2026-09-24T18:02:00+05:30", True),
        ("Ready", "2026-09-24T18:02:01+05:30", False),
        ("Ready", "2026-09-24T17:59:00+05:30", True),
        ("Disabled", "2026-09-24T18:00:00+05:30", False),
    ],
)
def test_scheduled_consumers_running_due_and_overdue(state, next_run, blocks):
    task = _task("python scripts/options_snapshot.py", state=state, next_run=next_run)
    result = preflight._assess_snapshot(_snapshot(tasks=[task]), NOW, 10)
    assert result["allowed"] is (not blocks)
    assert result["tasks"][0]["blocks"] is blocks


def test_running_standalone_dashboard_and_tunnel_are_permitted():
    tasks = [
        _task("tradedesk dashboard", state="Running", name="tradedesk-dashboard"),
        _task("cloudflared tunnel run", state="Running", name="tradedesk-tunnel"),
    ]
    assert preflight._assess_snapshot(_snapshot(tasks=tasks), NOW, 10)["allowed"]


def test_unknown_running_scheduled_wrapper_blocks_and_name_is_sanitized():
    task = _task("powershell -File opaque.ps1", state="Running", name="token=SECRET")
    report = preflight._assess_snapshot(_snapshot(tasks=[task]), NOW, 10)
    assert not report["allowed"]
    assert "SECRET" not in json.dumps(report)


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("DO_NOT_REPORT"),
        subprocess.CalledProcessError(1, "DO_NOT_REPORT", output="TOKEN_SECRET"),
        subprocess.TimeoutExpired("DO_NOT_REPORT", 1, output="TOKEN_SECRET"),
        ValueError("DO_NOT_REPORT"),
    ],
)
def test_audit_failures_are_blocked_and_sanitized(mock_windows, monkeypatch, failure):
    def fail():
        raise failure

    monkeypatch.setattr(preflight, "_windows_snapshot", fail)
    report = preflight.history_preflight(NOW)
    assert not report["allowed"]
    assert report["audit_status"] == "failed"
    assert "DO_NOT_REPORT" not in json.dumps(report)
    assert "TOKEN_SECRET" not in json.dumps(report)


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"processes": {}, "tasks": []},
        {"processes": [], "tasks": []},
        _snapshot(processes=[{"pid": True, "parent_pid": 1, "name": "python", "command": ""}]),
        _snapshot(tasks=[_task("tradedesk live", next_run="2026-09-24T18:00:00")]),
        _snapshot(tasks=[_task("tradedesk live", state="Bogus")]),
    ],
)
def test_malformed_or_incomplete_audit_is_not_permission(mock_windows, monkeypatch, snapshot):
    monkeypatch.setattr(preflight, "_windows_snapshot", lambda: snapshot)
    report = preflight.history_preflight(NOW)
    assert not report["allowed"]
    assert report["reasons"] == ["local_process_or_scheduler_audit_unavailable"]
