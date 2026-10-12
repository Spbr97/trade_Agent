from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import IST
from tradedesk.momentum_ignition_watch import (
    MAX_BREAK_EVEN_WIN_RATE,
    TARGET_R_GRID,
    _append_plan_event,
    _economics_contract,
    _evaluate_plan,
    _load_plan_events,
    _registration_blocker,
    _select_target,
    _target_economics,
    build_momentum_ignition_watch,
    collect_momentum_ignition_watch,
    load_momentum_ignition_watch,
    update_momentum_ignition_plans,
)
from tradedesk.prediction_ledger import canonical_sha256


def _ignition_frame(rank: int = 16) -> pd.DataFrame:
    index = pd.date_range("2026-10-10 00:00", periods=40, freq="15min", tz=IST)
    close = np.full(40, 100.0)
    close[-1] = 100.0 + rank * 0.05
    high = close + 1.0
    low = close - 1.0
    high[-1] = close[-1] + 0.8
    low[-1] = close[-1] - 1.6
    volume = np.full(40, 100.0)
    volume[-1] = 150.0 + rank * 5.0
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def _reclaim_frame() -> pd.DataFrame:
    frame = _ignition_frame(4)
    frame.loc[frame.index[-2], ["open", "high", "low", "close", "volume"]] = [
        99.5,
        100.0,
        98.0,
        99.0,
        100.0,
    ]
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = [
        99.2,
        101.6,
        99.2,
        100.8,
        130.0,
    ]
    return frame


def _shortlist(count: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "scrip_code": f"CDX_COIN{rank:02d}INR",
                "symbol": f"COIN{rank:02d}INR",
                "opportunity_probability": 0.50 + rank / 100.0,
            }
            for rank in range(1, count + 1)
        ]
    )


def _observed_at(frame: pd.DataFrame) -> datetime:
    return frame.index[-1].to_pydatetime() + timedelta(minutes=15)


def _build_many(count: int = 16):
    shortlist = _shortlist(count)
    frames = {
        str(row.scrip_code): _ignition_frame(index)
        for index, row in enumerate(shortlist.itertuples(index=False), 1)
    }
    report, registration = build_momentum_ignition_watch(
        shortlist,
        frames,
        observed_at=_observed_at(next(iter(frames.values()))),
    )
    return report, registration, frames


def test_full_universe_ranks_are_separate_from_top12_display_and_top3_registration() -> None:
    report, registration, _ = _build_many()

    assert report["intraday_valid_rows"] == 16
    assert report["universe_coverage"] == 1.0
    assert len(report["candidates"]) == 12
    assert len(registration) >= 4
    assert [row["scrip_code"] for row in registration[:3]] == [
        "CDX_COIN16INR",
        "CDX_COIN15INR",
        "CDX_COIN14INR",
    ]
    assert all(row["mechanism"] == "breakout_ignition" for row in registration)
    assert all(row["tradeable_now"] is False for row in registration)


def test_coverage_below_85_percent_blocks_only_new_registration() -> None:
    shortlist = _shortlist(10)
    frames = {
        str(row.scrip_code): _ignition_frame(index)
        for index, row in enumerate(shortlist.head(8).itertuples(index=False), 1)
    }
    report, registration = build_momentum_ignition_watch(
        shortlist,
        frames,
        observed_at=_observed_at(next(iter(frames.values()))),
    )

    assert report["status"] == "insufficient_universe_coverage"
    assert report["universe_coverage"] == 0.8
    assert registration == []
    assert all(
        "insufficient_universe_coverage" in row["blockers"]
        for row in report["candidates"]
    )


