"""task_health.py: real failures are reported once, harmless task codes never are."""

from __future__ import annotations

import json
from datetime import datetime

from tradedesk.broker.indstocks.models import IST
from tradedesk.task_health import changes, check, pipeline_problems, task_problems

NOW = datetime(2026, 10, 14, 20, 0, tzinfo=IST)


def _task(name: str, result: int, state: str = "Ready") -> dict:
    return {"name": name, "state": state, "last_run": "2026-10-14T16:00:00", "result": result}


def test_only_real_task_failures_are_problems() -> None:
    tasks = [
        _task("tradedesk-ok", 0),
        _task("tradedesk-running", 267009),
        _task("tradedesk-never-run", 267011),
        _task("tradedesk-already-running", 2147946720),
        _task("tradedesk-off", 1, state="Disabled"),
        _task("tradedesk-after-close", 1),
    ]
    assert [p["what"] for p in task_problems(tasks)] == ["tradedesk-after-close"]


def test_failed_or_stale_pipelines_are_problems(tmp_path) -> None:
    (tmp_path / "nse.json").write_text(
        json.dumps({"status": "failed", "failed_stage": "signal_and_m8_m11",
                    "finished_at": "2026-10-14T16:34:00+05:30"}), "utf-8")  # fmt: skip
    (tmp_path / "bse.json").write_text(
        json.dumps({"status": "completed", "finished_at": "2026-10-14T16:53:00+05:30"}), "utf-8"
    )
    (tmp_path / "crypto.json").write_text(
        json.dumps({"status": "completed", "finished_at": "2026-10-13T19:00:00+05:30"}), "utf-8"
    )
    problems = {p["key"]: p["detail"] for p in pipeline_problems(NOW, tmp_path)}
    assert problems["pipeline:nse"] == "failed at signal_and_m8_m11"
    assert "pipeline-stale:crypto" in problems  # older than its 14-hour window
    assert not any(k.endswith(":bse") for k in problems)


def test_each_failure_is_announced_once_and_its_clearing_too(tmp_path) -> None:
    first = check(NOW, tasks=[_task("tradedesk-after-close", 1)], pipeline_root=tmp_path)
    new, cleared = changes(None, first)
    assert [p["what"] for p in new] == ["tradedesk-after-close"] and cleared == []
    again = check(NOW, tasks=[_task("tradedesk-after-close", 1)], pipeline_root=tmp_path)
    assert changes(first, again) == ([], [])
    fixed = check(NOW, tasks=[_task("tradedesk-after-close", 0)], pipeline_root=tmp_path)
    new, cleared = changes(again, fixed)
    assert new == [] and [p["what"] for p in cleared] == ["tradedesk-after-close"]
    assert fixed["status"] == "ok"


def test_new_survivors_are_announced_once(tmp_path) -> None:
    from tradedesk import task_health

    lab = tmp_path / "lab"
    (lab / "intraday_mover_study" / "nse").mkdir(parents=True)
    (lab / "intraday_mover_study" / "nse" / "search_latest.json").write_text(
        json.dumps({"survivors": [{"name": "opening_bar:x", "target": "short_rest_of_day"}]}),
        "utf-8",
    )
    state = tmp_path / "notified.json"
    sent: list[str] = []
    original = task_health.toast
    task_health.toast = lambda title, body, urgent=False: sent.append(body) or True  # type: ignore[assignment]
    try:
        first = task_health.watch_survivors(state, lab)
        second = task_health.watch_survivors(state, lab)
    finally:
        task_health.toast = original  # type: ignore[assignment]
    assert first == ["intraday_mover_study/nse:opening_bar:x/short_rest_of_day"]
    assert second == [] and len(sent) == 1
