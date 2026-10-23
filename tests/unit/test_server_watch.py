from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "server_watch", ROOT / "deploy" / "laptop" / "server_watch.py"
)
assert _spec and _spec.loader
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)

NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


def test_a_fresh_health_check_is_fine() -> None:
    fresh = {"checked_at": (NOW - timedelta(minutes=55)).isoformat()}
    assert watch.problem_for(fresh, None, NOW) is None


def test_a_silent_health_check_is_a_problem() -> None:
    stale = {"checked_at": (NOW - timedelta(minutes=130)).isoformat()}
    assert watch.problem_for(stale, None, NOW) == "server health check silent for 130 min"


def test_unreachable_and_unreadable_are_problems() -> None:
    assert watch.problem_for(None, "timed out", NOW).startswith("server unreachable")
    assert "no readable timestamp" in watch.problem_for({}, None, NOW)


def test_one_missed_check_waits_two_in_a_row_alert(tmp_path: Path, monkeypatch) -> None:
    import tradedesk.task_health as th

    sent: list[str] = []
    monkeypatch.setattr(th, "alert", lambda title, body, **kw: sent.append(title))
    monkeypatch.setattr(watch, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(watch, "laptop_online", lambda: True)
    up = {"checked_at": datetime.now(UTC).isoformat()}
    replies: list = []

    def read():
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(watch, "read_server_health", read)
    replies[:] = [TimeoutError("busy"), up, TimeoutError("busy")]
    assert [watch.main() for _ in range(3)] == [0, 0, 0] and sent == []  # misses reset

    replies[:] = [TimeoutError("down"), up]
    assert watch.main() == 1 and sent == ["TRADEDESK SERVER SILENT"]  # second miss in a row
    assert watch.main() == 0 and sent[-1] == "tradedesk server back"