def test_shakeout_reclaim_requires_a_recent_same_symbol_structure_failure() -> None:
    frame = _reclaim_frame()
    code = "CDX_RECLAIMINR"
    shortlist = pd.DataFrame(
        [{"scrip_code": code, "symbol": "RECLAIMINR", "opportunity_probability": None}]
    )
    without_failure, registrations = build_momentum_ignition_watch(
        shortlist, {code: frame}, observed_at=_observed_at(frame)
    )
    assert registrations == []
    assert without_failure["candidates"][0]["mechanism"] == "breakout_ignition"
    assert "volume_ratio_below_1_50" in without_failure["candidates"][0]["blockers"]

    failure = {
        "plan_id": "failed-plan",
        "scrip_code": code,
        "state": "invalidated",
        "resolution_reason": "structure_failed_before_trigger",
        "resolved_at": frame.index[-2].isoformat(),
    }
    with_failure, registrations = build_momentum_ignition_watch(
        shortlist,
        {code: frame},
        observed_at=_observed_at(frame),
        plan_history=[failure],
    )
    assert len(registrations) == 1
    assert registrations[0]["mechanism"] == "shakeout_reclaim"
    assert registrations[0]["prior_failure_plan_id"] == "failed-plan"
    assert with_failure["reclaim_candidates"] == 1

    same_bar_failure = {**failure, "resolved_at": frame.index[-1].isoformat()}
    _, registrations = build_momentum_ignition_watch(
        shortlist,
        {code: frame},
        observed_at=_observed_at(frame),
        plan_history=[same_bar_failure],
    )
    assert registrations == []


def test_stablecoin_is_never_a_directional_m21_registration() -> None:
    frame = _ignition_frame()
    shortlist = pd.DataFrame(
        [{"scrip_code": "CDX_USDTINR", "symbol": "USDTINR", "opportunity_probability": 0.9}]
    )
    report, registration = build_momentum_ignition_watch(
        shortlist, {"CDX_USDTINR": frame}, observed_at=_observed_at(frame)
    )

    assert registration == []
    assert "non_directional_stablecoin" in report["candidates"][0]["blockers"]


def test_adaptive_target_uses_the_smallest_cost_feasible_grid_value() -> None:
    selected = _select_target(trigger=101.101, stop=98.90)
    assert selected is not None
    assert selected["target_r_multiple"] in TARGET_R_GRID
    assert selected["target_net_r"] > 0.0
    assert selected["stop_net_r"] < 0.0
    assert selected["break_even_win_rate"] <= MAX_BREAK_EVEN_WIN_RATE

    selected_index = TARGET_R_GRID.index(selected["target_r_multiple"])
    for multiple in TARGET_R_GRID[:selected_index]:
        economics = _target_economics(
            entry_raw=101.101,
            stop=98.90,
            target=101.101 + multiple * (101.101 - 98.90),
        )
        assert economics is None or economics["break_even_win_rate"] > MAX_BREAK_EVEN_WIN_RATE


def _plan_from_candidate(candidate: dict[str, object]) -> dict[str, object]:
    decision_bar = str(candidate["features"]["last_closed_bar"])  # type: ignore[index]
    economics_contract = _economics_contract()
    return {
        "version": "momentum-ignition-plan-v1",
        "event": "plan_armed",
        "plan_id": "test-plan",
        "market": "crypto",
        "scrip_code": candidate["scrip_code"],
        "symbol": candidate["symbol"],
        "mechanism": candidate["mechanism"],
        "armed_at": decision_bar,
        "decision_bar": decision_bar,
        "last_evaluated_bar": decision_bar,
        "state": "pending_trigger",
        "bars_seen": 0,
        "trigger_bars_seen": 0,
        "trigger": candidate["trigger"],
        "stop": candidate["stop"],
        "target": candidate["target"],
        "target_r_multiple": candidate["target_r_multiple"],
        "target_net_r": candidate["target_net_r"],
        "stop_net_r": candidate["stop_net_r"],
        "break_even_win_rate": candidate["break_even_win_rate"],
        "economics_contract": economics_contract,
        "economics_contract_sha256": canonical_sha256(economics_contract),
        "momentum_score": candidate["momentum_score"],
        "opportunity_probability": candidate["opportunity_probability"],
        "source_snapshot_sha256": "snapshot",
        "observed_at": decision_bar,
        "authority": "research_watch_only",
    }


