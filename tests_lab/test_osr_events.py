import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import osr_events
from tradedesk_lab.artifacts import digest
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT
from tradedesk_lab.osr_events import (
    DEFAULT_OSR_EVENT_CONTRACT,
    OsrOpportunity,
    opportunities_at,
    reconstruct_session_opportunities,
    resolve_opportunity,
)

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel


def execution_frame(periods: int = 100) -> pd.DataFrame:
    idx = pd.date_range("2026-09-25 09:15", periods=periods, freq="1min", tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "open": np.full(periods, 100.0),
            "high": np.full(periods, 100.1),
            "low": np.full(periods, 99.9),
            "close": np.full(periods, 100.0),
            "volume": np.full(periods, 1_000.0),
        },
        index=idx,
    )


def gap_reclaim_frame(periods: int = 100) -> pd.DataFrame:
    frame = execution_frame(periods)
    frame.loc[:, ["open", "high", "low", "close"]] = [97.9, 98.0, 97.8, 97.9]
    first = frame.index[0]
    frame.loc[first, ["open", "high", "low", "close"]] = [98.0, 98.05, 97.85, 97.9]
    low_bar = frame.index[8]
    frame.loc[low_bar, ["open", "high", "low", "close"]] = [97.8, 97.85, 97.6, 97.7]
    prior = frame.index[14]
    frame.loc[prior, ["open", "high", "low", "close"]] = [97.8, 97.9, 97.7, 97.85]
    reclaim = frame.index[15]
    frame.loc[reclaim, ["open", "high", "low", "close", "volume"]] = [
        97.85,
        98.20,
        97.80,
        98.15,
        2_000,
    ]
    frame.loc[frame.index[16] :, ["open", "high", "low", "close"]] = [
        98.1,
        98.2,
        98.0,
        98.1,
    ]
    return frame


def sweep_reclaim_frame(periods: int = 100) -> pd.DataFrame:
    frame = execution_frame(periods)
    sweep = frame.index[15]
    frame.loc[sweep, ["open", "high", "low", "close"]] = [100.0, 100.0, 99.7, 99.8]
    reclaim = frame.index[16]
    frame.loc[reclaim, ["open", "high", "low", "close", "volume"]] = [
        99.8,
        100.15,
        99.78,
        100.1,
        2_000,
    ]
    return frame


def opportunity() -> OsrOpportunity:
    return OsrOpportunity(
        identifier="OSR_v1:NSE_1:2026-09-25:opening_low_sweep_reclaim:0932",
        scrip_code="NSE_1",
        symbol="TEST",
        session="2026-09-25",
        mode="opening_low_sweep_reclaim",
        signal_bar_open="2026-09-25T09:31:00+05:30",
        decision_at="2026-09-25T09:32:00+05:30",
        state_started_at="2026-09-25T09:30:00+05:30",
        intended_entry=100.0,
        entry_limit=100.1,
        causal_reference=99.9,
        decision_values={},
        strategy_contract_sha256=DEFAULT_OSR_CONTRACT.sha256,
        event_contract_sha256=DEFAULT_OSR_EVENT_CONTRACT.sha256,
    )


def costs() -> EquityCostModel:
    return EquityCostModel(ChargeSchedule())


class FixedHighCostModel:
    slippage_pct = Decimal("0")

    def round_trip_cost(self, **_kwargs):
        return SimpleNamespace(total=Decimal("1.00"))


def test_gap_down_reclaim_uses_prior_context_and_a_completed_reclaim_bar():
    at = pd.Timestamp("2026-09-25 09:31", tz="Asia/Kolkata")
    events = opportunities_at(
        gap_reclaim_frame(),
        at=at,
        prior_close=100.0,
        prior_low=97.0,
        scrip_code="NSE_1",
        symbol="TEST",
    )
    gap_events = [event for event in events if event.mode == "gap_down_reclaim"]
    assert len(gap_events) == 1
    event = gap_events[0]
    assert pd.Timestamp(event.signal_bar_open) + pd.Timedelta(minutes=1) == at
    assert pd.Timestamp(event.decision_at) == at
    assert event.decision_values["gap_from_prior_close"] == pytest.approx(-0.02)
    assert event.decision_values["reclaim_close_location"] >= 0.65
    assert event.intended_entry <= event.entry_limit


