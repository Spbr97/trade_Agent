"""signal_tracker.py: the shared crypto/BSE "log a call, grade it later" machinery. These
tests cover the two pieces most likely to break silently - the JSONL round trip and the
rule-based failure flag that feeds review_queue.py - not the broker-fetching parts of the
scripts, which need real data and are covered by manual verification (see CLAUDE.md)."""

from __future__ import annotations

from pathlib import Path

from tradedesk.review_queue import load_queue
from tradedesk.signal_tracker import TrackedSignal, flag_setup_failures, load_log, save_log


def _row(i: int, setup: str, outcome: str | None) -> TrackedSignal:
    return TrackedSignal(
        signal_id=f"sig{i}", scrip_code="X", symbol="X", setup=setup, grade="C",
        armed_on="2026-01-01", entry=100.0, stop=95.0, t1=110.0, t2=115.0,
        net_rr_t1=1.0, net_rr_t2=2.0, rejected_for=[], logged_at="2026-01-01",
        outcome=outcome, label=1 if outcome == "target" else (0 if outcome else None),
    )  # fmt: skip


def test_log_round_trips_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = {f"sig{i}": _row(i, "nr7_breakout", None) for i in range(3)}
    save_log(rows, path)
    reloaded = load_log(path)
    assert reloaded.keys() == rows.keys()
    assert reloaded["sig0"].setup == "nr7_breakout"


def test_flag_setup_failures_flags_a_setup_below_the_hit_rate_floor(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {}
    for i in range(6):
        outcome = "stop" if i < 5 else "target"  # 1/6 = 17%, below a 30% floor
        rows[f"sig{i}"] = _row(i, "nr7_breakout", outcome)

    flagged = flag_setup_failures(
        "crypto", rows, min_resolved=5, hit_rate_floor=0.3, review_path=review_path
    )

    assert flagged == ["nr7_breakout underperforming on crypto"]
    items = load_queue(review_path)
    assert len(items) == 1
    item = next(iter(items.values()))
    assert item.market == "crypto"
    assert item.status == "pending"


def test_flag_setup_failures_does_not_flag_below_the_minimum_sample(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _row(i, "nr7_breakout", "stop") for i in range(3)}  # 0% but only 3 resolved

    flagged = flag_setup_failures(
        "crypto", rows, min_resolved=5, hit_rate_floor=0.3, review_path=review_path
    )

    assert flagged == []
    assert load_queue(review_path) == {}


def test_flag_setup_failures_does_not_duplicate_an_existing_flag(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _row(i, "nr7_breakout", "stop") for i in range(5)}

    first = flag_setup_failures("crypto", rows, min_resolved=5, review_path=review_path)
    second = flag_setup_failures("crypto", rows, min_resolved=5, review_path=review_path)

    assert first == ["nr7_breakout underperforming on crypto"]
    assert second == []  # already flagged - no duplicate item on a re-run
    assert len(load_queue(review_path)) == 1


def test_flag_setup_failures_leaves_a_healthy_setup_alone(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _row(i, "base_breakout", "target" if i < 4 else "stop") for i in range(5)}

    flagged = flag_setup_failures("crypto", rows, min_resolved=5, review_path=review_path)

    assert flagged == []
    assert load_queue(review_path) == {}
