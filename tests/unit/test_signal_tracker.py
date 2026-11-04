"""signal_tracker.py: the shared crypto/BSE "log a call, grade it later" machinery. These
tests cover the two pieces most likely to break silently - the JSONL round trip and the
rule-based failure flag that feeds review_queue.py - not the broker-fetching parts of the
scripts, which need real data and are covered by manual verification (see CLAUDE.md)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from tradedesk.evidence import ContractKind, EvidenceClass, OutcomeState
from tradedesk.review_queue import load_queue
from tradedesk.signal_tracker import (
    TrackedSignal,
    flag_setup_failures,
    load_log,
    render_session_report,
    save_log,
)


def _row(i: int, setup: str, outcome: str | None) -> TrackedSignal:
    return TrackedSignal(
        signal_id=f"sig{i}", scrip_code="X", symbol="X", setup=setup, grade="C",
        armed_on="2026-01-01", entry=100.0, stop=95.0, t1=110.0, t2=115.0,
        net_rr_t1=1.0, net_rr_t2=2.0, rejected_for=[], logged_at="2026-01-01",
        outcome=outcome, label=1 if outcome == "target" else (0 if outcome else None),
    )  # fmt: skip


_R = {"target": 2.0, "stop": -1.0, "timeout": 0.0, "gap_stop": -1.2}


def _traded(i: int, setup: str, outcome: str, r: float | None = None) -> TrackedSignal:
    row = _row(i, setup, outcome)
    row.r_multiple = _R[outcome] if r is None else r
    return row


def test_log_round_trips_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = {f"sig{i}": _row(i, "nr7_breakout", None) for i in range(3)}
    save_log(rows, path)
    reloaded = load_log(path)
    assert reloaded.keys() == rows.keys()
    assert reloaded["sig0"].setup == "nr7_breakout"
    assert reloaded["sig0"].evidence_class == EvidenceClass.QUALIFIED_CALL.value
    assert reloaded["sig0"].outcome_state == OutcomeState.PENDING_CALL.value
    assert reloaded["sig0"].contract_kind == ContractKind.LEGACY.value


def test_dedicated_market_log_enforces_market_separation(tmp_path: Path) -> None:
    path = tmp_path / "crypto_signal_tracking.jsonl"
    row = _row(1, "nr7_breakout", "stop")
    row.market = "nse"

    with pytest.raises(ValueError, match="cross-market"):
        save_log({row.signal_id: row}, path)


def test_duplicate_signal_ids_in_a_ledger_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    record = asdict(_row(1, "nr7_breakout", None))
    path.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate signal_id"):
        load_log(path)


def test_invalid_resolution_is_not_reported_as_a_failed_call() -> None:
    row = _row(1, "nr7_breakout", "unavailable")
    row.outcome_state = "invalid_call"
    row.label = None
    row.r_multiple = None
    text = render_session_report("nse", date(2026, 1, 2), [], [row], {row.signal_id: row})
    assert "INVALID CALL" in text
    assert "excluded from performance learning" in text
    assert "WRONG" not in text


def test_flag_setup_failures_flags_a_setup_that_clearly_loses_money(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {}
    for i in range(20):
        outcome = "stop" if i < 19 else "target"  # 1/20: clearly loses money
        rows[f"sig{i}"] = _traded(i, "nr7_breakout", outcome)

    flagged = flag_setup_failures(
        "crypto", rows, min_resolved=5, review_path=review_path
    )

    assert flagged == ["nr7_breakout underperforming on crypto"]
    items = load_queue(review_path)
    assert len(items) == 1
    item = next(iter(items.values()))
    assert item.market == "crypto"
    assert item.status == "pending"


def test_flag_setup_failures_does_not_flag_below_the_minimum_sample(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _traded(i, "nr7_breakout", "stop") for i in range(3)}  # only 3 resolved

    flagged = flag_setup_failures(
        "crypto", rows, min_resolved=5, review_path=review_path
    )

    assert flagged == []
    assert load_queue(review_path) == {}


def test_flag_setup_failures_does_not_duplicate_an_existing_flag(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _traded(i, "nr7_breakout", "stop") for i in range(10)}

    first = flag_setup_failures("crypto", rows, min_resolved=5, review_path=review_path)
    second = flag_setup_failures("crypto", rows, min_resolved=5, review_path=review_path)

    assert first == ["nr7_breakout underperforming on crypto"]
    assert second == []  # already flagged - no duplicate item on a re-run
    assert len(load_queue(review_path)) == 1


def test_flag_setup_failures_leaves_a_healthy_setup_alone(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _traded(i, "base_breakout", "target" if i < 4 else "stop") for i in range(5)}

    flagged = flag_setup_failures("crypto", rows, min_resolved=5, review_path=review_path)

    assert flagged == []
    assert load_queue(review_path) == {}


def test_failure_flag_thresholds_do_not_pool_exit_contracts(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {}
    for i in range(3):
        quick = _traded(i, "base_breakout", "stop")
        quick.signal_id = f"quick-{i}"
        quick.contract_version = "quick-profit-v1"
        swing = _traded(i + 10, "base_breakout", "stop")
        swing.signal_id = f"swing-{i}"
        swing.contract_version = "swing-v1"
        rows[quick.signal_id] = quick
        rows[swing.signal_id] = swing
    assert flag_setup_failures("nse", rows, min_resolved=5, review_path=review_path) == []


def test_failure_flag_names_the_underperforming_exit_contract(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {}
    for i in range(10):
        quick = _traded(i, "base_breakout", "stop")
        quick.signal_id = f"quick-{i}"
        quick.contract_version = "quick-profit-v1"
        swing = _traded(i + 20, "base_breakout", "target")
        swing.signal_id = f"swing-{i}"
        swing.contract_version = "swing-v1"
        rows[quick.signal_id] = quick
        rows[swing.signal_id] = swing
    assert flag_setup_failures("nse", rows, min_resolved=5, review_path=review_path) == [
        "base_breakout [quick-profit-v1] underperforming on nse"
    ]


def test_a_small_or_unclear_loss_is_noise_not_a_flag(tmp_path: Path) -> None:
    # Five straight stops happen; two targets in nine at 2R is not clearly losing.
    review_path = tmp_path / "queue.jsonl"
    rows = {f"a{i}": _traded(i, "nr7_breakout", "stop") for i in range(5)}
    rows |= {
        f"b{i}": _traded(i + 10, "base_breakout", "target" if i < 2 else "stop") for i in range(9)
    }
    assert flag_setup_failures("nse", rows, review_path=review_path) == []


def test_a_setup_inactive_on_the_market_is_not_flagged(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _traded(i, "trend_pullback", "stop") for i in range(12)}
    assert (
        flag_setup_failures(
            "nse", rows, review_path=review_path, inactive=frozenset({"trend_pullback"})
        )
        == []
    )
    assert flag_setup_failures("nse", rows, review_path=review_path) == [
        "trend_pullback underperforming on nse"
    ]


def test_a_setup_with_few_targets_that_makes_money_is_not_flagged(tmp_path: Path) -> None:
    # A trail or timeout exit can make money while hardly ever recording a target.
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _traded(i, "r_trail", "timeout", r=0.6 + 0.01 * i) for i in range(30)}
    assert flag_setup_failures("nse", rows, review_path=review_path) == []
    assert load_queue(review_path) == {}


def test_a_setup_with_many_targets_that_loses_money_is_flagged(tmp_path: Path) -> None:
    # Half the calls hit a small target, and the stops cost far more than the targets pay.
    review_path = tmp_path / "queue.jsonl"
    rows = {}
    for i in range(40):
        rows[f"sig{i}"] = _traded(i, "r_tight", *(("target", 0.3) if i % 2 else ("stop", -1.0)))
    assert flag_setup_failures("nse", rows, review_path=review_path) == [
        "r_tight underperforming on nse"
    ]
    detail = next(iter(load_queue(review_path).values())).detail
    assert "R a call after costs over 40 legacy-t1-tracker-v1 calls" in detail
    assert "20 of 40 resolved calls hit target" in detail


def test_without_a_cost_figure_the_flag_says_its_money_is_before_costs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradedesk import digests

    def broken(market: str) -> float:
        raise OSError("no config")

    monkeypatch.setattr(digests, "round_trip", broken)
    review_path = tmp_path / "queue.jsonl"
    rows = {f"sig{i}": _traded(i, "r_trail", "stop") for i in range(10)}
    assert flag_setup_failures("nse", rows, review_path=review_path)
    assert "-1.00R a call before costs" in next(iter(load_queue(review_path).values())).detail
