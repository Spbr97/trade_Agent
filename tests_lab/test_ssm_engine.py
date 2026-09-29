import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import ssm_engine
from tradedesk_lab.artifacts import digest
from tradedesk_lab.ssm_contract import DEFAULT_SSM_CONTRACT
from tradedesk_lab.ssm_engine import (
    DEFAULT_SSM_ENGINE_CONTRACT,
    SsmOpportunity,
    compute_slot_features,
    opportunities_for_slot,
    resolve_opportunity,
)

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel

SESSION = "2026-09-28"
SLOT = "09:45"
PRIOR_DATES = list(pd.bdate_range(end="2026-09-25", periods=40))


def costs() -> EquityCostModel:
    return EquityCostModel(ChargeSchedule())


def _one_session(day: str, *, slot_return: float, pre_return: float = 0.001) -> pd.DataFrame:
    idx = pd.date_range(f"{day} 09:15", periods=75, freq="1min", tz="Asia/Kolkata")
    pre = np.linspace(100.0, 100.0 * (1 + pre_return), 30)
    slot = np.linspace(pre[-1], pre[-1] * (1 + slot_return), 31)[1:]
    later = np.full(15, slot[-1])
    close = np.concatenate([pre, slot, later])
    open_ = np.concatenate(([close[0]], close[:-1]))
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + 0.03,
            "low": np.minimum(open_, close) - 0.03,
            "close": close,
            "volume": np.linspace(1_000, 1_500, len(idx)),
        },
        index=idx,
    )


def stock_frame(*, slot_return: float, current_slot_return: float = 0.0) -> pd.DataFrame:
    history = [
        _one_session(str(day.date()), slot_return=slot_return + 0.00002 * (i % 3))
        for i, day in enumerate(PRIOR_DATES)
    ]
    history.append(
        _one_session(SESSION, slot_return=current_slot_return, pre_return=0.0015)
    )
    return pd.concat(history)


def daily_frame() -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-09-29", periods=60, tz="Asia/Kolkata")
    close = np.linspace(90.0, 100.0, len(idx))
    open_ = close - 0.1
    return pd.DataFrame(
        {
            "open": open_,
            "high": close + 0.4,
            "low": open_ - 0.4,
            "close": close,
            "volume": np.full(len(idx), 1_000_000.0),
        },
        index=idx,
    )


def peer_frames(count: int = 31, *, include_self: bool = False) -> dict[str, pd.DataFrame]:
    result = {
        f"NSE_P{i:02d}": stock_frame(slot_return=-0.002 + i * 0.00016)
        for i in range(count)
    }
    if include_self:
        result["NSE_1"] = stock_frame(slot_return=0.004)
    return result


def feature_kwargs(**overrides) -> dict:
    kwargs = dict(
        scrip_code="NSE_1",
        session=SESSION,
        slot_start=SLOT,
        frame=stock_frame(slot_return=0.004),
        daily_frame=daily_frame(),
        universe_frames=peer_frames(),
        quantity=100,
        costs=costs(),
    )
    kwargs.update(overrides)
    return kwargs


def generated_opportunity() -> SsmOpportunity:
    result = opportunities_for_slot(symbol="TEST", **feature_kwargs())
    assert {item.mode for item in result} == {
        "lag1_cross_sectional",
        "multi_day_persistence",
    }
    return result[0]


def test_features_match_the_frozen_registry_and_are_finite():
    result = compute_slot_features(**feature_kwargs())
    expected = {feature.name for feature in DEFAULT_SSM_CONTRACT.features}
    assert set(result["values"]) == expected
    assert len(result["values"]) == 24
    assert all(np.isfinite(value) for value in result["values"].values())
    assert result["history_sessions"] == 40
    assert result["peer_count"] == 31
    assert result["available_at"] == "2026-09-28T09:44:00+05:30"
    assert result["entry_at"] == "2026-09-28T09:45:00+05:30"
    assert result["values"]["lag1_cross_sectional_rank"] == 1.0


def test_candidate_is_excluded_from_peer_statistics():
    without_self = compute_slot_features(**feature_kwargs())
    with_self = compute_slot_features(
        **feature_kwargs(universe_frames=peer_frames(include_self=True))
    )
    assert with_self == without_self


