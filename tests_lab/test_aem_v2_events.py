from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import (
    DEFAULT_EVENT_ENGINE_CONTRACT,
    AemV2Opportunity,
    opportunities_at,
    reconstruct_session_opportunities,
    resolve_opportunity,
)

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel


def frame_from_closes(
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    idx = pd.date_range("2026-09-25 09:15", periods=len(closes), freq="1min", tz="Asia/Kolkata")
    opens = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs or [max(o, c) + 0.02 for o, c in zip(opens, closes, strict=True)],
            "low": lows or [min(o, c) - 0.02 for o, c in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": volumes or [100.0] * len(closes),
        },
        index=idx,
    )


def execution_frame(periods: int = 80) -> pd.DataFrame:
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


def opportunity() -> AemV2Opportunity:
    return AemV2Opportunity(
        identifier="AEM_v2:NSE_1:2026-09-25:anticipatory_impulse:0920",
        scrip_code="NSE_1",
        symbol="TEST",
        session="2026-09-25",
        mode="anticipatory_impulse",
        signal_bar_open="2026-09-25T09:19:00+05:30",
        decision_at="2026-09-25T09:20:00+05:30",
        state_started_at="2026-09-25T09:17:00+05:30",
        intended_entry=100.0,
        entry_limit=100.15,
        causal_level=100.5,
        decision_values={},
        strategy_contract_sha256=DEFAULT_AEM_V2_CONTRACT.sha256,
        event_contract_sha256=DEFAULT_EVENT_ENGINE_CONTRACT.sha256,
    )


def costs() -> EquityCostModel:
    return EquityCostModel(ChargeSchedule())


def test_three_entry_modes_require_completed_events_in_causal_order():
    anticipatory = frame_from_closes(
        [100.0, 100.02, 100.05, 100.06, 100.10, 100.20],
        volumes=[100, 100, 100, 100, 100, 220],
        highs=[100.02, 100.04, 100.07, 100.65, 100.12, 100.22],
    )
    events = opportunities_at(
        anticipatory,
        at=pd.Timestamp("2026-09-25 09:21", tz="Asia/Kolkata"),
        scrip_code="NSE_1",
        symbol="TEST",
    )
    assert "anticipatory_impulse" in {event.mode for event in events}

    pullback = frame_from_closes(
        [100.0, 100.0, 100.0, 100.20, 100.15, 100.12, 100.25],
        volumes=[100, 100, 100, 220, 90, 80, 100],
        highs=[100.02, 100.02, 100.02, 100.22, 100.20, 100.15, 100.27],
        lows=[99.98, 99.98, 99.98, 99.99, 100.10, 100.09, 100.10],
    )
    events = opportunities_at(
        pullback,
        at=pd.Timestamp("2026-09-25 09:22", tz="Asia/Kolkata"),
        scrip_code="NSE_1",
        symbol="TEST",
    )
    assert "confirmed_pullback" in {event.mode for event in events}

    breakout = frame_from_closes(
        [100.0, 100.0, 100.0, 100.0, 100.0, 100.20, 100.12, 100.30],
        volumes=[100, 100, 100, 100, 100, 220, 80, 120],
        highs=[100.10, 100.10, 100.10, 100.10, 100.10, 100.25, 100.22, 100.32],
        lows=[99.98, 99.98, 99.98, 99.98, 99.98, 99.99, 100.08, 100.10],
    )
    events = opportunities_at(
        breakout,
        at=pd.Timestamp("2026-09-25 09:23", tz="Asia/Kolkata"),
        scrip_code="NSE_1",
        symbol="TEST",
    )
    assert "breakout_retest" in {event.mode for event in events}


