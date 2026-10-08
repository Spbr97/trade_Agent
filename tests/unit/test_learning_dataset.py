from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from tests.unit.test_outcome_resolver import _bars, _row
from tradedesk.learning_dataset import (
    build_learning_dataset,
    build_timing_opportunities,
    register_dataset_use,
)
from tradedesk.prediction_ledger import seal_prediction
from tradedesk.signal_tracker import resolve_outcomes


def _resolved_record(*, market: str = "nse", signal_id: str | None = None):  # type: ignore[no-untyped-def]
    row = _row(market=market)
    if signal_id is not None:
        payload = dict(row.prediction_payload or {})
        payload["signal_id"] = signal_id
        row.signal_id = signal_id
        row.prediction_payload = payload
        row.prediction_sha256 = seal_prediction(payload)

    class Store:
        def load(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return _bars([(99, 104, 98, 103)])

    resolve_outcomes(Store(), {row.signal_id: row}, 99)  # type: ignore[arg-type]
    return asdict(row)


def _reseal(record, **payload_updates):  # type: ignore[no-untyped-def]
    payload = dict(record["prediction_payload"])
    payload.update(payload_updates)
    record["prediction_payload"] = payload
    record["prediction_sha256"] = seal_prediction(payload)
    return record


def test_dataset_is_deterministic_and_uses_prediction_time_features_only() -> None:
    second = _resolved_record(signal_id="second")
    first = _resolved_record(signal_id="first")
    a = build_learning_dataset([second, first], market="nse", purpose="development")
    b = build_learning_dataset([first, second], market="nse", purpose="development")
    assert a.dataset_id == b.dataset_id
    assert a.rows == b.rows
    assert [row["signal_id"] for row in a.rows] == ["first", "second"]
    features = a.rows[0]["features"]
    assert features
    assert not any(name.startswith(("outcome", "exit", "label")) for name in features)
    assert a.version == "self-learning-dataset-v4"
    timing = a.rows[0]["timing_spec"]
    assert timing["scrip_code"] == a.rows[0]["scrip_code"]
    assert seal_prediction(timing["prediction_payload"]) == a.rows[0]["prediction_sha256"]


def test_rejected_and_shadow_mature_calls_are_counterfactual_evidence() -> None:
    rejected = _resolved_record(signal_id="rejected")
    rejected["evidence_class"] = "rejected_call"
    rejected["rejected_for"] = ["score below gate"]
    payload = rejected["prediction_payload"]
    payload["evidence_class"] = "rejected_call"
    payload["decision"]["rejected_for"] = ["score below gate"]
    rejected["prediction_sha256"] = seal_prediction(payload)
    shadow = _resolved_record(signal_id="shadow")
    shadow["evidence_class"] = "shadow_call"
    shadow["shadow"] = True
    payload = shadow["prediction_payload"]
    payload["evidence_class"] = "shadow_call"
    shadow["prediction_sha256"] = seal_prediction(payload)
    dataset = build_learning_dataset([rejected, shadow], market="nse", purpose="development")
    assert len(dataset.rows) == 2
    assert {row["evidence_role"] for row in dataset.rows} == {"counterfactual"}


def test_exclusions_are_explicit_and_invalid_never_enters_learning() -> None:
    pending = _resolved_record(signal_id="pending")
    pending.update(outcome=None, outcome_state="pending_call", label=None)
    invalid = _resolved_record(signal_id="invalid")
    invalid.update(outcome="unavailable", outcome_state="invalid_call", label=None)
    never = _resolved_record(signal_id="never")
    never.update(outcome="never_triggered", outcome_state="never_triggered", label=None)
    legacy = _resolved_record(signal_id="legacy")
    legacy.update(
        ledger_schema_version="legacy-unsealed-v0",
        prediction_payload=None,
        prediction_sha256=None,
    )
    dataset = build_learning_dataset(
        [pending, invalid, never, legacy], market="nse", purpose="development"
    )
    assert dataset.rows == ()
    assert dataset.exclusions == {
        "invalid": 1,
        "legacy_unsealed": 1,
        "never_triggered": 1,
        "pending": 1,
    }


def test_timing_population_retains_all_sealed_ex_ante_outcome_states() -> None:
    resolved = _resolved_record(signal_id="resolved")
    never = _resolved_record(signal_id="never")
    never.update(
        outcome="never_triggered",
        outcome_state="never_triggered",
        label=None,
        entry_on=None,
        actual_entry_price=None,
        net_r=None,
    )
    pending = _resolved_record(signal_id="pending")
    pending.update(
        outcome=None,
        outcome_state="pending_call",
        label=None,
        entry_on=None,
        actual_entry_price=None,
        exit_on=None,
        exit_price=None,
        net_r=None,
    )
    invalid = _resolved_record(signal_id="invalid")
    invalid.update(
        outcome="unavailable",
        outcome_state="invalid_call",
        label=None,
        net_r=None,
    )
    opportunities = build_timing_opportunities(
        [resolved, never, pending, invalid], market="nse", purpose="prospective"
    )
    assert [row["signal_id"] for row in opportunities] == [
        "invalid",
        "never",
        "pending",
        "resolved",
    ]
    states = {row["signal_id"]: row["outcome_state"] for row in opportunities}
    assert states == {
        "invalid": "invalid_call",
        "never": "never_triggered",
        "pending": "pending_call",
        "resolved": "resolved_call",
    }


def test_prospective_excludes_backfill_and_markets_cannot_pool() -> None:
    live = _resolved_record(signal_id="live")
    backfill = _resolved_record(signal_id="backfill")
    backfill["source"] = "backfill"
    _reseal(backfill, source="backfill")
    other_market = _resolved_record(market="crypto", signal_id="crypto")
    dataset = build_learning_dataset(
        [live, backfill, other_market], market="nse", purpose="prospective"
    )
    assert [row["signal_id"] for row in dataset.rows] == ["live"]
    assert dataset.exclusions == {"backfill_not_prospective": 1, "market_mismatch": 1}


def test_duplicate_predictions_are_not_reused() -> None:
    row = _resolved_record(signal_id="same")
    dataset = build_learning_dataset([row, dict(row)], market="nse", purpose="development")
    assert len(dataset.rows) == 1
    assert dataset.exclusions == {"duplicate_signal_id": 1}


def test_locked_outcomes_cannot_be_reused_by_later_dataset(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dataset = build_learning_dataset(
        [_resolved_record(signal_id="locked")], market="nse", purpose="locked_test"
    )
    registry = tmp_path / "uses.jsonl"
    register_dataset_use(dataset, registry)
    later = replace(dataset, dataset_id="different", purpose="development")
    with pytest.raises(ValueError, match="locked outcome reuse refused"):
        register_dataset_use(later, registry)
