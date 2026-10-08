from __future__ import annotations

import pytest

from tradedesk.precision_selector import SELECTOR_VERSION
from tradedesk.prediction_ledger import canonical_sha256
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
from tradedesk.random_timing_control import (
    CONTROL_VERSION as RANDOM_TIMING_CONTROL_VERSION,
)
from tradedesk.random_timing_control import summarize_random_timing_records


def _passing_timing() -> dict:  # type: ignore[type-arg]
    context_sha = "c" * 64
    records = []
    for index in range(100):
        record = {
            "control_version": RANDOM_TIMING_CONTROL_VERSION,
            "market": "nse",
            "contract_version": "quick-profit-v1",
            "selector_policy": "top_1_per_session",
            "selection_context_sha256": context_sha,
            "selection_manifest_sha256": "b" * 64,
            "signal_id": f"signal-{index:03d}",
            "prediction_sha256": f"prediction-{index:03d}",
            "scrip_code": "NSE_TEST",
            "armed_on": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
            "actual_outcome_state": "resolved_call",
            "actual_outcome": "target",
            "actual_entry_on": "2026-01-02",
            "actual_exit_on": "2026-01-02",
            "actual_net_r": 0.5,
            "alternatives": [
                {
                    "rank": rank,
                    "signal_id": f"control-{rank:02d}",
                    "exit_on": "2025-12-31",
                    "net_r": 0.0,
                    "source_sha256": canonical_sha256(
                        {"control_rank": rank}
                    ),
                }
                for rank in range(1, 21)
            ],
            "source_sha256": canonical_sha256({"source": index}),
        }
        record["record_sha256"] = canonical_sha256(record)
        records.append(record)
    timing = summarize_random_timing_records(
        records,
        market="nse",
        contract_version="quick-profit-v1",
        selection_context_sha256=context_sha,
    )
    timing.update(
        evidence_mode="append_only_single_frozen_selector_terminal_cohort",
        evidence_ledger_sha256="d" * 64,
        batches=5,
        new_pairs_added=20,
        batch_replay_sha256="e" * 64,
    )
    timing["replay_sha256"] = canonical_sha256(
        {key: value for key, value in timing.items() if key != "replay_sha256"}
    )
    return timing


def _challenger(experiment_id: str = "experiment-1") -> dict:  # type: ignore[type-arg]
    timing = _passing_timing()
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
            "version": SELECTOR_VERSION,
            "market": "nse",
            "policy": {"version": SELECTOR_VERSION, "market": "nse"},
            "primary_policy": "top_1_per_session",
            "selected_live_policy": None,
            "operating_points": {
                "top_1_per_session": {
                    "selected": 100,
                    "strict_accuracy": 0.82,
                    "wilson_95_low": 0.73,
                    "mean_net_r": 0.2,
                    "passes_final_accuracy_gate": True,
                }
            },
        },
        "controls": {"random_timing": timing},
        "timing_selection": {"context_sha256": timing["selection_context_sha256"]},
        "final_evidence_gates": {
            "sample_threshold_reached": True,
            "primary_selector_policy_clears_accuracy_and_net_gate": True,
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


def test_prospective_registration_binds_exact_random_timing_evidence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    challenger = _challenger()
    cohort = register_prospective_cohort(
        challenger, tmp_path / "cohort.json", starts_after="2026-10-06"
    )
    timing = challenger["controls"]["random_timing"]

    assert cohort["frozen_random_timing_evidence"] == timing
    assert cohort["random_timing_evidence_sha256"] == canonical_sha256(timing)
    assert cohort["frozen_final_evidence_gates"] == challenger["final_evidence_gates"]

    tampered = dict(cohort)
    tampered["frozen_random_timing_evidence"] = {
        **timing,
        "timing_advantage_r": 9.9,
    }
    with pytest.raises(ValueError, match="integrity hash"):
        seal_prospective_result(tampered, _passing_metrics(), tmp_path / "result.json")


def test_prospective_registration_rejects_missing_or_tampered_replay_digest(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    missing = _challenger()
    missing["controls"]["random_timing"].pop("replay_sha256")
    with pytest.raises(ValueError, match="replay digest"):
        register_prospective_cohort(
            missing, tmp_path / "missing.json", starts_after="2026-10-06"
        )

    tampered = _challenger()
    tampered["controls"]["random_timing"]["timing_advantage_r"] = 9.9
    with pytest.raises(ValueError, match="replay digest mismatch"):
        register_prospective_cohort(
            tampered, tmp_path / "tampered.json", starts_after="2026-10-06"
        )


def test_prospective_registration_rejects_selector_or_timing_context_mismatch(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    wrong_context = _challenger()
    wrong_context["timing_selection"]["context_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="selection-context mismatch"):
        register_prospective_cohort(
            wrong_context, tmp_path / "context.json", starts_after="2026-10-06"
        )

    wrong_selector = _challenger()
    wrong_selector["precision_selector"]["version"] = "obsolete-selector"
    with pytest.raises(ValueError, match="selector identity/policy mismatch"):
        register_prospective_cohort(
            wrong_selector, tmp_path / "selector.json", starts_after="2026-10-06"
        )

    false_score = _challenger()
    false_score["precision_selector"]["operating_points"][
        "top_1_per_session"
    ]["strict_accuracy"] = 0.5
    with pytest.raises(ValueError, match="primary selector evidence is incomplete"):
        register_prospective_cohort(
            false_score, tmp_path / "score.json", starts_after="2026-10-06"
        )


def test_promotion_review_rejects_resealed_timing_change_after_registration(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    challenger = _challenger()
    cohort = register_prospective_cohort(
        challenger, tmp_path / "cohort.json", starts_after="2026-10-06"
    )
    result = seal_prospective_result(
        cohort, _passing_metrics(), tmp_path / "result.json"
    )
    timing = challenger["controls"]["random_timing"]
    timing["timing_advantage_r"] = 0.99
    timing.pop("replay_sha256")
    timing["replay_sha256"] = canonical_sha256(timing)
    changed_digest = challenger_reproduction_sha256(challenger)

    review = build_promotion_review(
        challenger,
        cohort,
        result,
        changed_digest,
        tmp_path / "review.json",
    )
    assert review["status"] == "blocked"
    assert "independent reproduction hash mismatch" in review["blockers"]


def test_promotion_review_rejects_final_gate_change_after_registration(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    challenger = _challenger()
    cohort = register_prospective_cohort(
        challenger, tmp_path / "cohort.json", starts_after="2026-10-06"
    )
    result = seal_prospective_result(
        cohort, _passing_metrics(), tmp_path / "result.json"
    )
    challenger["final_evidence_gates"]["all_passed"] = True
    changed_digest = challenger_reproduction_sha256(challenger)

    review = build_promotion_review(
        challenger,
        cohort,
        result,
        changed_digest,
        tmp_path / "review.json",
    )
    assert review["status"] == "blocked"
    assert "independent reproduction hash mismatch" in review["blockers"]


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
