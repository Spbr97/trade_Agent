"""The server memory fail-safe: every batch job shares one capped memory budget, and the guard
pushes once when memory runs low, at once when a job is killed for lack of memory, and once when
memory is back - never every minute."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / "linux" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load("memory_guard")
render_units = _load("render_units")
GIB = 1024**3
T0 = datetime(2026, 10, 19, 16, 0).astimezone()


def _mem(available_share: float, swap_used_share: float = 0.0) -> dict[str, int]:
    total, swap = 12 * GIB, 4 * GIB
    return {
        "MemTotal": total, "MemAvailable": int(total * available_share),
        "SwapTotal": swap, "SwapFree": int(swap * (1 - swap_used_share)),
    }  # fmt: skip


def test_proc_files_parse_to_bytes_and_kill_counts() -> None:
    mem = guard.meminfo(
        "MemTotal:       12213436 kB\nMemAvailable:   11440128 kB\nHugePages_Total:       0\n"
    )
    assert mem["MemTotal"] == 12213436 * 1024 and mem["HugePages_Total"] == 0
    assert guard.oom_kills("pgfault 12\noom_kill 3\nnr_free_pages 9\n") == 3


def test_levels_and_a_margin_before_all_clear() -> None:
    assert guard.level(_mem(0.60)) == "ok"
    assert guard.level(_mem(0.15)) == "low"
    assert guard.level(_mem(0.05)) == "critical"
    assert guard.level(_mem(0.15, swap_used_share=0.8)) == "critical"  # swap nearly full too
    assert guard.level(_mem(0.60, swap_used_share=0.8)) == "ok"  # old swapped pages, RAM free
    assert guard.level(_mem(0.22), previous="low") == "low"  # no flapping around 20%
    assert guard.level(_mem(0.30), previous="critical") == "ok"


def test_one_push_per_change_with_a_reminder_and_an_all_clear() -> None:
    def step(state, minutes, current, kills=0):
        return guard.decide(
            state, now=T0 + timedelta(minutes=minutes), current=current, kills=kills, boot_id="b1"
        )

    events, state = step({}, 0, "ok")
    assert events == []
    events, state = step(state, 1, "low")
    assert events == ["level"]
    events, state = step(state, 2, "low")
    assert events == []  # not every minute
    events, state = step(state, 3, "critical")
    assert events == ["level"]  # worse: at once
    events, state = step(state, 4, "low")
    assert events == []  # better but not back
    events, state = step(state, 3 + 180, "low")
    assert events == ["level"]  # a reminder after 3 hours
    events, state = step(state, 200, "ok")
    assert events == ["clear"]
    assert "alerted_at" not in state


def test_a_kill_pushes_at_once_and_a_reboot_resets_the_count() -> None:
    events, state = guard.decide({}, now=T0, current="ok", kills=2, boot_id="b1")
    assert events == ["oom"]
    events, state = guard.decide(state, now=T0, current="ok", kills=2, boot_id="b1")
    assert events == []
    events, state = guard.decide(state, now=T0, current="ok", kills=0, boot_id="b2")
    assert events == [] and state["oom_kills"] == 0


def test_messages_name_the_killed_job_and_the_biggest_users() -> None:
    pushes = guard.messages(
        ["oom", "level"], current="critical", mem=_mem(0.05, 0.5),
        top=lambda: [("tradedesk-after-close", 6 * GIB), ("tradedesk-dashboard", GIB // 10)],
        killed=lambda: ["tradedesk-after-close"],
    )  # fmt: skip
    (oom_title, oom_body, oom_urgent), (title, body, urgent) = pushes
    assert oom_urgent and "tradedesk-after-close ran out of memory" in oom_body
    assert title == "Server memory CRITICAL" and urgent
    assert "0.6 GB of 12.0 GB free, swap 2.0 of 4.0 GB used" in body
    assert "tradedesk-after-close 6.0 GB" in body
    assert all(len(b) <= 400 for _, b, _ in pushes)


def test_biggest_reads_private_memory_from_the_cgroup_tree(tmp_path: Path) -> None:
    for path, anon in (
        ("system.slice/tradedesk-dashboard.service", 90 * 1024**2),
        ("tradedesk.slice/tradedesk-jobs.slice/tradedesk-after-close.service", 3 * GIB),
    ):
        unit = tmp_path / path
        unit.mkdir(parents=True)
        (unit / "memory.stat").write_text(f"anon {anon}\nfile 999999999999\n", encoding="utf-8")
    assert guard.biggest(tmp_path) == [
        ("tradedesk-after-close", 3 * GIB), ("tradedesk-dashboard", 90 * 1024**2)
    ]  # fmt: skip


def test_dry_run_reports_without_pushing_or_saving(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "STATE", tmp_path / "memory_guard.json")
    real = Path.read_text

    def fake(self: Path, *args, **kwargs) -> str:
        fixtures = {
            "/proc/meminfo": "MemTotal: 12000000 kB\nMemAvailable: 1000000 kB\n",
            "/proc/vmstat": "oom_kill 0\n",
            "/proc/sys/kernel/random/boot_id": "b1\n",
        }
        return fixtures.get(self.as_posix()) or real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake)
    monkeypatch.setattr(guard, "biggest", lambda *a, **k: [])
    assert guard.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("low: 1.0 GB of 11.4 GB free") and "Server memory LOW" in out
    assert not (tmp_path / "memory_guard.json").exists()


def test_every_batch_job_runs_inside_the_capped_slice() -> None:
    schedule = json.loads((ROOT / "deploy/schedule.json").read_text(encoding="utf-8"))
    files = render_units.render(schedule, root="/opt/tradedesk", user="tradedesk")
    slice_text = files["tradedesk-jobs.slice"]
    assert "MemoryMax=85%" in slice_text and "MemoryHigh=75%" in slice_text
    jobs = [t for n, t in files.items() if n.endswith(".service") and "Type=oneshot" in t]
    assert jobs and all("Slice=tradedesk-jobs.slice" in t for t in jobs)
    for always_on in ("tradedesk-dashboard.service", "tradedesk-cloudflare-tunnel.service"):
        assert "Slice=" not in files[always_on]  # outside the job budget: always keeps room
    for name in ("oracle-billing-guard.service", "memory-guard.service"):
        assert "OOMScoreAdjust=-900" in (ROOT / "deploy" / "linux" / name).read_text("utf-8")
