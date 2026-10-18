"""From the laptop, make sure the server is still alive: a dead server sends no alerts, and
silence must never pass for "all fine".

Every run (scheduled every 10 minutes while the laptop is on) reads the server's
health.json over SSH. It alerts (desktop + ntfy) when the server cannot be reached, or when
its hourly health check has not written for STALE_MINUTES. It stays quiet when the laptop
itself is offline (ntfy.sh unreachable), because then the problem is here, not there. A
problem re-alerts every REMIND_MINUTES and sends one "back" message when it clears.

    python deploy/laptop/server_watch.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HOST = "ubuntu@140.245.20.98"
KEY = Path.home() / ".ssh" / "oracle_tradedesk"
HEALTH = "/opt/tradedesk/data/reports/health.json"
STATE = ROOT / "data" / "reports" / "server_watch.json"
STALE_MINUTES = 90
REMIND_MINUTES = 60
SSH_CANDIDATES = (r"C:\Program Files\Git\usr\bin\ssh.exe", r"C:\Windows\System32\OpenSSH\ssh.exe")


def _ssh() -> str:
    for candidate in SSH_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("ssh") or "ssh"


def read_server_health() -> dict[str, Any]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash under pythonw
    out = subprocess.run(
        [_ssh(), "-i", str(KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         HOST, f"cat {HEALTH}"],
        capture_output=True, text=True, timeout=60, creationflags=flags,
    )  # fmt: skip
    if out.returncode != 0:
        raise ConnectionError(out.stderr.strip()[:200] or f"ssh exit {out.returncode}")
    return json.loads(out.stdout)


def laptop_online() -> bool:
    try:
        import httpx

        return httpx.get("https://ntfy.sh/v1/health", timeout=10).status_code == 200
    except Exception:  # noqa: BLE001 - any failure means we cannot judge the server
        return False


def problem_for(health: dict[str, Any] | None, error: str | None, now: datetime) -> str | None:
    if error is not None:
        return f"server unreachable over SSH: {error}"
    stamp = (health or {}).get("checked_at") or (health or {}).get("generated_at")
    try:
        checked = datetime.fromisoformat(str(stamp))
    except ValueError:
        return "server health.json has no readable timestamp"
    age = now - checked
    if age > timedelta(minutes=STALE_MINUTES):
        return f"server health check silent for {int(age.total_seconds() // 60)} min"
    return None


def main() -> int:
    from tradedesk.task_health import alert

    now = datetime.now(UTC)
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not laptop_online():
        print(f"{now.isoformat(timespec='seconds')} laptop offline: no judgement")
        return 0
    health, error = None, None
    try:
        health = read_server_health()
    except Exception as exc:  # noqa: BLE001 - every failure is reported, not raised
        error = repr(exc)[:200]
    problem = problem_for(health, error, now)
    last = state.get("last_alert_at")
    due = problem and (
        problem.split(":")[0] != state.get("problem", "").split(":")[0]
        or not last
        or now - datetime.fromisoformat(last) >= timedelta(minutes=REMIND_MINUTES)
    )
    if due:
        alert("TRADEDESK SERVER SILENT", f"{problem}\nNo server alerts can reach you.", urgent=True)
        state.update(problem=problem, last_alert_at=now.isoformat())
    elif not problem and state.get("problem"):
        alert("tradedesk server back", "Health check is reporting again.")
        state = {}
    state["checked_at"] = now.isoformat()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")
    print(f"{now.isoformat(timespec='seconds')} {problem or 'server OK'}")
    return 1 if problem else 0


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
