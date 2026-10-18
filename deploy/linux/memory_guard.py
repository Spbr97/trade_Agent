"""Server memory fail-safe: a phone push when memory runs low, the moment any job is stopped for
lack of memory, and when memory is back to normal. Runs every minute (memory-guard.timer).

The enforcement is systemd's, not this script's: every batch job runs in tradedesk-jobs.slice,
whose combined memory is capped below the machine's RAM (deploy/linux/render_units.py), so a
runaway job is stopped on its own while the dashboard, tunnel, billing guard and ssh keep room,
and a swap file (deploy/linux/install.sh) cushions short peaks. This script watches and tells.

Usage: python deploy/linux/memory_guard.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "reports" / "memory_guard.json"
CGROUP = Path("/sys/fs/cgroup")
LOW_AVAILABLE = 0.20  # share of RAM still available
CRITICAL_AVAILABLE = 0.08
CLEAR_AVAILABLE = 0.25  # back to normal only with a margin, so a level near 20% cannot flap
CRITICAL_SWAP_SHARE = 0.75  # swap nearly full while RAM is short
REMIND = timedelta(hours=3)
LEVELS = ("ok", "low", "critical")
GB = 1024**3


def meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo -> bytes per field."""

    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[key] = int(parts[0]) * (1024 if parts[1:] == ["kB"] else 1)
    return fields


def oom_kills(vmstat: str) -> int:
    """Processes the kernel has killed for lack of memory since boot (/proc/vmstat)."""

    for line in vmstat.splitlines():
        name, _, value = line.partition(" ")
        if name == "oom_kill":
            return int(value)
    return 0


def level(mem: Mapping[str, int], previous: str = "ok") -> str:
    available = mem.get("MemAvailable", 0) / (mem.get("MemTotal") or 1)
    swap_total = mem.get("SwapTotal", 0)
    swap_share = (swap_total - mem.get("SwapFree", 0)) / swap_total if swap_total else 0.0
    if available < CRITICAL_AVAILABLE or (
        available < LOW_AVAILABLE and swap_share >= CRITICAL_SWAP_SHARE
    ):
        return "critical"
    if available < LOW_AVAILABLE or (previous != "ok" and available < CLEAR_AVAILABLE):
        return "low"
    return "ok"


def decide(
    state: Mapping[str, Any], *, now: datetime, current: str, kills: int, boot_id: str
) -> tuple[list[str], dict[str, Any]]:
    """Pushes due now - "oom" (a new kill), "level" (worse, or a reminder), "clear" - and the
    state to keep if they are delivered."""

    if state.get("boot_id") != boot_id:  # the kernel's counter restarts at boot
        state = {"boot_id": boot_id, "oom_kills": 0, "level": "ok"}
    events = []
    if kills > int(state.get("oom_kills", 0)):
        events.append("oom")
    previous = str(state.get("level", "ok"))
    alerted = state.get("alerted_at")
    if current != "ok":
        worse = LEVELS.index(current) > LEVELS.index(previous)
        due = alerted is None or now - datetime.fromisoformat(alerted) >= REMIND
        if worse or due:
            events.append("level")
    elif previous != "ok":
        events.append("clear")
    new = {**state, "boot_id": boot_id, "oom_kills": kills, "level": current}
    new["checked_at"] = now.isoformat()
    if "level" in events:
        new["alerted_at"] = now.isoformat()
    if current == "ok":
        new.pop("alerted_at", None)
    return events, new


def biggest(root: Path = CGROUP, top: int = 3) -> list[tuple[str, int]]:
    """Services by private memory. Page cache is left out: the kernel frees it first."""

    sizes = []
    for pattern in ("system.slice/*.service", "*.slice/*.slice/*.service"):
        for unit in root.glob(pattern):
            try:
                stat = (unit / "memory.stat").read_text(encoding="utf-8")
            except OSError:
                continue
            anon = next(
                (int(ln.split()[1]) for ln in stat.splitlines() if ln.startswith("anon ")), 0
            )
            sizes.append((unit.name.removesuffix(".service"), anon))
    return sorted(sizes, key=lambda item: -item[1])[:top]


def oom_units() -> list[str]:
    """Services whose last run the out-of-memory killer ended (systemd Result=oom-kill)."""

    def systemctl(*args: str) -> str:
        return subprocess.run(
            ["systemctl", *args], capture_output=True, text=True, timeout=30, check=False
        ).stdout

    names = [
        line.split()[0]
        for line in systemctl(
            "list-units", "--type=service", "--all", "--no-legend", "--plain"
        ).splitlines()
        if line.strip()
    ]
    if not names:
        return []
    blocks = systemctl("show", "-p", "Id", "-p", "Result", *names).strip().split("\n\n")
    hit = []
    for block in blocks:
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Result") == "oom-kill":
            hit.append(props.get("Id", "?").removesuffix(".service"))
    return hit


def _free(mem: Mapping[str, int]) -> str:
    available, total = mem.get("MemAvailable", 0) / GB, mem.get("MemTotal", 0) / GB
    swap_total = mem.get("SwapTotal", 0)
    swap = (
        f", swap {(swap_total - mem.get('SwapFree', 0)) / GB:.1f} of {swap_total / GB:.1f} GB used"
        if swap_total
        else ""
    )
    return f"{available:.1f} GB of {total:.1f} GB free{swap}"


def messages(
    events: list[str],
    *,
    current: str,
    mem: Mapping[str, int],
    top: Callable[[], list[tuple[str, int]]] = biggest,
    killed: Callable[[], list[str]] = oom_units,
) -> list[tuple[str, str, bool]]:
    """(title, body, urgent) per push."""

    out = []
    if "oom" in events:
        names = ", ".join(killed()) or "a process (see journalctl -k)"
        out.append(
            (
                "Server: job stopped - out of memory",
                f"{names} ran out of memory and was stopped; the rest keeps running. A scheduled "
                f"job runs again at its next time. Now {_free(mem)}.",
                True,
            )
        )
    if "level" in events:
        users = ", ".join(f"{name} {size / GB:.1f} GB" for name, size in top() if size)
        out.append(
            (
                f"Server memory {current.upper()}",
                f"{_free(mem)}. Biggest: {users or 'n/a'}. Jobs share a capped budget, so the "
                "dashboard, alerts and billing guard keep room; a job over it is stopped alone.",
                current == "critical",
            )
        )
    if "clear" in events:
        out.append(("Server memory back to normal", f"{_free(mem)}.", False))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print, do not push or save")
    args = parser.parse_args(argv)

    mem = meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    kills = oom_kills(Path("/proc/vmstat").read_text(encoding="utf-8"))
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    now = datetime.now().astimezone()
    current = level(mem, str(state.get("level", "ok")) if state.get("boot_id") == boot_id else "ok")
    events, new_state = decide(state, now=now, current=current, kills=kills, boot_id=boot_id)
    pushes = messages(events, current=current, mem=mem)
    if args.dry_run:
        print(f"{current}: {_free(mem)}; oom kills since boot {kills}; events {events}")
        for title, body, urgent in pushes:
            print(f"{'URGENT ' if urgent else ''}{title}: {body}")
        return 0
    delivered = True
    if pushes:
        from tradedesk.task_health import alert

        for title, body, urgent in pushes:
            ok = alert(title, body[:400], urgent=urgent)
            delivered = delivered and ok
            print(f"{'sent' if ok else 'NOT SENT'}: {title}: {body}")
    if delivered:  # an undelivered push is retried next minute
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
