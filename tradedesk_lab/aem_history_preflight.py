"""Read-only, fail-closed local coordination for isolated historical downloads.

This is a point-in-time local audit, not an account-wide lock or usage ledger. It
does not read credentials, stop services, change tasks, or authorize token renewal.
Raw command lines are inspected only in memory and never returned in reports.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SCHEDULE_GUARD_SECONDS = 120
_BROKER_SCRIPTS = re.compile(
    r"\b(?:backfill_intraday|bse_signal_tracker|options_snapshot|poll_close_finality)\.py\b"
)
_OFFLINE_SCRIPTS = re.compile(
    r"\b(?:research_tracker|nse_signal_tracker|crypto_signal_tracker|"
    r"build_indicator_reference|entry_search|validate_intraday_setup|"
    r"backfill_signal_tracker)\.py\b"
)
_OFFLINE_LAB = {
    "aem-benchmark",
    "aem-universe-plan",
    "aem-prepare",
    "aem-report",
    "nse-screen",
    "verify-base",
    "status",
}
_SNAPSHOT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$processRows = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID
} | ForEach-Object {
    [pscustomobject]@{pid=[int]$_.ProcessId; parent_pid=[int]$_.ParentProcessId;
        name=[string]$_.Name; command=[string]$_.CommandLine}
})
$taskRows = @(Get-ScheduledTask | ForEach-Object {
    $taskItem = $_
    $taskCommand = ($taskItem.Actions | ForEach-Object {
        [string]$_.Execute + ' ' + [string]$_.Arguments
    }) -join ' ; '
    $brokerPattern = 'tradedesk|Trade_Agent|indstocks|backfill_intraday|' +
        'bse_signal_tracker|options_snapshot|poll_close_finality'
    if ($taskItem.TaskName -like 'tradedesk-*' -or $taskCommand -match $brokerPattern) {
        $taskInfo = Get-ScheduledTaskInfo -InputObject $taskItem
        $next = $null
        if ($taskInfo.NextRunTime -and $taskInfo.NextRunTime.Year -gt 2000) {
            $next = $taskInfo.NextRunTime.ToUniversalTime().ToString('o')
        }
        [pscustomobject]@{name=[string]$taskItem.TaskName;
            state=[string]$taskItem.State; command=$taskCommand; next_run=$next}
    }
})
[pscustomobject]@{processes=$processRows; tasks=$taskRows} | ConvertTo-Json -Depth 5 -Compress
"""


