"""Make silent failures loud.

The NSE after-close pipeline failed its critical step every day from 6 to 14 Oct 2026 and
nothing said so. This check, run hourly by its own scheduled task, reads every
`tradedesk-*` Windows task's last result and the accuracy-collection pipeline status files,
writes data/reports/health.json (the dashboard shows a banner only when something is
failing) and, when Telegram is configured, sends each NEW failure once - plus one message
when it clears. Read-only towards everything it checks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
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


def scheduled_tasks() -> list[dict[str, Any]]:
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


def notify(new: list[dict[str, Any]], cleared: list[dict[str, Any]]) -> bool:
    """Telegram only if it is enabled in config/alerts.yaml and a bot token is stored."""

    if not new and not cleared:
        return False
    try:
        import asyncio

        from tradedesk.alerts.cards import OutboundMessage
        from tradedesk.alerts.telegram import TelegramBot, load_bot_token
        from tradedesk.config import load_config

        settings = load_config(Path("."))
        telegram = settings.alerts.telegram
        token = load_bot_token()
        if not (telegram.enabled and telegram.allowed_chat_id and token):
            return False
        lines = [f"FAILING  {p['what']}: {p['detail']}" for p in new]
        lines += [f"CLEARED  {p['what']}" for p in cleared]
        message = OutboundMessage(title="tradedesk health", body="\n".join(lines)[:3500])
        return asyncio.run(TelegramBot(token, int(telegram.allowed_chat_id)).send(message))
    except Exception as exc:  # noqa: BLE001 - a health alert must never crash the check
        print(f"health notification not sent: {exc!r}")
        return False


def run(path: Path = REPORT_PATH) -> dict[str, Any]:
    previous = None
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    report = check()
    new, cleared = changes(previous, report)
    report["telegram_sent"] = notify(new, cleared)
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
