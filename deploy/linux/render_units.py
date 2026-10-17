"""deploy/schedule.json -> systemd .service/.timer files for a Linux server.

Every Windows trigger becomes OnCalendar lines in Asia/Kolkata, and every timer is
`Persistent=true`: a run missed while the server was down fires once as soon as it is back,
the systemd equivalent of Task Scheduler's StartWhenAvailable. Batch jobs are oneshot
services run through scripts/run_hidden.py exactly as on Windows (same log files, same exit
codes, same DuckDB lock wait). The dashboard and the Cloudflare tunnel become always-on
services that restart on failure.

Usage: python deploy/linux/render_units.py --root /opt/tradedesk --user tradedesk --out DIR
Nothing is installed by this script; deploy/linux/install.sh copies DIR into systemd.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TZ = "Asia/Kolkata"
# Root-only secrets for a server with no keyring (e.g. TRADEDESK_NTFY_TOPIC). systemd reads it as
# root before dropping to the service user; "-" means a missing file is not an error.
ENV_FILE = "/etc/tradedesk/env"
SCHEDULE = Path(__file__).resolve().parents[1] / "schedule.json"
DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def trigger_times(trigger: Mapping[str, Any]) -> list[int]:
    """Minutes after midnight at which one trigger fires on a day it applies to."""

    start = _minutes(trigger["at"])
    every = trigger.get("repeat_minutes")
    if not every:
        return [start]
    span = trigger.get("repeat_for_minutes")
    if not span or span >= 1440:
        # Windows "indefinitely" (or P3650D): every slot of every day, aligned to the start
        return sorted({(start + k * every) % 1440 for k in range(1440 // every + 1)})
    return [start + k * every for k in range(span // every + 1) if start + k * every < 1440]


def on_calendar(trigger: Mapping[str, Any]) -> list[str]:
    """OnCalendar= values for one trigger, grouped one line per hour."""

    kind = trigger["kind"]
    if kind == "Weekly":
        days = ",".join(d for d in DAY_NAMES if d in trigger["days"])
    elif kind in {"Daily", "Time"}:
        if kind == "Daily" and (trigger.get("every_days") or 1) != 1:
            raise ValueError(f"every {trigger['every_days']} days is not supported")
        days = ""
    else:
        raise ValueError(f"trigger kind {kind!r} has no timer equivalent")
    by_hour: dict[int, list[int]] = {}
    for minute in trigger_times(trigger):
        by_hour.setdefault(minute // 60, []).append(minute % 60)
    prefix = f"{days} " if days else ""
    return [
        f"{prefix}*-*-* {hour:02d}:{','.join(f'{m:02d}' for m in sorted(minutes))}:00 {TZ}"
        for hour, minutes in sorted(by_hour.items())
    ]


# Phone digests (scripts/send_digest.py), sent when the named job ends. ExecStopPost with a
# leading "-" can never change the job's own result; the sender skips non-close digests when
# the job did not succeed (the hourly health check already reports that failure).
DIGEST_HOOKS: dict[str, list[tuple[str, str]]] = {
    "tradedesk-after-close": [("close", "nse")],
    "tradedesk-bse-after-close": [("close", "bse")],
    "tradedesk-crypto-eod-learning": [("close", "crypto"), ("eod", "crypto")],
    "tradedesk-research-tracker": [("eod", "nse")],
    "tradedesk-bse-research-tracker": [("eod", "bse")],
    "tradedesk-intraday-study-nightly": [("intraday", "nse")],
    "tradedesk-intraday-study-bse-nightly": [("intraday", "bse")],
    "tradedesk-intraday-study-crypto-nightly": [("intraday", "crypto")],
    "tradedesk-multiday-hold-study": [("multiday", "")],
}


def digest_lines(job_name: str, *, root: str) -> list[str]:
    lines = []
    for kind, market in DIGEST_HOOKS.get(job_name, []):
        market_arg = f" --market {market}" if market else ""
        lines.append(
            f"ExecStopPost=-{root}/.venv/bin/python {root}/scripts/send_digest.py {kind}"
            f"{market_arg} --job {job_name} --result $SERVICE_RESULT --exit $EXIT_STATUS"
        )
    return lines


def _quote(arg: str) -> str:
    return f'"{arg}"' if any(ch in arg for ch in ' "\\') else arg


def service_unit(job: Mapping[str, Any], *, root: str, user: str) -> str:
    limit = job.get("time_limit_minutes") or 0
    command = " ".join(_quote(a) for a in job["command"])
    return "\n".join(
        [
            "[Unit]",
            f"Description=tradedesk job {job['name']} (ported from Windows Task Scheduler)",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            f"User={user}",
            f"WorkingDirectory={root}",
            f"Environment=TZ={TZ}",
            "Environment=PYTHONUNBUFFERED=1",
            f"EnvironmentFile=-{ENV_FILE}",
            f"ExecStart={root}/.venv/bin/python {root}/scripts/run_hidden.py {command}",
            *digest_lines(job["name"], root=root),
            f"TimeoutStartSec={f'{limit}min' if limit else 'infinity'}",
            "Nice=5",
            "",
        ]
    )


def timer_unit(job: Mapping[str, Any]) -> str:
    lines = [
        "[Unit]",
        f"Description=schedule for {job['name']}",
        "",
        "[Timer]",
    ]
    for trigger in job["triggers"]:
        if trigger.get("enabled", True):
            lines += [f"OnCalendar={value}" for value in on_calendar(trigger)]
    lines += [
        "Persistent=true",
        "AccuracySec=1s",
        f"Unit={job['name']}.service",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ]
    return "\n".join(lines)


def always_on_units(*, root: str, user: str) -> dict[str, str]:
    """The two Windows jobs that are not run_hidden batch jobs."""

    def unit(name: str, description: str, exec_start: str, after: str = "") -> str:
        return "\n".join(
            [
                "[Unit]",
                f"Description={description}",
                f"After=network-online.target{after}",
                "Wants=network-online.target",
                "",
                "[Service]",
                f"User={user}",
                f"WorkingDirectory={root}",
                f"Environment=TZ={TZ}",
                f"EnvironmentFile=-{ENV_FILE}",
                f"ExecStart={exec_start}",
                "Restart=always",
                "RestartSec=15",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
                "",
            ]
        )

    return {
        "tradedesk-dashboard.service": unit(
            "tradedesk-dashboard", "tradedesk dashboard on 127.0.0.1:8765",
            f"{root}/.venv/bin/tradedesk dashboard",
        ),
        "tradedesk-cloudflare-tunnel.service": unit(
            "tradedesk-cloudflare-tunnel", "Cloudflare quick tunnel to the dashboard",
            f"/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765 "
            f"--logfile {root}/data/logs/cloudflare_tunnel.log",
            " tradedesk-dashboard.service",
        ),
    }  # fmt: skip


def render(schedule: Mapping[str, Any], *, root: str, user: str) -> dict[str, str]:
    files = always_on_units(root=root, user=user)
    for job in schedule["jobs"]:
        if not job.get("enabled", True) or job["name"] in {
            "tradedesk-dashboard", "tradedesk-cloudflare-tunnel"
        }:  # fmt: skip
            continue
        if not job.get("command"):
            raise ValueError(f"{job['name']} is not a run_hidden job; port it by hand")
        files[f"{job['name']}.service"] = service_unit(job, root=root, user=user)
        files[f"{job['name']}.timer"] = timer_unit(job)
    return files


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="/opt/tradedesk")
    parser.add_argument("--user", default="tradedesk")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, default=SCHEDULE)
    args = parser.parse_args(argv)
    files = render(json.loads(args.schedule.read_text(encoding="utf-8")),
                   root=args.root, user=args.user)  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.out / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(files)} unit files to {args.out}")


if __name__ == "__main__":
    main()