def test_decision_ignores_future_bars_and_post_deadline_signals_are_rejected():
    frame = frame_from_closes(
        [100.0, 100.02, 100.05, 100.06, 100.10, 100.20, 50.0],
        volumes=[100, 100, 100, 100, 100, 220, 9_999_999],
        highs=[100.02, 100.04, 100.07, 100.65, 100.12, 100.22, 100.25],
        lows=[99.98, 100.0, 100.02, 100.03, 100.05, 100.08, 49.0],
    )
    at = pd.Timestamp("2026-09-25 09:21", tz="Asia/Kolkata")
    original = opportunities_at(frame, at=at, scrip_code="NSE_1", symbol="TEST")
    changed = frame.copy()
    changed.loc[changed.index >= at, ["open", "high", "low", "close", "volume"]] = [
        1,
        10_000,
        0.5,
        9_000,
        999_999_999,
    ]
    assert opportunities_at(changed, at=at, scrip_code="NSE_1", symbol="TEST") == original

    late_frame = execution_frame(110)
    assert (
        opportunities_at(
            late_frame,
            at=pd.Timestamp("2026-09-25 11:01", tz="Asia/Kolkata"),
            scrip_code="NSE_1",
            symbol="TEST",
        )
        == ()
    )
    assert all(
        pd.Timestamp(event.decision_at).strftime("%H:%M") <= "11:00"
        for event in reconstruct_session_opportunities(
            late_frame, scrip_code="NSE_1", symbol="TEST"
        )
    )


def test_gap_above_limit_is_unfilled_chase_not_a_favorable_fill():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:20", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [
        100.30,
        100.50,
        100.25,
        100.40,
    ]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "unfilled"
    assert result["outcome"] == "chase_rejected"
    assert result["strict_success"] is False


def test_same_bar_ambiguity_is_stop_first_and_entry_follows_prediction():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:20", tz="Asia/Kolkata")
    # Open below the trigger, then the bar spans both barriers. The low may be
    # pre-fill, so the registered adverse path is applied and disclosed.
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [99.95, 101.0, 99.0, 100.0]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "resolved" and result["outcome"] == "stop"
    assert result["reason"] == "same_bar_target_stop_ambiguity"
    assert result["ambiguous_bar_resolved_adversely"] is True
    assert result["decision_before_entry"] is True
    assert pd.Timestamp(result["entry_at"]) >= pd.Timestamp(opportunity().decision_at)


def test_target_touch_can_be_cost_negative_and_never_counts_as_strict_success():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:20", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [100.0, 100.8, 100.0, 100.5]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=1,
        costs=costs(),
    )
    assert result["status"] == "resolved"
    assert result["target_hit"] is True and result["outcome"] == "target"
    assert result["net_pnl"] < 0
    assert result["strict_success"] is False


def test_missing_entry_or_outcome_bars_fail_closed_as_unresolved():
    frame = execution_frame()
    missing_entry = frame.drop(pd.Timestamp("2026-09-25 09:21", tz="Asia/Kolkata"))
    result = resolve_opportunity(
        opportunity(),
        missing_entry,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unresolved"
    assert result["outcome"] == "data_failure"
    assert result["strict_success"] is False

    too_short = execution_frame(10)  # through 09:24; cannot observe the 25-minute deadline
    result = resolve_opportunity(
        opportunity(),
        too_short,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unresolved"
    assert result["reason"] == "missing_or_incomplete_outcome_window"
    assert result["filled"] is True


def test_bars_after_the_frozen_exit_window_cannot_change_a_resolved_outcome():
    frame = execution_frame()
    fill_stamp = pd.Timestamp("2026-09-25 09:20", tz="Asia/Kolkata")
    frame.loc[fill_stamp, ["open", "high", "low", "close"]] = [100.0, 100.8, 100.0, 100.5]
    original = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    damaged_later = frame.drop(pd.Timestamp("2026-09-25 10:10", tz="Asia/Kolkata"))
    changed = resolve_opportunity(
        opportunity(),
        damaged_later,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert changed == original


def test_expired_entry_is_reported_and_contract_fingerprints_are_enforced():
    frame = execution_frame()
    frame.loc[
        pd.date_range("2026-09-25 09:20", periods=3, freq="1min", tz="Asia/Kolkata"),
        ["open", "high", "low", "close"],
    ] = [99.0, 99.9, 98.9, 99.5]
    result = resolve_opportunity(
        opportunity(),
        frame,
        DEFAULT_AEM_V2_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unfilled" and result["outcome"] == "entry_expired"

    altered = replace(DEFAULT_EVENT_ENGINE_CONTRACT, maximum_chase_pct=0.002)
    with pytest.raises(ValueError, match="event contract differs"):
        resolve_opportunity(
            opportunity(),
            frame,
            DEFAULT_AEM_V2_CONTRACT.geometries[0],
            quantity=100,
            costs=costs(),
            event_contract=altered,
        )
