from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from tradedesk.engine.indicators import atr
from tradedesk.evidence import OutcomeState
from tradedesk.outcome_resolver import resolve_versioned_call
from tradedesk.precision_selector import PRIMARY_SELECTOR_POLICY, SELECTOR_VERSION
from tradedesk.prediction_ledger import canonical_sha256, seal_prediction
from tradedesk.random_timing_control import (
    N_COHORTS,
    SELECTION_CONTRACT_VERSION,
    SELECTION_MANIFEST_VERSION,
    accumulate_random_timing_evidence,
    control_configuration,
    evaluate_matched_random_timing,
)

MARKET = "nse"
CONTRACT_VERSION = "quick-profit-v1"
CODE = "NSE_TEST"
SETUP = "breakout"
REGIME = "trend"
STRATEGY_VERSION = "strategy-v1"
FEATURE_VERSION = "feature-v1"
MODEL_VERSION = "base-model-v1"
CONTRACT_SHA256 = "a" * 64


@dataclass(frozen=True)
class TimingFixture:
    frame: pd.DataFrame
    historical: list[dict[str, Any]]
    actual: list[dict[str, Any]]
    starts_after: str


def _seal_object(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "record_sha256": canonical_sha256(payload)}


def _selection_context(starts_after: str, *, identity: str = "primary") -> dict[str, Any]:
    return _seal_object(
        {
            "version": SELECTION_CONTRACT_VERSION,
            "market": MARKET,
            "contract_version": CONTRACT_VERSION,
            "selector_policy": PRIMARY_SELECTOR_POLICY,
            "selector_version": SELECTOR_VERSION,
            "control_configuration_sha256": canonical_sha256(control_configuration()),
            "starts_after": starts_after,
            "frozen_model_identity": identity,
            "source_cohort": {
                "contract_version": CONTRACT_VERSION,
                "contract_sha256": CONTRACT_SHA256,
                "strategy_version": STRATEGY_VERSION,
                "feature_version": FEATURE_VERSION,
                "model_version": MODEL_VERSION,
            },
        }
    )


def _selection_manifest(
    rows: list[dict[str, Any]], context: dict[str, Any]
) -> dict[str, Any]:
    return _seal_object(
        {
            "version": SELECTION_MANIFEST_VERSION,
            "market": MARKET,
            "contract_version": CONTRACT_VERSION,
            "selection_context_sha256": context["record_sha256"],
            "selected_signal_ids": [str(row["signal_id"]) for row in rows],
            "selected_sessions": [str(row["armed_on"]) for row in rows],
        }
    )


def _base_frame(periods: int) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=periods, freq="B", tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.5,
            "low": 99.5,
            "close": 100.0,
            "volume": 1_000_000.0,
        },
        index=index,
    )


def _prediction_payload(
    signal_id: str, armed_on: str, source_atr: float
) -> dict[str, Any]:
    source_snapshot = {
        "as_of_session": armed_on,
        "source_feature_row": {
            "atr": source_atr,
            "atr14": source_atr,
            "close": 100.0,
        },
    }
    return {
        "schema_version": "prediction-ledger-v1",
        "signal_id": signal_id,
        "market": MARKET,
        "instrument": {"scrip_code": CODE, "symbol": CODE},
        "contract": {"version": CONTRACT_VERSION, "sha256": CONTRACT_SHA256},
        "versions": {
            "strategy": STRATEGY_VERSION,
            "feature_contract": FEATURE_VERSION,
            "model": MODEL_VERSION,
        },
        "levels": {
            "armed_on": armed_on,
            "entry_range": [101.0, 101.0],
            "stop": 100.0,
            "targets": [101.75, 101.75],
            "chased_atr_multiple": 1.0,
        },
        "execution": {
            "quantity": 100.0,
            "risk_amount": 100.0,
            "position_value": 10_100.0,
            "assumptions": {"slippage_pct": 0.0},
        },
        "source_snapshot": source_snapshot,
        "source_snapshot_sha256": canonical_sha256(source_snapshot),
    }


def _opportunity(
    frame: pd.DataFrame, *, signal_id: str, armed_index: int
) -> dict[str, Any]:
    armed_on = frame.index[armed_index].date().isoformat()
    source_atr = float(atr(frame.iloc[: armed_index + 1], 14).iloc[-1])
    payload = _prediction_payload(signal_id, armed_on, source_atr)
    outcome = resolve_versioned_call(
        SimpleNamespace(
            prediction_payload=payload,
            market=MARKET,
            contract_version=CONTRACT_VERSION,
            armed_on=armed_on,
            entry=101.0,
            stop=100.0,
        ),
        frame,
    )
    assert outcome is not None
    return {
        "signal_id": signal_id,
        "prediction_sha256": seal_prediction(payload),
        "market": MARKET,
        "scrip_code": CODE,
        "setup": SETUP,
        "regime": REGIME,
        "contract_version": CONTRACT_VERSION,
        "contract_sha256": CONTRACT_SHA256,
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
        "model_version": MODEL_VERSION,
        "evidence_role": "recommended",
        "armed_on": armed_on,
        "outcome_state": outcome.outcome_state,
        "outcome": outcome.outcome,
        "label": outcome.label,
        "entry_on": outcome.entry_on,
        "actual_entry_price": outcome.entry_price,
        "exit_on": outcome.exit_on,
        "exit_price": outcome.exit_price,
        "net_r": outcome.net_r,
        "timing_spec": {
            "scrip_code": CODE,
            "entry": 101.0,
            "stop": 100.0,
            "t1": 101.75,
            "t2": 101.75,
            "prediction_payload": payload,
        },
    }