def _windows_snapshot() -> dict:
    """Capture privately: neither stdout nor exceptions may expose command lines."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SNAPSHOT_SCRIPT],
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout.lstrip("\ufeff"))


def _classification(name: str, command: str) -> str:
    """Conservatively classify known launchers without persisting their arguments."""
    line = command.lower().replace('"', "").replace("'", "")
    executable = name.lower()
    python_like = bool(re.fullmatch(r"(?:pythonw?|uv|tradedesk)(?:\.exe)?", executable))
    if not line:
        return "uninspectable_python_or_tradedesk" if python_like else "not_relevant"
    if _BROKER_SCRIPTS.search(line):
        return "known_broker_script"
    # A wrapper may contain several commands. Any explicit broker operation wins.
    operations = re.findall(r"\btradedesk(?:\.exe|\.cli)?\s+([a-z][a-z-]*)", line)
    if any(
        op in {"live", "auth", "candles", "quote", "stream", "instruments", "data"}
        for op in operations
    ):
        return "production_broker_operation"
    if "indstocks" in line:
        return "explicit_broker_reference"
    lab_operations = re.findall(r"\btradedesk_lab(?:\.__main__)?\s+([a-z][a-z-]*)", line)
    if lab_operations:
        return (
            "known_offline_research"
            if all(op in _OFFLINE_LAB for op in lab_operations)
            else "other_research_consumer"
        )
    if operations and all(op == "dashboard" for op in operations):
        return "standalone_dashboard"
    if re.search(r"(?:^|\s)-m\s+(?:pytest|ruff|mypy)\b|\b(?:pytest|ruff)\.exe\b", line):
        return "offline_development_tool"
    if _OFFLINE_SCRIPTS.search(line):
        return "known_offline_script"
    if "cloudflared" in line:
        return "local_tunnel"
    if python_like:
        return "unclassified_python_or_tradedesk"
    if re.search(r"tradedesk|trade_agent|backfill_intraday", line):
        return "unclassified_repository_consumer"
    return "not_relevant"


_NON_CONSUMERS = {
    "not_relevant",
    "standalone_dashboard",
    "offline_development_tool",
    "known_offline_script",
    "known_offline_research",
    "local_tunnel",
}


def _safe_name(value: str, fallback: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else fallback


def _assess_snapshot(snapshot: dict, now: datetime, pid: int) -> dict:
    """Validate and reduce a private snapshot to a credential-free audit."""
    if not isinstance(snapshot, dict):
        raise ValueError("invalid snapshot")
    processes, tasks = snapshot["processes"], snapshot["tasks"]
    if not isinstance(processes, list) or not isinstance(tasks, list):
        raise ValueError("invalid snapshot collections")
    parents = {}
    for row in processes:
        if (
            not isinstance(row, dict)
            or not all(
                isinstance(row.get(key), int) and not isinstance(row[key], bool)
                for key in ("pid", "parent_pid")
            )
            or not all(isinstance(row.get(key), str) for key in ("name", "command"))
        ):
            raise ValueError("invalid process row")
        if row["pid"] in parents:
            raise ValueError("duplicate process id")
        parents[row["pid"]] = row["parent_pid"]
    # Ignore this collector and its launchers, not other collectors with similar CLI text.
    exempt = {pid}
    cursor = parents.get(pid)
    while cursor and cursor not in exempt:
        exempt.add(cursor)
        cursor = parents.get(cursor)
    if pid not in parents:
        raise ValueError("current process missing from audit")
    process_audit = []
    for row in processes:
        if row["pid"] in exempt:
            continue
        kind = _classification(row["name"], row["command"])
        if kind != "not_relevant":
            process_audit.append(
                {
                    "pid": row["pid"],
                    "name": _safe_name(row["name"], "process"),
                    "classification": kind,
                    "blocks": kind not in _NON_CONSUMERS,
                }
            )
    task_audit = []
    for row in tasks:
        if (
            not isinstance(row, dict)
            or not all(isinstance(row.get(key), str) for key in ("name", "state", "command"))
            or row.get("state") not in {"Ready", "Running", "Disabled", "Queued", "Unknown"}
        ):
            raise ValueError("invalid task row")
        kind = _classification("", row["command"])
        # Relevant tasks with opaque wrappers remain possible consumers.
        if kind == "not_relevant":
            kind = "unclassified_scheduled_task"
        raw_next = row.get("next_run")
        next_run = None
        if raw_next is not None:
            if not isinstance(raw_next, str):
                raise ValueError("invalid next run")
            next_run = datetime.fromisoformat(raw_next)
            if next_run.tzinfo is None or next_run.utcoffset() is None:
                raise ValueError("naive next run")
        consumer = kind not in _NON_CONSUMERS
        running = row["state"] in {"Running", "Queued", "Unknown"}
        due = (
            row["state"] != "Disabled"
            and next_run is not None
            and (next_run <= now + timedelta(seconds=SCHEDULE_GUARD_SECONDS))
        )
        task_audit.append(
            {
                "name": _safe_name(row["name"], "scheduled_task"),
                "state": row["state"],
                "classification": kind,
                "next_run": next_run.astimezone(UTC).isoformat() if next_run else None,
                "blocks": bool(consumer and (running or due)),
            }
        )
    reasons = []
    if any(row["blocks"] for row in process_audit):
        reasons.append("active_possible_broker_consumer")
    if any(row["blocks"] for row in task_audit):
        reasons.append("running_or_imminent_possible_broker_task")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "processes": sorted(process_audit, key=lambda row: row["pid"]),
        "tasks": sorted(task_audit, key=lambda row: row["name"]),
    }


def history_preflight(now: datetime | None = None) -> dict:
    """Check before client creation and each request; no override or side effects.

    Weekdays 09:00 through 16:00 IST are protected regardless of holiday claims.
    The two-minute leading guard prevents starting a request just before protection.
    Outside those times, inability to inspect processes/tasks is a blocking result.
    """
    now = now or datetime.now(UTC)
    report = {
        "allowed": False,
        "reasons": [],
        "audit_status": "not_run",
        "processes": [],
        "tasks": [],
        "schedule_guard_seconds": SCHEDULE_GUARD_SECONDS,
        "limitations": [
            "Point-in-time local process/task audit; concurrent starts remain possible.",
            "Other machines and earlier account usage are not observable here.",
            "The broker rate limiter is per instance, not an aggregate account budget.",
            "This preflight never authorizes token generation, refresh, or service interruption.",
        ],
    }
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        report["reasons"] = ["timezone_aware_clock_required"]
        return report
    now = now.astimezone(IST)
    report["checked_at"] = now.isoformat()
    if platform.system() != "Windows":
        report["reasons"].append("unsupported_operating_system")
    if now.weekday() < 5 and time(9) <= now.time() < time(16):
        report["reasons"].append("protected_weekday_market_window")
    elif now.weekday() < 5 and time(8, 58) <= now.time() < time(9):
        report["reasons"].append("protected_weekday_market_window_starting")
    if report["reasons"]:
        return report
    try:
        reduced = _assess_snapshot(_windows_snapshot(), now, os.getpid())
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        # Exception text can contain captured command lines or credentials: omit it.
        report["reasons"] = ["local_process_or_scheduler_audit_unavailable"]
        report["audit_status"] = "failed"
        return report
    report.update(reduced, audit_status="complete")
    return report
