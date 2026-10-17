"""Export every `tradedesk-*` Windows scheduled task to deploy/schedule.json: the single,
platform-neutral list of jobs that deploy/linux/render_units.py turns into systemd timers.

Read-only towards Windows: it only queries Task Scheduler. Re-run it after changing a task on
Windows so the server's schedule stays identical. Times are wall-clock Asia/Kolkata.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy" / "schedule.json"

_PS = r"""
Get-ScheduledTask -TaskName 'tradedesk-*' | ForEach-Object {
  $t = $_
  [pscustomobject]@{
    name = $t.TaskName
    state = [string]$t.State
    execute = $t.Actions[0].Execute
    arguments = $t.Actions[0].Arguments
    time_limit = [string]$t.Settings.ExecutionTimeLimit
    triggers = @($t.Triggers | ForEach-Object {
      [pscustomobject]@{
        kind = ($_.CimClass.CimClassName -replace 'MSFT_Task', '' -replace 'Trigger', '')
        start = [string]$_.StartBoundary
        days_of_week = [int]$_.DaysOfWeek
        days_interval = [int]$_.DaysInterval
        repeat_every = [string]$_.Repetition.Interval
        repeat_for = [string]$_.Repetition.Duration
        enabled = [bool]$_.Enabled
      }
    })
  }
} | ConvertTo-Json -Depth 5
"""

DAY_BITS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")  # Task Scheduler's bit order


def _duration_minutes(value: str) -> int | None:
    """ISO-8601 duration (PT30M, PT8H, P1D) -> minutes."""

    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value or "")
    if not value or not match:
        return None
    days, hours, minutes, _ = (int(x or 0) for x in match.groups())
    return days * 1440 + hours * 60 + minutes


def _command(execute: str, arguments: str) -> list[str] | None:
    """The job as `run_hidden.py <log> <script|tradedesk> args...`, or None if it is not a
    run_hidden job (those are listed for review, not ported blindly)."""

    parts = re.findall(r'"[^"]*"|\S+', arguments or "")
    parts = [p.strip('"') for p in parts]
    if parts and parts[0].replace("\\", "/").endswith("scripts/run_hidden.py"):
        return parts[1:]
    return None


def normalise(task: dict[str, Any]) -> dict[str, Any]:
    triggers = []
    for trig in task.get("triggers") or []:
        start = datetime.fromisoformat(trig["start"]) if trig.get("start") else None
        days = [DAY_BITS[i] for i in range(7) if trig.get("days_of_week", 0) & (1 << i)]
        triggers.append(
            {
                "kind": trig["kind"],  # Weekly / Daily / Time / Logon / Boot
                "at": start.strftime("%H:%M") if start else None,
                "first_date": start.date().isoformat() if start else None,
                "days": days,
                "every_days": trig.get("days_interval") or None,
                "repeat_minutes": _duration_minutes(trig.get("repeat_every", "")),
                "repeat_for_minutes": _duration_minutes(trig.get("repeat_for", "")),
                "enabled": trig.get("enabled", True),
            }
        )
    return {
        "name": task["name"],
        "enabled": task.get("state") != "Disabled",
        "command": _command(task.get("execute", ""), task.get("arguments", "")),
        "windows_action": f"{task.get('execute', '')} {task.get('arguments', '')}".strip(),
        "time_limit_minutes": _duration_minutes(task.get("time_limit", "")),
        "triggers": triggers,
    }


def main() -> int:
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout  # fmt: skip
    tasks = json.loads(raw)
    tasks = tasks if isinstance(tasks, list) else [tasks]
    jobs = sorted((normalise(t) for t in tasks), key=lambda j: j["name"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"timezone": "Asia/Kolkata", "jobs": jobs}, indent=2) + "\n",
                   encoding="utf-8")  # fmt: skip
    unported = [j["name"] for j in jobs if j["command"] is None]
    print(f"wrote {OUT} with {len(jobs)} jobs; not run_hidden (review by hand): {unported}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