def test_predicted_slot_and_later_mutations_cannot_change_features_or_calls():
    original_frame = stock_frame(slot_return=0.004)
    original_features = compute_slot_features(**feature_kwargs(frame=original_frame))
    original_calls = opportunities_for_slot(symbol="TEST", **feature_kwargs(frame=original_frame))

    changed = original_frame.copy()
    future = changed.index >= pd.Timestamp("2026-09-28 09:44", tz="Asia/Kolkata")
    changed.loc[future, ["open", "high", "low", "close", "volume"]] = [
        5_000.0,
        5_500.0,
        4_500.0,
        5_100.0,
        99_000_000.0,
    ]
    assert compute_slot_features(**feature_kwargs(frame=changed)) == original_features
    assert opportunities_for_slot(symbol="TEST", **feature_kwargs(frame=changed)) == original_calls


def test_future_peer_and_daily_rows_are_ignored():
    original = compute_slot_features(**feature_kwargs())
    peers = peer_frames()
    for peer in peers.values():
        future = peer.index >= pd.Timestamp("2026-09-28 09:44", tz="Asia/Kolkata")
        peer.loc[future, ["open", "high", "low", "close", "volume"]] = [
            8_000.0,
            8_500.0,
            7_500.0,
            8_100.0,
            88_000_000.0,
        ]
    future_daily = daily_frame()
    future_daily.loc[
        future_daily.index >= pd.Timestamp(SESSION, tz="Asia/Kolkata"), "close"
    ] = 9_999
    assert (
        compute_slot_features(
            **feature_kwargs(universe_frames=peers, daily_frame=future_daily)
        )
        == original
    )


def test_incomplete_history_or_peer_population_fails_closed():
    short = pd.concat(
        [
            _one_session(str(day.date()), slot_return=0.004)
            for day in PRIOR_DATES[-19:]
        ]
        + [_one_session(SESSION, slot_return=0.0)]
    )
    with pytest.raises(ValueError, match="insufficient_same_slot_history"):
        compute_slot_features(**feature_kwargs(frame=short))

    with pytest.raises(ValueError, match="insufficient_peer_universe"):
        compute_slot_features(**feature_kwargs(universe_frames=peer_frames(29)))


def test_missing_pre_decision_bar_and_unregistered_slot_fail_closed():
    missing = stock_frame(slot_return=0.004).drop(
        pd.Timestamp("2026-09-28 09:30", tz="Asia/Kolkata")
    )
    with pytest.raises(ValueError, match="incomplete_pre_slot_history"):
        compute_slot_features(**feature_kwargs(frame=missing))
    with pytest.raises(ValueError, match="frozen registry"):
        compute_slot_features(**feature_kwargs(slot_start="09:30"))


def execution_frame() -> pd.DataFrame:
    frame = _one_session(SESSION, slot_return=0.0)
    slot = pd.date_range(f"{SESSION} 09:45", periods=30, freq="1min", tz="Asia/Kolkata")
    frame.loc[slot, ["open", "high", "low", "close", "volume"]] = [
        100.0,
        100.10,
        99.90,
        100.0,
        10_000.0,
    ]
    return frame


