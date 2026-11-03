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


_CONTRACTS = ROOT / "deploy" / "linux" / "check_live_contracts.py"
_cspec = importlib.util.spec_from_file_location("check_live_contracts", _CONTRACTS)
assert _cspec and _cspec.loader
check_live_contracts = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(check_live_contracts)


def _cohort(root: Path, frozen: dict[str, str]) -> None:
    state = root / "data" / "m14_m18" / "forward" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"activation": {"contract_sha256": frozen}}), encoding="utf-8")


def test_a_deploy_that_changes_a_frozen_file_fails_before_any_evidence_is_lost(
    tmp_path: Path,
) -> None:
    # 2026-10-31: one wrapped line in tradedesk_lab/portable.py broke lab_forward for two days.
    _cohort(tmp_path, {"tradedesk_lab/portable.py": "869e7d77" + "0" * 56, "b.py": "b" * 64})
    bad = check_live_contracts.check(
        tmp_path, lambda: {"tradedesk_lab/portable.py": "8d7973da" + "0" * 56, "b.py": "b" * 64}
    )
    assert list(bad) == ["tradedesk_lab/portable.py"]
    assert "869e7d77" in bad["tradedesk_lab/portable.py"]
    assert check_live_contracts.check(tmp_path, lambda: {"b.py": "b" * 64}) == {
        "tradedesk_lab/portable.py": "missing from the deployed code"
    }


def test_matching_code_or_no_cohort_yet_passes(tmp_path: Path) -> None:
    assert check_live_contracts.check(tmp_path, lambda: {"a.py": "a" * 64}) == {}
    _cohort(tmp_path, {"a.py": "a" * 64})
    assert check_live_contracts.check(tmp_path, lambda: {"a.py": "a" * 64}) == {}
