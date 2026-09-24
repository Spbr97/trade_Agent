import hashlib
import json
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_benchmark import (
    _assert_replay,
    counterfactual_grid,
    decision_times,
    null_decision,
    run_benchmark,
    sample_cohorts,
)
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_detector import AemCandidate, evaluate_trigger
from tradedesk_lab.aem_labels import label_trade

from tradedesk.config.models import ChargeSchedule, RiskConfig
from tradedesk.markets.costs import EquityCostModel


def fixture():
    contract = AemContract(max_hold_minutes=3)
    candidate = AemCandidate(
        "test",
        "NSE_1",
        "TEST",
        date(2026, 9, 17),
        102,
        3,
        {},
        contract.sha256,
    )
    idx = pd.date_range("2026-09-18 09:15", periods=375, freq="min", tz="Asia/Kolkata")
    session = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 1000,
            "vwap": 100.0,
            "atr14": 0.2,
            "session_bar": np.arange(375),
            "tod_rvol": 1.0,
        },
        index=idx,
    )
    return (
        candidate,
        session,
        contract,
        RiskConfig(trading_capital=Decimal("100000")),
        EquityCostModel(ChargeSchedule()),
    )


def test_full_window_has_101_closed_bar_decision_times():
    times = decision_times("2026-09-18", AemContract())
    assert len(times) == 101 and times[0].strftime("%H:%M") == "09:20"
    assert times[-1].strftime("%H:%M") == "11:00"


def test_forced_random_attempt_is_causal_and_bypasses_signal_gates():
    candidate, session, contract, _, _ = fixture()
    at = session.index[5]
    before = null_decision(candidate, session, at, contract)
    assert before.decision == "TRADE" and before.features["eligible"] is False
    assert before.features["entry_pattern"] is None
    future = session.copy()
    future.loc[future.index >= at, ["open", "high", "low", "close", "volume", "vwap"]] = 999
    assert null_decision(candidate, future, at, contract) == before


def test_random_attempt_at_observed_trigger_has_identical_fill_and_costs():
    candidate, session, contract, risk, costs = fixture()
    session.iloc[5, session.columns.get_indexer(["open", "high", "low", "close", "volume"])] = [
        99.9,
        100.7,
        99.8,
        100.6,
        3000,
    ]
    session.loc[session.index[5], ["vwap", "tod_rvol"]] = [100.2, 3]
    at = session.index[6]
    observed = evaluate_trigger(candidate, session, at, contract)
    assert observed.decision == "TRADE"
    random = null_decision(candidate, session, at, contract)
    assert label_trade(candidate, observed, session, risk, costs, contract) == label_trade(
        candidate, random, session, risk, costs, contract
    )


def test_grid_uses_shared_labeler_and_entry_relative_horizon():
    candidate, session, contract, risk, costs = fixture()
    grid = counterfactual_grid(candidate, session, risk=risk, costs=costs, contract=contract)
    assert len(grid) == 101 and set(grid.status) == {"resolved"}
    assert set(grid.minutes_held) == {3.0}
    assert set(grid.outcome) == {"time_exit"}
    assert (grid.net_r < 0).all()


def _grid():
    return pd.DataFrame(
        [
            {
                "event_id": event,
                "session_date": day,
                "slot": slot,
                "status": "resolved",
                "net_r": slot - 1.0,
                "strict_success": slot == 2,
            }
            for event, day in [("a", "2026-09-17"), ("b", "2026-09-17"), ("c", "2026-09-18")]
            for slot in range(3)
        ]
    )


def test_seed_and_population_order_are_reproducible_with_shared_session_times():
    first = sample_cohorts(_grid(), n_cohorts=20, seed=1, expected_slot_count=3)
    second = sample_cohorts(
        _grid().sample(frac=1, random_state=42), n_cohorts=20, seed=1, expected_slot_count=3
    )
    pd.testing.assert_frame_equal(first, second)
    assert first.groupby(["cohort_id", "session_date"]).slot.nunique().max() == 1
    assert first.groupby("cohort_id").event_id.nunique().eq(3).all()


@pytest.mark.parametrize("failure", ["unresolved", "missing_slot", "duplicate", "multiple_dates"])
def test_never_selects_only_clean_or_profitable_paths(failure):
    grid = _grid()
    if failure == "unresolved":
        grid.loc[0, "status"] = "insufficient_execution_horizon"
    elif failure == "missing_slot":
        grid = grid.iloc[1:]
    elif failure == "duplicate":
        grid = pd.concat([grid, grid.iloc[:1]])
    else:
        grid.loc[0, "session_date"] = "2026-09-19"
    with pytest.raises(ValueError):
        sample_cohorts(grid, n_cohorts=1, seed=5, expected_slot_count=3)


def test_missing_last_slot_for_every_event_still_blocks_sampling():
    grid = _grid()
    with pytest.raises(ValueError, match="every slot"):
        sample_cohorts(grid.loc[grid.slot < 2], n_cohorts=1, seed=5, expected_slot_count=3)


def test_saved_observed_outcome_must_replay_exactly():
    saved = pd.Series({"event_id": "a", "status": "resolved", "net_r": 0.1, "strict_success": True})
    _assert_replay(saved, {"status": "resolved", "net_r": 0.1, "strict_success": True})
    with pytest.raises(ValueError, match="observed_replay_mismatch"):
        _assert_replay(saved, {"net_r": 0.2})


def test_incomplete_session_cannot_define_the_random_pool():
    candidate, session, contract, risk, costs = fixture()
    with pytest.raises(ValueError, match="incomplete_counterfactual_session"):
        counterfactual_grid(candidate, session.iloc[:-1], risk=risk, costs=costs, contract=contract)


@pytest.mark.parametrize(
    "failure", ["missing_latest", "missing_manifest", "bad_json", "missing_key"]
)
def test_invalid_saved_inputs_have_durable_blocked_reports(tmp_path, failure):
    dataset_id = "a" * 32
    folder = tmp_path / "aem/datasets" / dataset_id
    folder.mkdir(parents=True)
    if failure in {"bad_json", "missing_key"}:
        (folder / "manifest.json").write_text("{" if failure == "bad_json" else "{}")
        pd.DataFrame({"event_id": ["one"]}).to_csv(folder / "events.csv", index=False)
    report = run_benchmark(
        root=tmp_path,
        output=tmp_path,
        dataset_id=None if failure == "missing_latest" else dataset_id,
        n_cohorts=2,
    )
    assert report["status"] == "blocked_invalid_or_unavailable_source"
    assert report["eligible_for_live"] is False and "comparison" not in report
    saved = list((tmp_path / "aem_benchmark/runs").glob("*/report.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == report


def test_removing_observed_events_cannot_change_the_comparison_pool(tmp_path):
    dataset_id = "a" * 32
    folder = tmp_path / "aem/datasets" / dataset_id
    folder.mkdir(parents=True)
    original = pd.DataFrame({"event_id": ["winner", "loser"], "net_r": [1.0, -1.0]})
    expected = hashlib.sha256(
        pd.util.hash_pandas_object(original, index=True).values.tobytes()
    ).hexdigest()
    (folder / "manifest.json").write_text(json.dumps({"dataset_sha256": expected, "events": 2}))
    original.iloc[:1].to_csv(folder / "events.csv", index=False)
    report = run_benchmark(root=tmp_path, output=tmp_path, dataset_id=dataset_id, n_cohorts=2)
    assert report["error"] == "dataset_event_population_hash_mismatch"
    assert "comparison" not in report