def test_next_slot_open_fill_and_target_are_cost_adjusted():
    frame = execution_frame()
    entry_bar = pd.Timestamp(f"{SESSION} 09:45", tz="Asia/Kolkata")
    frame.loc[entry_bar, ["open", "high", "low", "close"]] = [100.0, 100.6, 99.9, 100.4]
    result = resolve_opportunity(
        generated_opportunity(),
        frame,
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "resolved"
    assert result["outcome"] == "target"
    assert result["strict_success"] is True
    assert result["net_r"] < result["gross_r"]
    assert result["decision_before_entry"] is True


def test_same_bar_target_stop_ambiguity_is_a_loss():
    frame = execution_frame()
    entry_bar = pd.Timestamp(f"{SESSION} 09:45", tz="Asia/Kolkata")
    frame.loc[entry_bar, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
    result = resolve_opportunity(
        generated_opportunity(),
        frame,
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "resolved" and result["outcome"] == "stop"
    assert result["reason"] == "same_bar_target_stop_ambiguity"
    assert result["ambiguous_bar_resolved_adversely"] is True
    assert result["strict_success"] is False


def test_missing_entry_or_outcome_bar_is_never_a_win():
    opportunity = generated_opportunity()
    entry = pd.Timestamp(f"{SESSION} 09:45", tz="Asia/Kolkata")
    missing_entry = execution_frame().drop(entry)
    result = resolve_opportunity(
        opportunity,
        missing_entry,
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unresolved"
    assert result["reason"] == "missing_entry_bar"
    assert result["strict_success"] is False

    missing_later = execution_frame().drop(entry + pd.Timedelta(minutes=10))
    result = resolve_opportunity(
        opportunity,
        missing_later,
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unresolved"
    assert result["reason"] == "missing_or_incomplete_outcome_window"
    assert result["filled"] is True
    assert result["strict_success"] is False


def test_zero_entry_volume_is_unfilled_and_time_exit_is_not_a_strict_win():
    opportunity = generated_opportunity()
    frame = execution_frame()
    entry = pd.Timestamp(f"{SESSION} 09:45", tz="Asia/Kolkata")
    frame.loc[entry, "volume"] = 0
    result = resolve_opportunity(
        opportunity,
        frame,
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    assert result["status"] == "unfilled"
    assert result["outcome"] == "no_entry_liquidity"

    result = resolve_opportunity(
        opportunity,
        execution_frame(),
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=1_000,
        costs=costs(),
    )
    assert result["status"] == "resolved"
    assert result["outcome"] == "time_exit"
    assert result["strict_success"] is False


def test_bars_after_slot_and_contract_mismatches_cannot_rewrite_outcome():
    opportunity = generated_opportunity()
    frame = execution_frame()
    original = resolve_opportunity(
        opportunity,
        frame,
        DEFAULT_SSM_CONTRACT.geometries[0],
        quantity=100,
        costs=costs(),
    )
    later = frame.copy()
    stamp = pd.Timestamp(f"{SESSION} 10:20", tz="Asia/Kolkata")
    later.loc[stamp, ["open", "high", "low", "close", "volume"]] = [
        -1.0,
        -1.0,
        -1.0,
        -1.0,
        -1.0,
    ]
    assert (
        resolve_opportunity(
            opportunity,
            later,
            DEFAULT_SSM_CONTRACT.geometries[0],
            quantity=100,
            costs=costs(),
        )
        == original
    )

    altered = replace(DEFAULT_SSM_ENGINE_CONTRACT, tick_size=0.10)
    with pytest.raises(ValueError, match="engine contract differs"):
        resolve_opportunity(
            opportunity,
            frame,
            DEFAULT_SSM_CONTRACT.geometries[0],
            quantity=100,
            costs=costs(),
            engine_contract=altered,
        )


def test_geometry_and_decision_order_are_frozen():
    opportunity = generated_opportunity()
    bad = replace(opportunity, decision_at=opportunity.entry_at)
    with pytest.raises(ValueError, match="does not precede entry"):
        resolve_opportunity(
            bad,
            execution_frame(),
            DEFAULT_SSM_CONTRACT.geometries[0],
            quantity=100,
            costs=costs(),
        )

    assert Decimal(str(DEFAULT_SSM_CONTRACT.geometries[0].gross_reward_risk)) >= Decimal(
        "0.6666666666666666"
    )


def test_tracked_ssm_engine_evidence_matches_frozen_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/ssm-engine.json").read_text("utf-8"))

    assert evidence["status"] == "causal_engine_implemented_not_evaluated"
    assert evidence["milestone"] == 1
    assert evidence["feature_engine_implemented"] is True
    assert evidence["opportunity_engine_implemented"] is True
    assert evidence["outcome_engine_implemented"] is True
    assert evidence["feature_count"] == 24
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_SSM_CONTRACT.sha256
    assert evidence["engine_contract_sha256"] == DEFAULT_SSM_ENGINE_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(Path(ssm_engine.__file__))
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_ssm_engine.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False
