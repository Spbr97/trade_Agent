from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tradedesk.evidence import (
    CONTRACTS,
    QUICK_PROFIT_V1,
    SWING_V1,
    ContractKind,
    EvidenceClass,
    OutcomeState,
    classify_evidence,
    classify_outcome,
    enrich_call_record,
    is_trainable_outcome,
)


def test_recommendation_authority_is_separate_from_lifecycle() -> None:
    assert classify_evidence([], shadow=False) is EvidenceClass.QUALIFIED_CALL
    assert classify_evidence(["score below gate"], shadow=False) is EvidenceClass.REJECTED_CALL
    assert classify_evidence(["retired"], shadow=True) is EvidenceClass.SHADOW_CALL

    assert classify_outcome(None) is OutcomeState.PENDING_CALL
    assert classify_outcome("target") is OutcomeState.RESOLVED_CALL
    assert classify_outcome("stop") is OutcomeState.RESOLVED_CALL
    assert classify_outcome("invalidated") is OutcomeState.NEVER_TRIGGERED
    assert classify_outcome("unavailable") is OutcomeState.INVALID_CALL
    assert classify_outcome("unknown_future_value") is OutcomeState.INVALID_CALL


def test_only_known_mature_price_outcomes_are_trainable() -> None:
    assert is_trainable_outcome("target")
    assert is_trainable_outcome("timeout")
    assert not is_trainable_outcome(None)
    assert not is_trainable_outcome("insufficient")
    assert not is_trainable_outcome("never_triggered")
    assert not is_trainable_outcome("unavailable")


def test_quick_and_swing_contracts_are_frozen_and_distinct() -> None:
    assert QUICK_PROFIT_V1.kind is ContractKind.QUICK_PROFIT
    assert QUICK_PROFIT_V1.target_r == 0.75
    assert QUICK_PROFIT_V1.max_hold_sessions == 3
    assert SWING_V1.kind is ContractKind.SWING
    assert SWING_V1.target_r == 2.0
    assert SWING_V1.max_hold_sessions == 10
    assert QUICK_PROFIT_V1.version != SWING_V1.version
    assert CONTRACTS[QUICK_PROFIT_V1.version] is QUICK_PROFIT_V1
    with pytest.raises(FrozenInstanceError):
        QUICK_PROFIT_V1.target_r = 1.0  # type: ignore[misc]


def test_historical_record_is_enriched_as_legacy_without_relabelling_it() -> None:
    record = enrich_call_record(
        {
            "signal_id": "x",
            "rejected_for": ["score below gate"],
            "shadow": False,
            "outcome": "stop",
        },
        market="crypto",
    )
    assert record["market"] == "crypto"
    assert record["evidence_class"] == EvidenceClass.REJECTED_CALL.value
    assert record["outcome_state"] == OutcomeState.RESOLVED_CALL.value
    assert record["contract_kind"] == ContractKind.LEGACY.value
    assert record["contract_version"] == "legacy-t1-tracker-v1"


def test_market_or_contract_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="cross-market"):
        enrich_call_record(
            {"market": "nse", "rejected_for": [], "outcome": None}, market="crypto"
        )
    with pytest.raises(ValueError, match="kind/version mismatch"):
        enrich_call_record(
            {
                "rejected_for": [],
                "outcome": None,
                "contract_kind": "quick_profit",
                "contract_version": "swing-v1",
            },
            market="nse",
        )


def test_unknown_evidence_class_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown evidence class"):
        enrich_call_record({"evidence_class": "looks_good", "outcome": None})
