"""Frozen Milestone-0 contract for the custom AEM v2 research algorithm.

This module defines research hypotheses and evidence gates. It cannot generate a
signal, evaluate an outcome, place an order, or change production eligibility.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

ENTRY_MODES = ("anticipatory_impulse", "confirmed_pullback", "breakout_retest")
TOP_K_POLICIES = (1, 2, 3)
EVIDENCE_CLASSES = (
    "development_diagnostic",
    "locked_later_historical",
    "prospective_shadow",
)
FORBIDDEN_PREDICTION_FIELDS = frozenset(
    {
        "entry",
        "entry_at",
        "exit",
        "exit_at",
        "gross_pnl",
        "gross_r",
        "label",
        "net_pnl",
        "net_r",
        "outcome",
        "status",
        "strict_success",
        "target_hit",
    }
)


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class AemV2Geometry:
    id: str
    target_pct: float
    stop_pct: float
    max_hold_minutes: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", self.id):
            raise ValueError("geometry id must be lowercase snake case")
        values = (self.target_pct, self.stop_pct)
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
            raise ValueError("geometry percentages must be finite numbers")
        if not 0 < self.stop_pct < self.target_pct < 0.02:
            raise ValueError("quick-profit geometry requires 0 < stop < target < 2%")
        if not 5 <= self.max_hold_minutes <= 90:
            raise ValueError("maximum hold must be between 5 and 90 minutes")

    @property
    def gross_reward_risk(self) -> float:
        return self.target_pct / self.stop_pct


@dataclass(frozen=True)
class AemV2Feature:
    name: str
    group: str
    source: str
    available_at: str = "decision_time"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", self.name):
            raise ValueError("feature name must be lowercase snake case")
        if self.name in FORBIDDEN_PREDICTION_FIELDS:
            raise ValueError(f"outcome or execution field is forbidden: {self.name}")
        if self.available_at != "decision_time":
            raise ValueError("AEM v2 features must be available at decision time")


GEOMETRIES = (
    AemV2Geometry("quick_35_30_25", target_pct=0.0035, stop_pct=0.0030, max_hold_minutes=25),
    AemV2Geometry("quick_45_35_40", target_pct=0.0045, stop_pct=0.0035, max_hold_minutes=40),
    AemV2Geometry("quick_60_45_60", target_pct=0.0060, stop_pct=0.0045, max_hold_minutes=60),
)

FEATURES = (
    AemV2Feature("minutes_since_open", "time_opening", "m1"),
    AemV2Feature("opening_range_position", "time_opening", "m1"),
    AemV2Feature("opening_gap_atr", "time_opening", "daily_m1"),
    AemV2Feature("time_remaining_minutes", "time_opening", "calendar"),
    AemV2Feature("return_1m", "impulse", "m1"),
    AemV2Feature("return_3m", "impulse", "m1"),
    AemV2Feature("return_5m", "impulse", "m1"),
    AemV2Feature("price_acceleration_3m", "impulse", "m1"),
    AemV2Feature("body_ratio", "impulse", "m1"),
    AemV2Feature("wick_balance", "impulse", "m1"),
    AemV2Feature("range_expansion", "impulse", "m1"),
    AemV2Feature("prior_compression", "breakout_state", "m1"),
    AemV2Feature("causal_level_distance", "breakout_state", "daily_m1"),
    AemV2Feature("failed_break_count", "breakout_state", "m1"),
    AemV2Feature("retest_depth", "breakout_state", "m1"),
    AemV2Feature("vwap_distance", "vwap", "m1"),
    AemV2Feature("vwap_slope_5m", "vwap", "m1"),
    AemV2Feature("vwap_hold_bars", "vwap", "m1"),
    AemV2Feature("tod_rvol", "participation", "m1_history"),
    AemV2Feature("volume_acceleration_3m", "participation", "m1"),
    AemV2Feature("rolling_turnover_inr", "participation", "m1"),
    AemV2Feature("relative_strength_5m", "cross_sectional", "universe_m1"),
    AemV2Feature("cross_sectional_breadth", "cross_sectional", "universe_m1"),
    AemV2Feature("cross_sectional_dispersion", "cross_sectional", "universe_m1"),
    AemV2Feature("resistance_distance", "remaining_room", "daily_m1"),
    AemV2Feature("daily_extension_atr", "remaining_room", "daily_m1"),
    AemV2Feature("achievable_move_before_deadline", "remaining_room", "m1"),
    AemV2Feature("median_turnover_20d", "execution", "daily"),
    AemV2Feature("impact_proxy", "execution", "m1"),
    AemV2Feature("limit_distance", "execution", "m1"),
    AemV2Feature("chase_pct", "execution", "m1"),
    AemV2Feature("modeled_round_trip_cost_pct", "execution", "cost_model"),
    AemV2Feature("causal_volatility", "regime", "universe_m1"),
    AemV2Feature("trend_age_bars", "regime", "m1"),
)


@dataclass(frozen=True)
class AemV2Contract:
    version: str = "aem-v2-contract-v1"
    strategy_version: str = "AEM_v2_precision_ladder"
    primary_track: str = "same_session_long_nse_cash"
    signal_interval_minutes: int = 1
    execution_interval_minutes: int = 1
    earliest_decision_time: str = "09:20"
    latest_decision_time: str = "11:00"
    entry_modes: tuple[str, ...] = ENTRY_MODES
    geometries: tuple[AemV2Geometry, ...] = GEOMETRIES
    features: tuple[AemV2Feature, ...] = FEATURES
    maximum_geometries: int = 6
    maximum_selected_calls_per_session: int = 3
    top_k_policies: tuple[int, ...] = TOP_K_POLICIES
    research_minimum_gross_reward_risk: float = 1.0
    production_minimum_net_reward_risk: float = 2.0
    production_compatible: bool = False

    def __post_init__(self) -> None:
        if self.signal_interval_minutes != 1 or self.execution_interval_minutes != 1:
            raise ValueError("AEM v2 Milestone 0 is frozen to M1 signal and execution bars")
        if self.earliest_decision_time >= self.latest_decision_time:
            raise ValueError("decision window is invalid")
        if self.entry_modes != ENTRY_MODES or len(set(self.entry_modes)) != len(self.entry_modes):
            raise ValueError("entry modes must equal the frozen ordered registry")
        if not 1 <= len(self.geometries) <= self.maximum_geometries:
            raise ValueError("geometry grid exceeds its frozen budget")
        if len({geometry.id for geometry in self.geometries}) != len(self.geometries):
            raise ValueError("geometry identifiers must be unique")
        if any(
            geometry.gross_reward_risk <= self.research_minimum_gross_reward_risk
            for geometry in self.geometries
        ):
            raise ValueError("every research geometry must have gross reward/risk above one")
        if self.maximum_selected_calls_per_session != 3 or self.top_k_policies != TOP_K_POLICIES:
            raise ValueError("AEM v2 must report frozen top-one, top-two and top-three policies")
        feature_names = [feature.name for feature in self.features]
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("feature registry contains duplicate names")
        if not self.features:
            raise ValueError("feature registry cannot be empty")
        if self.production_compatible:
            raise ValueError("quick-profit research cannot silently claim production compatibility")
        if self.production_minimum_net_reward_risk != 2.0:
            raise ValueError("the known production reward/risk gate must remain explicit")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


@dataclass(frozen=True)
class AemV2ResearchProtocol:
    version: str = "aem-v2-accuracy-50-v1"
    strict_success: str = (
        "prediction_before_entry_then_executable_fill_then_target_before_stop_or_deadline_"
        "and_net_pnl_gt_0"
    )
    initial_pipeline_trial_budget: int = 24
    minimum_observed_strict_success_rate: float = 0.50
    minimum_wilson95_lower: float = 0.40
    minimum_resolved_selected_fills: int = 100
    minimum_active_sessions: int = 30
    minimum_active_session_coverage: float = 0.40
    maximum_unresolved_selected_calls: int = 0
    minimum_mean_net_r: float = 0.0
    minimum_random_advantage_r: float = 0.10
    evidence_classes: tuple[str, ...] = EVIDENCE_CLASSES
    mandatory_stress_cases: tuple[str, ...] = (
        "base_costs",
        "costs_1p25x",
        "costs_1p50x",
        "slippage_2x",
        "one_bar_delay",
        "adverse_missed_fills",
    )
    ultimate_minimum_total_resolved: int = 500
    ultimate_minimum_oos_resolved: int = 100
    ultimate_minimum_observed_rate: float = 0.80
    ultimate_minimum_prospective_resolved: int = 100

    def __post_init__(self) -> None:
        proportions = (
            self.minimum_observed_strict_success_rate,
            self.minimum_wilson95_lower,
            self.minimum_active_session_coverage,
            self.ultimate_minimum_observed_rate,
        )
        if any(not 0 <= value <= 1 for value in proportions):
            raise ValueError("accuracy and coverage gates must be proportions")
        if self.minimum_observed_strict_success_rate != 0.50:
            raise ValueError("Milestone 0 must preserve the 50% qualification threshold")
        if self.minimum_wilson95_lower != 0.40:
            raise ValueError("Milestone 0 must preserve the 40% Wilson lower-bound gate")
        if self.minimum_resolved_selected_fills < 100 or self.minimum_active_sessions < 30:
            raise ValueError("sample and active-session gates cannot be weakened")
        if self.maximum_unresolved_selected_calls != 0:
            raise ValueError("unresolved selected calls must fail closed")
        if not 1 <= self.initial_pipeline_trial_budget <= 24:
            raise ValueError("initial pipeline trial budget must be 1..24")
        if self.evidence_classes != EVIDENCE_CLASSES:
            raise ValueError("evidence classes must remain ordered and explicit")
        if not self.mandatory_stress_cases or self.minimum_random_advantage_r < 0.10:
            raise ValueError("stress and matched-random gates cannot be weakened")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


DEFAULT_AEM_V2_CONTRACT = AemV2Contract()
DEFAULT_AEM_V2_PROTOCOL = AemV2ResearchProtocol()


def protocol_bundle(
    contract: AemV2Contract = DEFAULT_AEM_V2_CONTRACT,
    protocol: AemV2ResearchProtocol = DEFAULT_AEM_V2_PROTOCOL,
) -> dict[str, Any]:
    result = {
        "contract": contract.to_dict(),
        "contract_sha256": contract.sha256,
        "protocol": protocol.to_dict(),
        "protocol_sha256": protocol.sha256,
        "feature_count": len(contract.features),
        "geometry_count": len(contract.geometries),
        "entry_mode_count": len(contract.entry_modes),
    }
    result["bundle_sha256"] = _sha(result)
    return result


def freeze_aem_v2_protocol(root: Path = ROOT, output: Path = OUTPUT) -> dict[str, Any]:
    """Persist an immutable research-only Milestone-0 protocol artifact."""

    root, output = Path(root).resolve(), Path(output).resolve()
    plan_path = root / "docs/plan-aem-v2-50pct-baseline.md"
    if not plan_path.is_file():
        raise ValueError("AEM v2 plan is missing")
    run_id = uuid4().hex
    report_path = output / "aem_v2/protocol/runs" / run_id / "report.json"
    bundle = protocol_bundle()
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "aem-v2-milestone-0-v1",
        "status": "protocol_frozen_not_evaluated",
        "milestone": 0,
        "artifact_path": str(report_path),
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_implemented": False,
        "canonical_baseline": {
            "dataset_id": "ec539cf66bea4c18ba994506f85a52d6",
            "resolved_fills": 693,
            "strict_wins": 149,
            "strict_success_rate": 149 / 693,
            "wilson95_lower": 0.18603500195342146,
            "mean_net_r": -0.2747079541350378,
        },
        "plan_path": str(plan_path),
        "plan_sha256": digest(plan_path),
        "implementation_sha256": digest(Path(__file__)),
        **bundle,
        "next_milestone": "causal_event_and_outcome_engine",
        "limitations": [
            "This artifact freezes hypotheses and evidence gates; it reports no model result.",
            "All existing 120-session AEM history is consumed development evidence.",
            "The parked market/sector context store has no collected index sessions.",
            "The quick-profit geometries are not compatible with the production 2.0 net R:R gate.",
            "No production scanner, manager, risk rule, alert or order path reads this artifact.",
        ],
    }
    write_json(report_path, report)
    write_json(
        output / "aem_v2/protocol/latest.json",
        {"id": run_id, "path": str(report_path), "bundle_sha256": bundle["bundle_sha256"]},
    )
    return report
