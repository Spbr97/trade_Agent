"""Frozen governance contract for the accuracy-improvement program.

This module evaluates evidence.  It never generates a call, trains a model, changes
the canonical baseline, or authorizes live behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class EvidenceClass(StrEnum):
    CONSUMED_DEVELOPMENT = "consumed_historical_development"
    LOCKED_HISTORICAL_OOS = "locked_historical_oos"
    PROSPECTIVE_SHADOW = "prospective_shadow"
    PAPER = "paper"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ScoreboardContract:
    id: str
    claim: str
    comparator: str
    target_stop_policy: str


@dataclass(frozen=True)
class GateSpec:
    id: str
    metric: str
    comparison: str
    threshold: float | bool

    def __post_init__(self) -> None:
        if self.comparison not in {"eq", "gt", "gte", "lt", "lte"}:
            raise ValueError("unknown accuracy-program gate comparison")
        if not self.id or not self.metric:
            raise ValueError("accuracy-program gates require identifiers")


@dataclass(frozen=True)
class QualificationLevel:
    id: str
    evidence_classes: tuple[EvidenceClass, ...]
    gates: tuple[GateSpec, ...]


SCOREBOARDS = (
    ScoreboardContract(
        id="aem_same_contract",
        claim="direct_aem_v1_baseline_improvement",
        comparator="aem_v1_50_stock_canonical",
        target_stop_policy="target_0.8pct_stop_0.6pct_deadline_90m_unchanged",
    ),
    ScoreboardContract(
        id="new_quick_profit_contract",
        claim="new_agent_strategy_champion",
        comparator="own_unfiltered_same_contract_and_aem_v1_canonical",
        target_stop_policy="versioned_strategy_specific_geometry_never_relabelled_as_aem",
    ),
)


LEVELS = (
    QualificationLevel(
        id="level_0_integrity",
        evidence_classes=tuple(EvidenceClass),
        gates=tuple(
            GateSpec(name, name, "eq", True)
            for name in (
                "complete_coordinate_accounting",
                "features_available_by_decision",
                "immutable_predictions",
                "outcomes_appended_separately",
                "deterministic_outcome_contract",
                "point_in_time_universe",
                "reproducible_fingerprints",
            )
        ),
    ),
    QualificationLevel(
        id="level_1_mechanism",
        evidence_classes=(EvidenceClass.CONSUMED_DEVELOPMENT,),
        gates=(
            GateSpec("beats_inverse", "beats_inverse", "eq", True),
            GateSpec("beats_temporal_placebo", "beats_temporal_placebo", "eq", True),
            GateSpec("fold_direction_consistent", "fold_direction_consistent", "eq", True),
            GateSpec("adjusted_empirical_p", "adjusted_empirical_p", "lte", 0.05),
            GateSpec("positive_selection_spread", "selection_net_r_spread", "gt", 0.0),
            GateSpec("edge_exceeds_costs", "edge_exceeds_costs", "eq", True),
        ),
    ),
    QualificationLevel(
        id="level_2_credible_improvement",
        evidence_classes=(EvidenceClass.LOCKED_HISTORICAL_OOS,),
        gates=(
            GateSpec("same_contract_comparator", "same_contract_comparator_passed", "eq", True),
            GateSpec("matched_stock_control", "matched_stock_control_passed", "eq", True),
            GateSpec("minimum_resolved", "resolved_fills", "gte", 100.0),
            GateSpec("minimum_active_sessions", "active_sessions", "gte", 30.0),
            GateSpec("minimum_coverage", "active_session_coverage", "gte", 0.40),
            GateSpec("minimum_accuracy", "strict_success_rate", "gte", 0.265),
            GateSpec("wilson_above_baseline", "wilson95_lower", "gt", 0.18603500195342146),
            GateSpec("paired_accuracy_delta", "accuracy_delta_block_lower", "gt", 0.0),
            GateSpec("positive_mean_net_r", "mean_net_r", "gt", 0.0),
            GateSpec("random_advantage", "matched_random_advantage_r", "gte", 0.10),
            GateSpec("positive_worst_stress", "minimum_stress_mean_net_r", "gt", 0.0),
            GateSpec("symbol_concentration", "maximum_symbol_win_share", "lte", 0.20),
            GateSpec("sector_concentration", "maximum_sector_win_share", "lte", 0.35),
        ),
    ),
    QualificationLevel(
        id="level_3_50pct_research_baseline",
        evidence_classes=(EvidenceClass.LOCKED_HISTORICAL_OOS,),
        gates=(
            GateSpec("minimum_resolved", "resolved_fills", "gte", 100.0),
            GateSpec("minimum_active_sessions", "active_sessions", "gte", 30.0),
            GateSpec("minimum_coverage", "active_session_coverage", "gte", 0.40),
            GateSpec("minimum_accuracy", "strict_success_rate", "gte", 0.50),
            GateSpec("minimum_wilson", "wilson95_lower", "gte", 0.40),
            GateSpec("positive_worst_stress", "minimum_stress_mean_net_r", "gt", 0.0),
            GateSpec("positive_portfolio", "portfolio_net_return", "gt", 0.0),
            GateSpec("stable_time_regime", "fold_month_regime_direction_positive", "eq", True),
        ),
    ),
    QualificationLevel(
        id="level_4_prospective_70_80",
        evidence_classes=(EvidenceClass.PROSPECTIVE_SHADOW, EvidenceClass.PAPER),
        gates=(
            GateSpec("minimum_resolved", "resolved_fills", "gte", 100.0),
            GateSpec("minimum_active_sessions", "active_sessions", "gte", 30.0),
            GateSpec("minimum_accuracy", "strict_success_rate", "gte", 0.80),
            GateSpec("minimum_wilson", "wilson95_lower", "gte", 0.70),
            GateSpec("session_target", "active_sessions_meeting_target", "gte", 0.70),
            GateSpec("minimum_coverage", "active_session_coverage", "gte", 0.40),
            GateSpec("positive_mean_net_r", "mean_net_r", "gt", 0.0),
            GateSpec("positive_total_net", "total_net_return", "gt", 0.0),
            GateSpec("positive_worst_stress", "minimum_stress_mean_net_r", "gt", 0.0),
            GateSpec("block_uncertainty", "session_week_uncertainty_passed", "eq", True),
            GateSpec("concentration", "concentration_passed", "eq", True),
            GateSpec("calibration", "calibration_passed", "eq", True),
            GateSpec("drift", "drift_passed", "eq", True),
        ),
    ),
)


@dataclass(frozen=True)
class AccuracyProgramProtocol:
    version: str = "accuracy-program-v1"
    canonical_dataset_id: str = "ec539cf66bea4c18ba994506f85a52d6"
    canonical_baseline_name: str = "aem_v1_50_stock_canonical"
    canonical_resolved_fills: int = 693
    canonical_strict_wins: int = 149
    canonical_strict_success_rate: float = 0.215007215007215
    canonical_wilson95_lower: float = 0.18603500195342146
    canonical_mean_net_r: float = -0.2747079541350378
    consumed_through_session: str = "2026-09-18"
    max_signal_families_per_cycle: int = 3
    max_mechanism_variants_per_cycle: int = 12
    max_model_pipelines_per_surviving_family: int = 6
    minimum_matched_shuffles: int = 256
    maximum_familywise_empirical_p: float = 0.05
    familywise_control: str = "max_statistic_shuffle_or_holm"
    scoreboards: tuple[ScoreboardContract, ...] = SCOREBOARDS
    levels: tuple[QualificationLevel, ...] = LEVELS

    def __post_init__(self) -> None:
        expected = {
            "version": "accuracy-program-v1",
            "canonical_dataset_id": "ec539cf66bea4c18ba994506f85a52d6",
            "canonical_baseline_name": "aem_v1_50_stock_canonical",
            "canonical_resolved_fills": 693,
            "canonical_strict_wins": 149,
            "canonical_strict_success_rate": 0.215007215007215,
            "canonical_wilson95_lower": 0.18603500195342146,
            "canonical_mean_net_r": -0.2747079541350378,
            "consumed_through_session": "2026-09-18",
            "max_signal_families_per_cycle": 3,
            "max_mechanism_variants_per_cycle": 12,
            "max_model_pipelines_per_surviving_family": 6,
            "minimum_matched_shuffles": 256,
            "maximum_familywise_empirical_p": 0.05,
            "familywise_control": "max_statistic_shuffle_or_holm",
        }
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ValueError(f"accuracy-program v1 field is frozen: {name}")
        if self.scoreboards != SCOREBOARDS or self.levels != LEVELS:
            raise ValueError("accuracy-program scoreboards and levels are frozen")
        if self.canonical_strict_wins / self.canonical_resolved_fills != (
            self.canonical_strict_success_rate
        ):
            raise ValueError("canonical accuracy identity is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_ACCURACY_PROGRAM = AccuracyProgramProtocol()
SCOREBOARD_IDS = frozenset(scoreboard.id for scoreboard in SCOREBOARDS)
LEVEL_BY_ID = {level.id: level for level in LEVELS}


def _valid_observed(value: Any, threshold: float | bool) -> float | bool:
    if isinstance(threshold, bool):
        if not isinstance(value, bool):
            raise ValueError("boolean accuracy-program gate received a non-boolean value")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric accuracy-program gate received a nonnumeric value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("accuracy-program gate received a nonfinite value")
    return number


def _passes(observed: float | bool, gate: GateSpec) -> bool:
    if gate.comparison == "eq":
        return observed == gate.threshold
    if gate.comparison == "gt":
        return observed > gate.threshold
    if gate.comparison == "gte":
        return observed >= gate.threshold
    if gate.comparison == "lt":
        return observed < gate.threshold
    return observed <= gate.threshold


def evaluate_qualification_level(
    level_id: str,
    *,
    scoreboard_id: str,
    evidence_class: EvidenceClass | str,
    metrics: dict[str, Any],
    protocol: AccuracyProgramProtocol = DEFAULT_ACCURACY_PROGRAM,
) -> dict[str, Any]:
    """Evaluate one frozen level; absent evidence is a visible failure."""

    if protocol.sha256 != DEFAULT_ACCURACY_PROGRAM.sha256:
        raise ValueError("accuracy-program protocol fingerprint changed")
    if level_id not in LEVEL_BY_ID:
        raise ValueError("unknown accuracy-program qualification level")
    if scoreboard_id not in SCOREBOARD_IDS:
        raise ValueError("unknown accuracy-program scoreboard")
    try:
        evidence = EvidenceClass(evidence_class)
    except ValueError as exc:
        raise ValueError("unknown accuracy-program evidence class") from exc
    level = LEVEL_BY_ID[level_id]
    evidence_passed = evidence in level.evidence_classes
    gate_results: dict[str, dict[str, Any]] = {
        "evidence_class": {
            "status": "pass" if evidence_passed else "fail",
            "passed": evidence_passed,
            "observed": evidence.value,
            "requirement": [item.value for item in level.evidence_classes],
        }
    }
    for gate in level.gates:
        if gate.metric not in metrics or metrics[gate.metric] is None:
            gate_results[gate.id] = {
                "status": "not_available",
                "passed": False,
                "observed": None,
                "metric": gate.metric,
                "comparison": gate.comparison,
                "threshold": gate.threshold,
            }
            continue
        observed = _valid_observed(metrics[gate.metric], gate.threshold)
        passed = _passes(observed, gate)
        gate_results[gate.id] = {
            "status": "pass" if passed else "fail",
            "passed": passed,
            "observed": observed,
            "metric": gate.metric,
            "comparison": gate.comparison,
            "threshold": gate.threshold,
        }
    passed = all(result["passed"] is True for result in gate_results.values())
    return {
        "version": protocol.version,
        "protocol_sha256": protocol.sha256,
        "level": level.id,
        "scoreboard": scoreboard_id,
        "evidence_class": evidence.value,
        "passed": passed,
        "promotion_review_allowed": passed and level.id == "level_4_prospective_70_80",
        "eligible_for_live": False,
        "gates": gate_results,
    }