def test_opening_low_requires_a_completed_range_then_sweep_then_later_reclaim():
    frame = sweep_reclaim_frame()
    too_early = opportunities_at(
        frame,
        at=pd.Timestamp("2026-09-25 09:31", tz="Asia/Kolkata"),
        prior_close=100.0,
        prior_low=99.0,
        scrip_code="NSE_1",
        symbol="TEST",
    )
    assert "opening_low_sweep_reclaim" not in {event.mode for event in too_early}

    at = pd.Timestamp("2026-09-25 09:32", tz="Asia/Kolkata")
    events = opportunities_at(
        frame,
        at=at,
        prior_close=100.0,
        prior_low=99.0,
        scrip_code="NSE_1",
        symbol="TEST",
    )
    sweep_events = [event for event in events if event.mode == "opening_low_sweep_reclaim"]
    assert len(sweep_events) == 1
    event = sweep_events[0]
    assert pd.Timestamp(event.state_started_at) < pd.Timestamp(event.signal_bar_open)
    assert event.causal_reference == pytest.approx(99.9)
    assert event.decision_values["sweep_depth"] >= 0.001


def test_signal_is_future_invariant_and_session_reconstruction_deduplicates_modes():
    frame = gap_reclaim_frame()
    at = pd.Timestamp("2026-09-25 09:31", tz="Asia/Kolkata")
    original = opportunities_at(
        frame,
        at=at,
        prior_close=100.0,
        prior_low=97.0,
        scrip_code="NSE_1",
        symbol="TEST",
    )
    changed = frame.copy()
    changed.loc[changed.index >= at, ["open", "high", "low", "close", "volume"]] = [
        1.0,
        10_000.0,
        0.5,
        9_000.0,
        999_999_999.0,
    ]
    assert (
        opportunities_at(
            changed,
            at=at,
            prior_close=100.0,
            prior_low=97.0,
            scrip_code="NSE_1",
            symbol="TEST",
        )
        == original
    )

    reconstructed = reconstruct_session_opportunities(
        frame,
        prior_close=100.0,
        prior_low=97.0,
        scrip_code="NSE_1",
        symbol="TEST",
    )
    modes = [event.mode for event in reconstructed]
    assert modes.count("gap_down_reclaim") == 1
    assert len(modes) == len(set(modes))
    assert all(
        pd.Timestamp(event.decision_at).strftime("%H:%M") <= "11:15"
        for event in reconstructed
    )


@pytest.mark.parametrize(
    ("frame", "prior_close", "prior_low"),
    [
        (gap_reclaim_frame(), 100.0, 97.0),
        (sweep_reclaim_frame(), 100.0, 99.0),
        (execution_frame(), 100.0, 99.0),
    ],
)
def test_reconstruction_prefilter_matches_exhaustive_detection(frame, prior_close, prior_low):
    expected = []
    seen_modes = set()
    for stamp in frame.index:
        at = stamp + pd.Timedelta(minutes=1)
        if at.strftime("%H:%M") < DEFAULT_OSR_CONTRACT.earliest_decision_time:
            continue
        if at.strftime("%H:%M") > DEFAULT_OSR_CONTRACT.latest_decision_time:
            break
        for event in opportunities_at(
            frame,
            at=at,
            prior_close=prior_close,
            prior_low=prior_low,
            scrip_code="NSE_1",
            symbol="TEST",
        ):
            if event.mode not in seen_modes:
                expected.append(event)
                seen_modes.add(event.mode)
    actual = reconstruct_session_opportunities(
        frame,
        prior_close=prior_close,
        prior_low=prior_low,
        scrip_code="NSE_1",
        symbol="TEST",
    )
    assert actual == tuple(expected)


