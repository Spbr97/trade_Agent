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
