"""Make silent failures loud.

The NSE after-close pipeline failed its critical step every day from 6 to 14 Oct 2026 and
nothing said so. This check, run hourly by its own scheduled task, reads every
`tradedesk-*` task's last result (Windows Task Scheduler, or systemd on Linux) and the
accuracy-collection pipeline status files,
writes data/reports/health.json (the dashboard shows a banner only when something is
failing) and sends each NEW failure once as a desktop toast and an ntfy phone push (plus
Telegram when it is configured) - and one more when it clears. It also alerts on any new
mover-study survivor.
Read-only towards everything it checks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST

REPORT_PATH = Path("data/reports/health.json")
PIPELINE_ROOT = Path("data/m14_m18/accuracy_collection_health")
# Windows task results that are not failures: success, running, not yet run, terminated by
# a deliberate stop, and "already running" refusals from MultipleInstances=IgnoreNew.
BENIGN_RESULTS = frozenset({0, 267009, 267011, 267014, 2147750687, 2147946720})
PIPELINE_BAD = frozenset({"failed", "degraded"})
PIPELINE_MAX_AGE = {
    "nse": timedelta(days=4),
    "bse": timedelta(days=4),
    "crypto": timedelta(hours=14),
}

_PS = (
    "Get-ScheduledTask -TaskName 'tradedesk-*' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
    "[pscustomobject]@{ name = $_.TaskName; state = [string]$_.State; "
    "last_run = $i.LastRunTime.ToString('o'); result = [int64]$i.LastTaskResult } } "
    "| ConvertTo-Json -Compress"
)


RUNNING = 267009  # Task Scheduler's "currently running" code; reused for systemd


def parse_systemctl_show(text: str) -> list[dict[str, Any]]:
    """`systemctl show` blocks for tradedesk-*.service and .timer units -> the same rows
    Windows gives: name, state ("Disabled" when the timer is off), last_run, result."""

    units: dict[str, dict[str, str]] = {}
    for block in text.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id"):
            units[props["Id"]] = props
    rows = []
    for unit_id, props in sorted(units.items()):
        if not unit_id.endswith(".service"):
            continue
        name = unit_id.removesuffix(".service")
        timer = units.get(f"{name}.timer")
        status = int(props.get("ExecMainStatus") or 0)
        if props.get("ActiveState") in {"activating", "active", "reloading"}:
            result = RUNNING
        elif props.get("Result", "success") not in {"success", "exit-code"}:
            result = status or 1  # timeout, signal, core-dump: a failure even at status 0
        else:
            result = status
        disabled = timer is not None and timer.get("UnitFileState") not in {"enabled", "static"}
        rows.append(
            {
                "name": name,
                "state": "Disabled" if disabled else "Ready",
                "last_run": props.get("ExecMainStartTimestamp") or "never",
                "result": result,
            }
        )
    return rows


def _systemd_tasks() -> list[dict[str, Any]]:
    def systemctl(*args: str) -> str:
        return subprocess.run(
            ["systemctl", *args], capture_output=True, text=True, timeout=60, check=True
        ).stdout

    listed = systemctl(
        "list-units", "--all", "--plain", "--no-legend", "tradedesk-*.service", "tradedesk-*.timer"
    )
    names = [line.split()[0] for line in listed.splitlines() if line.strip()]
    if not names:
        return []
    props = "Id,Result,ExecMainStatus,ExecMainStartTimestamp,ActiveState,UnitFileState"
    return parse_systemctl_show(systemctl("show", f"--property={props}", *names))


def scheduled_tasks() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return _systemd_tasks()
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout.strip()
    rows = json.loads(out) if out else []
    return rows if isinstance(rows, list) else [rows]


def task_problems(tasks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    problems = []
    for task in tasks:
        if str(task.get("state")) == "Disabled":
            continue
        result = int(task.get("result") or 0)
        if result in BENIGN_RESULTS:
            continue
        problems.append(
            {
                "key": f"task:{task['name']}",
                "what": task["name"],
                "detail": f"last run {str(task.get('last_run'))[:16]} exited with {result}",
            }
        )
    return problems


def pipeline_problems(now: datetime, root: Path = PIPELINE_ROOT) -> list[dict[str, Any]]:
    problems = []
    for market, max_age in PIPELINE_MAX_AGE.items():
        path = root / f"{market}.json"
        if not path.exists():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append({"key": f"pipeline:{market}", "what": f"{market} pipeline",
                             "detail": "status file unreadable"})  # fmt: skip
            continue
        status = str(report.get("status"))
        stamp = report.get("finished_at") or report.get("started_at")
        if status in PIPELINE_BAD:
            problems.append(
                {
                    "key": f"pipeline:{market}",
                    "what": f"{market} after-close pipeline",
                    "detail": f"{status} at {report.get('failed_stage') or 'unknown stage'}",
                }
            )
        elif stamp and now - datetime.fromisoformat(stamp) > max_age:
            problems.append(
                {
                    "key": f"pipeline-stale:{market}",
                    "what": f"{market} after-close pipeline",
                    "detail": f"no completed run since {str(stamp)[:16]}",
                }
            )
    return problems


def check(
    now: datetime | None = None,
    *,
    tasks: Sequence[Mapping[str, Any]] | None = None,
    pipeline_root: Path = PIPELINE_ROOT,
) -> dict[str, Any]:
    now = (now or datetime.now(IST)).astimezone(IST)
    rows = list(tasks) if tasks is not None else scheduled_tasks()
    failing = task_problems(rows) + pipeline_problems(now, pipeline_root)
    return {
        "checked_at": now.isoformat(),
        "tasks_checked": len(rows),
        "status": "failing" if failing else "ok",
        "failing": failing,
    }


def changes(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> tuple[list, list]:
    before = {p["key"]: p for p in (previous or {}).get("failing", [])}
    now = {p["key"]: p for p in current.get("failing", [])}
    new = [p for k, p in now.items() if k not in before]
    cleared = [p for k, p in before.items() if k not in now]
    return new, cleared


def alert(title: str, body: str, *, urgent: bool = False) -> bool:
    """A Windows desktop toast where there is a desktop, plus an ntfy phone push when a topic
    is stored (`tradedesk alerts setup-ntfy`; the channel for a headless server). Best effort
    either way. True if any channel delivered."""

    sent = False
    if sys.platform == "win32":
        try:
            from tradedesk.alerts.cards import OutboundMessage
            from tradedesk.alerts.desktop import DesktopNotifier

            sent = DesktopNotifier().send(OutboundMessage(title=title, body=body, urgent=urgent))
        except Exception as exc:  # noqa: BLE001 - a health alert must never crash the check
            print(f"desktop notification not sent: {exc!r}")
    from tradedesk.alerts import ntfy

    return ntfy.send(title, body, urgent=urgent) or sent


def notify(new: list[dict[str, Any]], cleared: list[dict[str, Any]]) -> bool:
    """A desktop toast and ntfy push (see alert()); Telegram too only if enabled in
    config/alerts.yaml with a stored bot token."""

    if not new and not cleared:
        return False
    lines = [f"FAILING  {p['what']}: {p['detail']}" for p in new]
    lines += [f"CLEARED  {p['what']}" for p in cleared]
    sent = alert("tradedesk health", "\n".join(lines)[:400], urgent=bool(new))
    try:
        import asyncio

        from tradedesk.alerts.cards import OutboundMessage
        from tradedesk.alerts.telegram import TelegramBot, load_bot_token
        from tradedesk.config import load_config

        settings = load_config(Path("."))
        telegram = settings.alerts.telegram
        token = load_bot_token()
        if telegram.enabled and telegram.allowed_chat_id and token:
            message = OutboundMessage(title="tradedesk health", body="\n".join(lines)[:3500])
            bot = TelegramBot(token, int(telegram.allowed_chat_id))
            sent = asyncio.run(bot.send(message)) or sent
    except Exception as exc:  # noqa: BLE001
        print(f"telegram health notification not sent: {exc!r}")
    return sent


SURVIVOR_FILES = (
    "mover_study/nse",
    "mover_study/bse",
    "intraday_mover_study/nse",
    "intraday_mover_study/bse",
    "intraday_mover_study/crypto",
)
NOTIFIED_PATH = Path("data/reports/health_notified.json")
LAB_ROOT = Path("data/m14_m18")


def watch_survivors(
    notified_path: Path = NOTIFIED_PATH, lab_root: Path = LAB_ROOT
) -> list[str]:
    """Toast any mover-study search survivor not seen before, and remember it. Also records
    the current failing keys so a person or tool reading the file sees what was announced."""

    try:
        state = json.loads(notified_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {}
    seen = set(state.get("survivors_notified", []))
    fresh: list[str] = []
    for part in SURVIVOR_FILES:
        try:
            found = json.loads((lab_root / part / "search_latest.json").read_text("utf-8-sig"))
        except (OSError, ValueError):
            continue
        for item in found.get("survivors", []):
            key = f"{part}:{item['name']}/{item['target']}"
            if key not in seen:
                fresh.append(key)
    if fresh:
        alert(
            "tradedesk: new mover-study survivor",
            f"{len(fresh)} new rule(s) passed their search, e.g. {fresh[0]}. Research only.",
        )
        state["survivors_notified"] = sorted(seen | set(fresh))
        state.setdefault("failing_keys", [])
        notified_path.parent.mkdir(parents=True, exist_ok=True)
        notified_path.write_text(json.dumps(state, indent=4), encoding="utf-8")
    return fresh


def record_failing(keys: Iterable[str], notified_path: Path = NOTIFIED_PATH) -> None:
    try:
        state = json.loads(notified_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {"survivors_notified": []}
    state["failing_keys"] = sorted(keys)
    notified_path.parent.mkdir(parents=True, exist_ok=True)
    notified_path.write_text(json.dumps(state, indent=4), encoding="utf-8")


def run(path: Path = REPORT_PATH) -> dict[str, Any]:
    previous = None
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    report = check()
    new, cleared = changes(previous, report)
    report["notification_sent"] = notify(new, cleared)
    report["new_survivors"] = watch_survivors()
    record_failing([p["key"] for p in report["failing"]])
    report["new_since_last_check"] = [p["what"] for p in new]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tradedesk.task_health")
    parser.add_argument("command", choices=["check"])
    parser.parse_args(argv)
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