def _append_bar(
    frame: pd.DataFrame,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> pd.DataFrame:
    return pd.concat(
        [
            frame,
            pd.DataFrame(
                [{"open": open_price, "high": high, "low": low, "close": close, "volume": volume}],
                index=[frame.index[-1] + timedelta(minutes=15)],
            ),
        ]
    )


def test_trigger_is_later_close_with_volume_and_does_not_reuse_confirmation_path() -> None:
    report, registration, frames = _build_many()
    del report
    candidate = registration[0]
    frame = frames[str(candidate["scrip_code"])]
    plan = _plan_from_candidate(candidate)
    trigger = float(candidate["trigger"])
    stop = float(candidate["stop"])
    target = float(candidate["target"])

    # Even though this confirmation bar spans both barriers, entry happens only at its
    # completed close, so its earlier path cannot resolve the newly-created position.
    confirmation = _append_bar(
        frame,
        open_price=trigger * 0.999,
        high=target * 1.01,
        low=stop * 0.99,
        close=trigger * 1.0001,
        volume=400.0,
    )
    result = _evaluate_plan(
        plan,
        confirmation,
        observed_at=confirmation.index[-1].to_pydatetime() + timedelta(minutes=15),
    )
    assert result is not None
    assert result["state"] == "triggered"
    assert result["triggered_at"] != result["decision_bar"]

    both = _append_bar(
        confirmation,
        open_price=(stop + target) / 2.0,
        high=target * 1.01,
        low=stop * 0.99,
        close=(stop + target) / 2.0,
        volume=100.0,
    )
    resolved = _evaluate_plan(
        result,
        both,
        observed_at=both.index[-1].to_pydatetime() + timedelta(minutes=15),
    )
    assert resolved is not None
    assert resolved["state"] == "stop"


def test_actual_confirmation_entry_is_rechecked_and_never_moves_the_target() -> None:
    _, registration, frames = _build_many()
    candidate = registration[0]
    frame = frames[str(candidate["scrip_code"])]
    plan = _plan_from_candidate(candidate)
    target = float(candidate["target"])
    confirmation = _append_bar(
        frame,
        open_price=target,
        high=target * 1.02,
        low=target * 0.99,
        close=target * 1.001,
        volume=400.0,
    )

    result = _evaluate_plan(
        plan,
        confirmation,
        observed_at=confirmation.index[-1].to_pydatetime() + timedelta(minutes=15),
    )
    assert result is not None
    assert result["state"] == "invalidated"
    assert result["resolution_reason"] == "actual_entry_failed_cost_gate"
    assert result["target"] == target


def test_update_registers_only_top3_and_keeps_a_separate_hash_chain(tmp_path) -> None:
    report, registration, frames = _build_many()
    observed_at = _observed_at(next(iter(frames.values())))
    summary = update_momentum_ignition_plans(
        report,
        registration,
        frames,
        observed_at=observed_at,
        output_root=tmp_path,
    )

    assert summary["new_plans_registered"] == 3
    assert summary["states"] == {"pending_trigger": 3}
    path = tmp_path / "crypto" / "plans.jsonl"
    events = _load_plan_events(path)
    assert len(events) == 3
    assert events[0]["previous_sha256"] == "GENESIS"
    repeated = update_momentum_ignition_plans(
        report,
        registration,
        frames,
        observed_at=observed_at,
        output_root=tmp_path,
    )
    assert repeated["new_plans_registered"] == 0
    assert repeated["total_plans"] == 3
    assert "snapshot_plan_cap" in repeated["registration_blockers"]

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["trigger"] = float(tampered["trigger"]) + 1.0
    lines[0] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash-chain failure"):
        _load_plan_events(path)


def test_blocked_leader_backfills_from_rank_four(tmp_path) -> None:
    report, registration, frames = _build_many()
    observed_at = _observed_at(next(iter(frames.values())))
    active = _plan_from_candidate(registration[0])
    active["plan_id"] = "already-active"
    active["decision_bar"] = (
        datetime.fromisoformat(str(active["decision_bar"])) - timedelta(minutes=15)
    ).isoformat()
    active["last_evaluated_bar"] = str(
        registration[0]["features"]["last_closed_bar"]
    )
    _append_plan_event(tmp_path / "crypto" / "plans.jsonl", active)

    summary = update_momentum_ignition_plans(
        report,
        registration,
        frames,
        observed_at=observed_at,
        output_root=tmp_path,
    )

    assert summary["registration_blockers"] == {"active_plan_exists": 1}
    assert summary["new_plans_registered"] == 3
    events = _load_plan_events(tmp_path / "crypto" / "plans.jsonl")
    armed_codes = {
        event["scrip_code"] for event in events if event["event"] == "plan_armed"
    }
    assert registration[3]["scrip_code"] in armed_codes


def test_plan_fails_closed_if_its_economics_fingerprint_is_tampered() -> None:
    _, registration, frames = _build_many()
    candidate = registration[0]
    plan = _plan_from_candidate(candidate)
    plan["economics_contract_sha256"] = "tampered"
    frame = frames[str(candidate["scrip_code"])]

    result = _evaluate_plan(plan, frame, observed_at=_observed_at(frame))

    assert result is not None
    assert result["state"] == "invalidated"
    assert result["resolution_reason"] == "economics_contract_integrity_failure"


def test_registration_enforces_cooldown_daily_cap_and_reclaim_24h_cap() -> None:
    frame = _ignition_frame()
    decision = frame.index[-1].to_pydatetime()
    candidate = {
        "scrip_code": "CDX_TESTINR",
        "mechanism": "breakout_ignition",
        "features": {"last_closed_bar": decision.isoformat()},
    }
    recent = {
        "scrip_code": "CDX_TESTINR",
        "state": "invalidated",
        "resolved_at": (decision - timedelta(minutes=30)).isoformat(),
        "armed_at": (decision - timedelta(hours=1)).isoformat(),
        "decision_bar": (decision - timedelta(hours=1)).isoformat(),
    }
    assert (
        _registration_blocker(candidate, latest={"one": recent}, observed_at=decision)
        == "four_bar_cooldown"
    )

    older = {**recent, "resolved_at": (decision - timedelta(hours=2)).isoformat()}
    second = {
        **older,
        "armed_at": (decision - timedelta(hours=3)).isoformat(),
        "decision_bar": (decision - timedelta(hours=3)).isoformat(),
    }
    assert (
        _registration_blocker(
            candidate, latest={"one": older, "two": second}, observed_at=decision
        )
        == "daily_plan_cap"
    )

    reclaim = {**candidate, "mechanism": "shakeout_reclaim"}
    previous_reclaim = {
        **older,
        "mechanism": "shakeout_reclaim",
        "armed_at": (decision - timedelta(hours=3)).isoformat(),
    }
    assert (
        _registration_blocker(
            reclaim, latest={"one": previous_reclaim}, observed_at=decision
        )
        == "reclaim_24h_cap"
    )


def test_non_crypto_loader_is_explicitly_not_applicable(tmp_path) -> None:
    report = load_momentum_ignition_watch("nse", output_root=tmp_path)

    assert report["status"] == "not_applicable"
    assert report["eligible_for_live"] is False
    assert report["baseline_accuracy_improved"] is False
    assert report["watch_plans"]["evidence"]["status"] == "not_applicable"


def test_collector_persists_separate_verified_snapshot_and_loader_fails_closed(
    tmp_path,
) -> None:
    shortlist = _shortlist(16)
    frames = {
        str(row.scrip_code): _ignition_frame(index)
        for index, row in enumerate(shortlist.itertuples(index=False), 1)
    }
    observed_at = _observed_at(next(iter(frames.values())))
    collected = collect_momentum_ignition_watch(
        shortlist,
        frames,
        observed_at=observed_at,
        output_root=tmp_path,
    )
    loaded = load_momentum_ignition_watch(
        "crypto", output_root=tmp_path, observed_at=observed_at
    )
    assert collected["snapshot_sha256"] == loaded["snapshot_sha256"]
    assert loaded["watch_plans"]["total_plans"] == 3

    path = tmp_path / "crypto" / "latest.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["status"] = "qualified"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        load_momentum_ignition_watch("crypto", output_root=tmp_path)

    path.write_text(json.dumps(collected, sort_keys=True), encoding="utf-8")
    (tmp_path / "crypto" / "snapshots.jsonl").unlink()
    with pytest.raises(ValueError, match="snapshot ledger is missing"):
        load_momentum_ignition_watch("crypto", output_root=tmp_path)


def test_decision_snapshot_excludes_an_unclosed_m15_bar() -> None:
    report, _, frames = _build_many()
    code = "CDX_COIN16INR"
    frame = frames[code]
    observed_at = _observed_at(frame)
    unclosed = pd.DataFrame(
        [{"open": 500.0, "high": 600.0, "low": 400.0, "close": 550.0, "volume": 9999.0}],
        index=[pd.Timestamp(observed_at)],
    )
    rebuilt, _ = build_momentum_ignition_watch(
        _shortlist(16),
        {**frames, code: pd.concat([frame, unclosed])},
        observed_at=observed_at,
    )
    by_code = {row["scrip_code"]: row for row in rebuilt["candidates"]}
    assert by_code[code]["features"]["last_close"] != 550.0
    assert report["candidates"][0]["features"]["last_closed_bar"] == by_code[code][
        "features"
    ]["last_closed_bar"]
