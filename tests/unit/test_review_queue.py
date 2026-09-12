"""review_queue.py: the approve/reject bookkeeping log behind the dashboard's Review tab."""

from __future__ import annotations

import datetime as real_datetime
from pathlib import Path

import pytest

from tradedesk import review_queue as rq


def test_add_then_decide_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "queue.jsonl"
    item = rq.add_item("nse", "title", "detail", "proposal", path=path)
    assert item.status == "pending"

    decided = rq.decide(item.id, "approved", path=path)
    assert decided is not None
    assert decided.status == "approved"
    assert decided.decided_at is not None

    reloaded = rq.load_queue(path)
    assert reloaded[item.id].status == "approved"


def test_decide_unknown_id_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "queue.jsonl"
    assert rq.decide("nope", "approved", path=path) is None


def test_two_items_added_within_the_same_clock_tick_do_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real bug found 2026-09-12: flag_setup_failures() flagging multiple setups in one
    loop called add_item() back-to-back fast enough that datetime.now() returned the same
    value twice, and the timestamp-only id silently let the second overwrite the first."""
    path = tmp_path / "queue.jsonl"

    class _FrozenDatetime:
        @staticmethod
        def now(tz: object = None) -> real_datetime.datetime:
            return real_datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=real_datetime.UTC)

    monkeypatch.setattr(rq, "datetime", _FrozenDatetime)

    rq.add_item("crypto", "first setup underperforming", "d1", "p1", path=path)
    rq.add_item("crypto", "second setup underperforming", "d2", "p2", path=path)

    items = rq.load_queue(path)
    assert len(items) == 2
    titles = {i.title for i in items.values()}
    assert titles == {"first setup underperforming", "second setup underperforming"}
