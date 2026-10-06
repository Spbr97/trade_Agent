from __future__ import annotations

import pytest

from tradedesk.promotion_control import (
    approve_promotion,
    build_promotion_review,
    challenger_reproduction_sha256,
    monitor_promotion_health,
    register_prospective_cohort,
    rollback_promotion,
    seal_prospective_result,
    validate_promotion_record,
)


def _challenger(experiment_id: str = "experiment-1") -> dict:  # type: ignore[type-arg]
    return {
        "experiment_id": experiment_id,
        "dataset_id": "dataset-1",
        "market": "nse",
        "configuration": {"learner": "logistic"},
        "cohorts": {
            "contract_versions": ["quick-profit-v1"],
            "model_versions": ["active-v1"],
            "strategy_versions": ["strategy-v1"],
            "feature_versions": ["features-v1"],
        },
        "features": ["decision.rule_score"],
        "model": {"intercept": 0.1, "coefficients": {"decision.rule_score": 1.0}},
        "scores": {
            "chronological_test": {
                "frozen_baseline": {"accuracy": 0.6},
                "challenger": {"accuracy": 0.82},
            }
        },
        "precision_selector": {
            "policy": {"version": "precision-selector-v1"},
            "selected_live_policy": "confidence_80",
        },
        "final_evidence_gates": {
            "sample_threshold_reached": True,
            "any_preregistered_selector_policy_clears_accuracy_and_net_gate": True,
            "matched_random_timing_margin_passed": True,
            "prospective_cohort_passed": False,
            "all_passed": False,
        },
    }


def _passing_metrics() -> dict:  # type: ignore[type-arg]
    return {
        "resolved_calls": 120,
        "wins": 100,
        "sessions": 40,
        "mean_net_r": 0.25,
        "mean_after_tax_r": 0.18,
        "random_timing_margin_r": 0.12,
        "integrity_passed": True,
    }


def _review(tmp_path, challenger):  # type: ignore[no-untyped-def]
    cohort = register_prospective_cohort(
        challenger,
        tmp_path / f"{challenger['experiment_id']}-cohort.json",
        starts_after="2026-10-06",
    )
    result = seal_prospective_result(
        cohort, _passing_metrics(), tmp_path / f"{challenger['experiment_id']}-result.json"
    )
    digest = challenger_reproduction_sha256(challenger)
    return build_promotion_review(
        challenger,
        cohort,
        result,
        digest,
        tmp_path / f"{challenger['experiment_id']}-review.json",
    )


def test_prospective_registration_refuses_incomplete_historical_gates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    challenger = _challenger()
    challenger["final_evidence_gates"]["matched_random_timing_margin_passed"] = False
    with pytest.raises(ValueError, match="matched_random_timing_margin_passed"):
        register_prospective_cohort(challenger, tmp_path / "cohort.json", starts_after="x")


def test_promotion_requires_passing_forward_evidence_reproduction_and_explicit_approval(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    review = _review(tmp_path, _challenger())
    assert review["status"] == "awaiting_explicit_user_approval"
    assert review["promotion_authorized"] is False
    with pytest.raises(PermissionError, match="explicit user approval"):
        approve_promotion(
            review,
            tmp_path / "promotion.json",
            explicit_user_approval=False,
            approval_reference="",
        )
    promotion = approve_promotion(
        review,
        tmp_path / "promotion.json",
        explicit_user_approval=True,
        approval_reference="user-message-123",
    )
    validate_promotion_record(promotion)
    assert promotion["status"] == "approved"
    assert promotion["approved_by_user"] is True


def test_health_monitor_pauses_on_stale_drift_or_contract_mismatch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    review = _review(tmp_path, _challenger())
    promotion = approve_promotion(
        review,
        tmp_path / "promotion.json",
        explicit_user_approval=True,
        approval_reference="user-message-123",
    )
    paused = monitor_promotion_health(
        promotion, data_fresh=False, drift_detected=True, contract_matches=False
    )
    validate_promotion_record(paused)
    assert paused["status"] == "paused"
    assert set(paused["pause_reasons"]) == {
        "stale_data",
        "performance_or_calibration_drift",
        "contract_mismatch",
    }


def test_rollback_restores_the_previous_verified_promotion(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "promotion.json"
    first = approve_promotion(
        _review(tmp_path, _challenger("experiment-1")),
        path,
        explicit_user_approval=True,
        approval_reference="user-first",
    )
    second = approve_promotion(
        _review(tmp_path, _challenger("experiment-2")),
        path,
        explicit_user_approval=True,
        approval_reference="user-second",
    )
    assert second["previous_promotion"]["experiment_id"] == first["experiment_id"]
    with pytest.raises(PermissionError, match="explicit user request"):
        rollback_promotion(second, path, explicit_user_request=False)
    restored = rollback_promotion(second, path, explicit_user_request=True)
    validate_promotion_record(restored)
    assert restored["experiment_id"] == first["experiment_id"]
    assert restored["rollback_from_experiment_id"] == second["experiment_id"]


def test_tampered_promotion_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    promotion = approve_promotion(
        _review(tmp_path, _challenger()),
        tmp_path / "promotion.json",
        explicit_user_approval=True,
        approval_reference="user-message-123",
    )
    promotion["experiment_id"] = "tampered"
    with pytest.raises(ValueError, match="integrity hash"):
        validate_promotion_record(promotion)
