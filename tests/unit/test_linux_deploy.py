from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tradedesk import task_health

ROOT = Path(__file__).resolve().parents[2]
_RENDER = ROOT / "deploy" / "linux" / "render_units.py"
_spec = importlib.util.spec_from_file_location("render_units", _RENDER)
assert _spec and _spec.loader
render_units = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_units)


def _trigger(**kw):
    base = {"kind": "Daily", "at": "07:30", "days": [], "every_days": 1,
            "repeat_minutes": None, "repeat_for_minutes": None, "enabled": True}  # fmt: skip
    return base | kw


def test_weekly_trigger_keeps_its_weekdays_and_time() -> None:
    trig = _trigger(kind="Weekly", at="16:05", days=["Mon", "Tue", "Wed", "Thu", "Fri"])
    assert render_units.on_calendar(trig) == ["Mon,Tue,Wed,Thu,Fri *-*-* 16:05:00 Asia/Kolkata"]


def test_bounded_repetition_stops_at_the_window_end_inclusive() -> None:
    lines = render_units.on_calendar(_trigger(repeat_minutes=15, repeat_for_minutes=840))
    assert lines[0] == "*-*-* 07:30,45:00 Asia/Kolkata"
    assert lines[-1] == "*-*-* 21:00,15,30:00 Asia/Kolkata"


def test_ten_year_repetition_means_every_slot_of_every_day() -> None:
    times = render_units.trigger_times(
        _trigger(kind="Time", at="15:32", repeat_minutes=15, repeat_for_minutes=5256000)
    )
    assert len(times) == 96 and times[0] == 2 and 15 * 60 + 32 in times


def test_every_timer_catches_up_missed_runs_and_every_job_is_ported() -> None:
    schedule = json.loads((ROOT / "deploy/schedule.json").read_text(encoding="utf-8"))
    files = render_units.render(schedule, root="/opt/tradedesk", user="tradedesk")
    timers = {name: text for name, text in files.items() if name.endswith(".timer")}

    enabled = [j for j in schedule["jobs"] if j["enabled"]]
    assert len(timers) == len(enabled) - 2  # dashboard + tunnel are always-on services
    assert all("Persistent=true" in text and "OnCalendar=" in text for text in timers.values())
    assert "Restart=always" in files["tradedesk-dashboard.service"]


def test_unsupported_trigger_fails_loudly() -> None:
    with pytest.raises(ValueError):
        render_units.on_calendar(_trigger(kind="Logon", at=None))


def test_systemd_status_maps_to_the_same_rows_as_windows() -> None:
    show = "\n\n".join(
        [
            "Id=tradedesk-a.service\nResult=success\nExecMainStatus=0\nActiveState=inactive\n"
            "ExecMainStartTimestamp=Mon 2026-10-19 16:05:00 IST",
            "Id=tradedesk-a.timer\nUnitFileState=enabled",
            "Id=tradedesk-b.service\nResult=exit-code\nExecMainStatus=1\nActiveState=failed",
            "Id=tradedesk-b.timer\nUnitFileState=enabled",
            "Id=tradedesk-c.service\nResult=timeout\nExecMainStatus=0\nActiveState=failed",
            "Id=tradedesk-c.timer\nUnitFileState=disabled",
            "Id=tradedesk-d.service\nResult=success\nExecMainStatus=0\nActiveState=activating",
        ]
    )
    rows = {r["name"]: r for r in task_health.parse_systemctl_show(show)}

    assert rows["tradedesk-a"]["result"] == 0
    assert rows["tradedesk-b"]["result"] == 1
    assert rows["tradedesk-c"]["state"] == "Disabled"
    assert rows["tradedesk-d"]["result"] == task_health.RUNNING
    problems = {p["what"] for p in task_health.task_problems(rows.values())}
    assert problems == {"tradedesk-b"}


def test_every_unit_reads_the_root_only_secrets_file() -> None:
    schedule = json.loads((ROOT / "deploy/schedule.json").read_text(encoding="utf-8"))
    files = render_units.render(schedule, root="/opt/tradedesk", user="tradedesk")
    services = [text for name, text in files.items() if name.endswith(".service")]
    assert services and all("EnvironmentFile=-/etc/tradedesk/env" in t for t in services)