def _fixture(*, actual_count: int, historical_count: int = N_COHORTS) -> TimingFixture:
    historical_indices = [30 + 5 * offset for offset in range(historical_count)]
    boundary_index = historical_indices[-1] + 5
    actual_indices = [boundary_index + 10 + 5 * offset for offset in range(actual_count)]
    frame = _base_frame(actual_indices[-1] + 12)

    for armed_index in actual_indices:
        next_index = armed_index + 1
        frame.iloc[next_index, frame.columns.get_loc("open")] = 101.0
        frame.iloc[next_index, frame.columns.get_loc("high")] = 102.0
        frame.iloc[next_index, frame.columns.get_loc("low")] = 100.5
        frame.iloc[next_index, frame.columns.get_loc("close")] = 101.5

    historical = [
        _opportunity(frame, signal_id=f"history-{offset:03d}", armed_index=armed_index)
        for offset, armed_index in enumerate(historical_indices)
    ]
    actual = [
        _opportunity(frame, signal_id=f"actual-{offset:03d}", armed_index=armed_index)
        for offset, armed_index in enumerate(actual_indices)
    ]
    assert all(
        row["outcome_state"] == OutcomeState.NEVER_TRIGGERED.value
        for row in historical
    )
    assert all(row["net_r"] == 0.75 for row in actual)
    return TimingFixture(
        frame=frame,
        historical=historical,
        actual=actual,
        starts_after=frame.index[boundary_index].date().isoformat(),
    )