def test_invalid_prior_context_and_incomplete_m1_fail_closed():
    frame = gap_reclaim_frame()
    kwargs = {
        "at": pd.Timestamp("2026-09-25 09:31", tz="Asia/Kolkata"),
        "scrip_code": "NSE_1",
        "symbol": "TEST",
    }
    with pytest.raises(ValueError, match="invalid_prior_daily_context"):
        opportunities_at(frame, prior_close=0, prior_low=97.0, **kwargs)
    with pytest.raises(ValueError, match="invalid_prior_daily_context"):
        opportunities_at(frame, prior_close=100.0, prior_low=101.0, **kwargs)
    with pytest.raises(ValueError, match="incomplete_m1_sequence"):
        opportunities_at(
            frame.drop(frame.index[10]),
            prior_close=100.0,
            prior_low=97.0,
            **kwargs,
        )


def test_gap_above_entry_limit_is_rejected_as_unfilled_chase():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:32", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [100.3, 100.5, 100.25, 100.4]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "unfilled"
    assert result["outcome"] == "chase_rejected"
    assert result["strict_success"] is False


def test_same_bar_target_stop_ambiguity_is_resolved_as_a_loss():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:32", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [99.95, 100.8, 99.4, 100.0]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "resolved" and result["outcome"] == "stop"
    assert result["reason"] == "same_bar_target_stop_ambiguity"
    assert result["ambiguous_bar_resolved_adversely"] is True
    assert result["strict_success"] is False
    assert result["decision_before_entry"] is True


def test_target_touch_with_negative_net_pnl_is_not_a_strict_success():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:32", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [100.0, 100.5, 100.0, 100.4]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=1,
        costs=FixedHighCostModel(),
    )
    assert result["status"] == "resolved"
    assert result["target_hit"] is True
    assert result["net_pnl"] < 0
    assert result["strict_success"] is False


def test_missing_entry_and_outcome_bars_are_never_wins():
    missing_entry = execution_frame().drop(
        pd.Timestamp("2026-09-25 09:33", tz="Asia/Kolkata")
    )
    result = resolve_opportunity(
        opportunity(),
        missing_entry,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unresolved"
    assert result["outcome"] == "data_failure"
    assert result["strict_success"] is False

    too_short = execution_frame(25)
    result = resolve_opportunity(
        opportunity(),
        too_short,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unresolved"
    assert result["reason"] == "missing_or_incomplete_outcome_window"
    assert result["filled"] is True
    assert result["strict_success"] is False


def test_bars_after_frozen_exit_window_cannot_change_the_outcome():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:32", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [100.0, 100.5, 100.0, 100.4]
    original = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    damaged_later = frame.drop(pd.Timestamp("2026-09-25 10:10", tz="Asia/Kolkata"))
    changed = resolve_opportunity(
        opportunity(),
        damaged_later,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert changed == original


def test_expired_entry_and_contract_mismatches_are_explicit():
    frame = execution_frame()
    frame.loc[
        pd.date_range("2026-09-25 09:32", periods=2, freq="1min", tz="Asia/Kolkata"),
        ["open", "high", "low", "close"],
    ] = [99.0, 99.9, 98.9, 99.5]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_OSR_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unfilled" and result["outcome"] == "entry_expired"

    altered = replace(DEFAULT_OSR_EVENT_CONTRACT, tick_size=0.10)
    with pytest.raises(ValueError, match="event contract differs"):
        resolve_opportunity(
            opportunity(),
            frame,
            DEFAULT_OSR_CONTRACT.geometries[0],
            quantity=100,
            costs=costs(),
            event_contract=altered,
        )


def test_tracked_osr_engine_evidence_matches_frozen_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/osr-engine.json").read_text(encoding="utf-8"))

    assert evidence["status"] == "causal_engine_implemented_not_evaluated"
    assert evidence["milestone"] == 1
    assert evidence["opportunity_engine_implemented"] is True
    assert evidence["outcome_engine_implemented"] is True
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_OSR_CONTRACT.sha256
    assert evidence["event_contract_sha256"] == DEFAULT_OSR_EVENT_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(Path(osr_events.__file__))
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_osr_events.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False
