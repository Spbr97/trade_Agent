from __future__ import annotations

from dataclasses import replace

import pytest
from tradedesk_lab.accuracy_program_contract import (
    DEFAULT_ACCURACY_PROGRAM,
    LEVEL_BY_ID,
    AccuracyProgramProtocol,
    EvidenceClass,
    evaluate_qualification_level,
)


def _passing_metrics(level_id: str) -> dict[str, float | bool]:
    metrics: dict[str, float | bool] = {}
    for gate in LEVEL_BY_ID[level_id].gates:
        if isinstance(gate.threshold, bool):
            metrics[gate.metric] = gate.threshold
        elif gate.comparison in {"gt", "gte", "eq"}:
            metrics[gate.metric] = float(gate.threshold) + (
                0.01 if gate.comparison == "gt" else 0.0
            )
        else:
            metrics[gate.metric] = float(gate.threshold) - (
                0.01 if gate.comparison == "lt" else 0.0
            )
    return metrics


def test_protocol_identity_is_stable_and_canonical_baseline_is_exact() -> None:
    protocol = AccuracyProgramProtocol()

    assert protocol.sha256 == DEFAULT_ACCURACY_PROGRAM.sha256
    assert protocol.canonical_strict_wins / protocol.canonical_resolved_fills == pytest.approx(
        protocol.canonical_strict_success_rate
    )
    assert len(protocol.scoreboards) == 2
    assert len(protocol.levels) == 5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_strict_success_rate", 0.50),
        ("canonical_baseline_name", "renamed_baseline"),
        ("max_signal_families_per_cycle", 4),
        ("max_mechanism_variants_per_cycle", 13),
        ("max_model_pipelines_per_surviving_family", 7),
        ("maximum_familywise_empirical_p", 0.10),
    ],
)
def test_frozen_protocol_cannot_be_weakened(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="frozen"):
        replace(DEFAULT_ACCURACY_PROGRAM, **{field: value})


def test_missing_evidence_is_not_available_and_never_passes() -> None:
    result = evaluate_qualification_level(
        "level_2_credible_improvement",
        scoreboard_id="aem_same_contract",
        evidence_class=EvidenceClass.LOCKED_HISTORICAL_OOS,
        metrics={},
    )

    assert result["passed"] is False
    assert all(
        gate["status"] == "not_available"
        for name, gate in result["gates"].items()
        if name != "evidence_class"
    )
    assert result["eligible_for_live"] is False


def test_wrong_evidence_class_fails_even_when_every_metric_passes() -> None:
    result = evaluate_qualification_level(
        "level_2_credible_improvement",
        scoreboard_id="aem_same_contract",
        evidence_class=EvidenceClass.CONSUMED_DEVELOPMENT,
        metrics=_passing_metrics("level_2_credible_improvement"),
    )

    assert result["passed"] is False
    assert result["gates"]["evidence_class"]["status"] == "fail"


def test_level_two_can_pass_but_never_authorizes_live_or_promotion() -> None:
    result = evaluate_qualification_level(
        "level_2_credible_improvement",
        scoreboard_id="new_quick_profit_contract",
        evidence_class=EvidenceClass.LOCKED_HISTORICAL_OOS,
        metrics=_passing_metrics("level_2_credible_improvement"),
    )

    assert result["passed"] is True
    assert result["promotion_review_allowed"] is False
    assert result["eligible_for_live"] is False


def test_level_four_only_allows_review_and_never_live_eligibility() -> None:
    result = evaluate_qualification_level(
        "level_4_prospective_70_80",
        scoreboard_id="new_quick_profit_contract",
        evidence_class=EvidenceClass.PROSPECTIVE_SHADOW,
        metrics=_passing_metrics("level_4_prospective_70_80"),
    )

    assert result["passed"] is True
    assert result["promotion_review_allowed"] is True
    assert result["eligible_for_live"] is False


@pytest.mark.parametrize("bad_value", [True, "0.5", float("nan"), float("inf")])
def test_invalid_numeric_evidence_is_rejected(bad_value: object) -> None:
    metrics = _passing_metrics("level_2_credible_improvement")
    metrics["strict_success_rate"] = bad_value  # type: ignore[assignment]

    with pytest.raises(ValueError):
        evaluate_qualification_level(
            "level_2_credible_improvement",
            scoreboard_id="aem_same_contract",
            evidence_class=EvidenceClass.LOCKED_HISTORICAL_OOS,
            metrics=metrics,
        )


def test_unknown_contract_coordinates_are_rejected() -> None:
    with pytest.raises(ValueError, match="scoreboard"):
        evaluate_qualification_level(
            "level_0_integrity",
            scoreboard_id="unknown",
            evidence_class=EvidenceClass.CONSUMED_DEVELOPMENT,
            metrics={},
        )
    with pytest.raises(ValueError, match="qualification level"):
        evaluate_qualification_level(
            "unknown",
            scoreboard_id="aem_same_contract",
            evidence_class=EvidenceClass.CONSUMED_DEVELOPMENT,
            metrics={},
        )
    with pytest.raises(ValueError, match="evidence class"):
        evaluate_qualification_level(
            "level_0_integrity",
            scoreboard_id="aem_same_contract",
            evidence_class="unknown",
            metrics={},
        )