def _evaluate(
    fixture: TimingFixture,
    rows: list[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
    historical: list[dict[str, Any]] | None = None,
    frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    frozen_context = context or _selection_context(fixture.starts_after)
    return evaluate_matched_random_timing(
        rows,
        historical_opportunities=historical or fixture.historical,
        market=MARKET,
        contract_version=CONTRACT_VERSION,
        selection_context=frozen_context,
        selection_manifest=_selection_manifest(rows, frozen_context),
        bars_by_code={CODE: frame if frame is not None else fixture.frame},
    )


def _reseal_report(report: dict[str, Any]) -> None:
    report["replay_sha256"] = canonical_sha256(
        {key: value for key, value in report.items() if key != "replay_sha256"}
    )


def test_exact_twenty_matched_prior_calls_can_pass_fixed_terminal_gate() -> None:
    fixture = _fixture(actual_count=100)

    report = _evaluate(fixture, fixture.actual)

    assert report["status"] == "random_timing_pass"
    assert report["selector_policy"] == PRIMARY_SELECTOR_POLICY
    assert report["paired_calls"] == 100
    assert report["active_sessions"] == 100
    assert report["actual_fills"] == 100
    assert report["actual_no_fills"] == 0
    assert report["model_expectancy_r"] == 0.75
    assert report["matched_prior_timing_expectancy_r_mean"] == 0.0
    assert report["timing_advantage_r"] == 0.75
    assert report["empirical_rank_tail_probability"] == 1 / (N_COHORTS + 1)
    assert report["p_value"] is None
    assert report["inferential_p_value_available"] is False
    assert report["random_timing_gate_passed"] is True
    assert report["terminal_evaluation_locked"] is True
    assert report["markets_pooled"] is False
    assert report["contracts_pooled"] is False

    expected_controls = [row["signal_id"] for row in reversed(fixture.historical)]
    for record in report["records"]:
        assert [item["rank"] for item in record["alternatives"]] == list(
            range(1, N_COHORTS + 1)
        )
        assert [item["signal_id"] for item in record["alternatives"]] == expected_controls
        assert record["actual_outcome_state"] == OutcomeState.RESOLVED_CALL.value
        assert record["record_sha256"]
        assert all(
            alternative["exit_on"] < record["armed_on"]
            for alternative in record["alternatives"]
        )


def test_source_and_recorded_outcome_tampering_fail_closed() -> None:
    fixture = _fixture(actual_count=1)

    outcome_tamper = deepcopy(fixture.actual)
    outcome_tamper[0]["net_r"] = 99.0
    outcome_report = _evaluate(fixture, outcome_tamper)
    assert outcome_report["status"] == "invalid_source"
    assert "actual candle replay mismatch: net_r" in outcome_report["reason"]

    source_tamper = fixture.frame.copy()
    armed = pd.Timestamp(fixture.actual[0]["armed_on"]).date()
    armed_index = list(source_tamper.index.date).index(armed)
    source_tamper.iloc[armed_index, source_tamper.columns.get_loc("close")] = 100.25
    source_report = _evaluate(fixture, fixture.actual, frame=source_tamper)
    assert source_report["status"] == "invalid_source"
    assert "sealed armed-session close/ATR" in source_report["reason"]


def test_pending_matched_call_collects_without_substituting_older_history() -> None:
    fixture = _fixture(actual_count=1, historical_count=N_COHORTS + 1)
    history = deepcopy(fixture.historical)
    history[-1]["outcome_state"] = OutcomeState.PENDING_CALL.value

    report = _evaluate(fixture, fixture.actual, historical=history)

    assert report["status"] == "collecting_insufficient_evidence"
    assert report["paired_calls"] == 0
    assert report["awaiting_selected_outcomes"] == 1
    assert report["insufficient_matched_history"] == 0
    assert report["records"] == []


def test_selection_manifest_and_one_per_session_are_enforced() -> None:
    fixture = _fixture(actual_count=2)
    context = _selection_context(fixture.starts_after)
    incomplete_manifest = _selection_manifest([], context)

    with pytest.raises(ValueError, match="manifest does not match selected rows"):
        evaluate_matched_random_timing(
            fixture.actual[:1],
            historical_opportunities=fixture.historical,
            market=MARKET,
            contract_version=CONTRACT_VERSION,
            selection_context=context,
            selection_manifest=incomplete_manifest,
            bars_by_code={CODE: fixture.frame},
        )

    duplicate_session = [fixture.actual[0], fixture.actual[0]]
    with pytest.raises(ValueError, match="duplicate armed sessions"):
        evaluate_matched_random_timing(
            duplicate_session,
            historical_opportunities=fixture.historical,
            market=MARKET,
            contract_version=CONTRACT_VERSION,
            selection_context=context,
            selection_manifest=_selection_manifest(duplicate_session, context),
            bars_by_code={CODE: fixture.frame},
        )


def test_accumulator_reaches_60_plus_40_and_freezes_terminal_cohort(
    tmp_path: Path,
) -> None:
    fixture = _fixture(actual_count=105)
    context = _selection_context(fixture.starts_after)
    first_batch = _evaluate(fixture, fixture.actual[:60], context=context)
    second_batch = _evaluate(fixture, fixture.actual[60:100], context=context)
    post_terminal_batch = _evaluate(fixture, fixture.actual[100:], context=context)
    ledger = tmp_path / "timing-evidence.json"

    first = accumulate_random_timing_evidence(first_batch, ledger)
    assert first["status"] == "collecting_insufficient_evidence"
    assert first["paired_calls"] == 60
    assert first["new_pairs_added"] == 60

    pending_rows = deepcopy(fixture.actual[100:101])
    pending_rows[0]["outcome_state"] = OutcomeState.PENDING_CALL.value
    pending_batch = _evaluate(fixture, pending_rows, context=context)
    retained = accumulate_random_timing_evidence(pending_batch, ledger)
    assert retained["paired_calls"] == 60
    assert retained["awaiting_selected_outcomes"] == 1
    assert retained["evidence_ledger_sha256"] == first["evidence_ledger_sha256"]

    other_context = _selection_context(fixture.starts_after, identity="different-model")
    wrong_context_batch = _evaluate(
        fixture, fixture.actual[100:101], context=other_context
    )
    with pytest.raises(ValueError, match="scope/configuration mismatch"):
        accumulate_random_timing_evidence(wrong_context_batch, ledger)

    duplicate_session_batch = deepcopy(first_batch)
    duplicate_record = deepcopy(first_batch["records"][0])
    duplicate_record["signal_id"] = "different-signal-same-session"
    duplicate_record["record_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in duplicate_record.items()
            if key != "record_sha256"
        }
    )
    duplicate_session_batch["records"] = [duplicate_record]
    _reseal_report(duplicate_session_batch)
    with pytest.raises(ValueError, match="second top-one winner"):
        accumulate_random_timing_evidence(duplicate_session_batch, ledger)

    terminal = accumulate_random_timing_evidence(second_batch, ledger)
    terminal_ids = [record["signal_id"] for record in terminal["records"]]
    assert terminal["status"] == "random_timing_pass"
    assert terminal["paired_calls"] == 100
    assert terminal["new_pairs_added"] == 40
    assert terminal["terminal_evaluation_locked"] is True
    terminal_ledger_sha = terminal["evidence_ledger_sha256"]

    frozen = accumulate_random_timing_evidence(post_terminal_batch, ledger)
    assert frozen["status"] == "random_timing_pass"
    assert frozen["paired_calls"] == 100
    assert frozen["observed_pairs_total"] == 100
    assert frozen["new_pairs_added"] == 0
    assert frozen["terminal_batch_ignored"] is True
    assert frozen["evidence_ledger_sha256"] == terminal_ledger_sha
    assert [record["signal_id"] for record in frozen["records"]] == terminal_ids
