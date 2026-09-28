"""Frozen Milestone-0 contract for Opening Sweep-Reclaim (OSR) research.

OSR is a research-only, long-side NSE cash hypothesis.  It looks for a failed
early downside auction and a causal reclaim; it is deliberately not another
parameterization of AEM v2's impulse, pullback, or breakout-retest generators.
This module cannot generate a signal, resolve an outcome, or change production.
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

ENTRY_MODES = ("gap_down_reclaim", "opening_low_sweep_reclaim")
TOP_K_POLICIES = (1, 2, 3)
EVIDENCE_CLASSES = (
    "consumed_historical_development",
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
class OsrGeometry:
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
        if not 0 < self.stop_pct < self.target_pct < 0.015:
            raise ValueError("OSR geometry requires 0 < stop < target < 1.5%")
        if not 5 <= self.max_hold_minutes <= 60:
            raise ValueError("OSR maximum hold must be between 5 and 60 minutes")

    @property
    def gross_reward_risk(self) -> float:
        return self.target_pct / self.stop_pct


@dataclass(frozen=True)
class OsrFeature:
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
            raise ValueError("OSR features must be available at decision time")


GEOMETRIES = (
    OsrGeometry("reclaim_30_22_20", target_pct=0.0030, stop_pct=0.0022, max_hold_minutes=20),
    OsrGeometry("reclaim_40_28_35", target_pct=0.0040, stop_pct=0.0028, max_hold_minutes=35),
    OsrGeometry("reclaim_55_36_50", target_pct=0.0055, stop_pct=0.0036, max_hold_minutes=50),
)

FEATURES = (
    OsrFeature("minutes_since_open", "time", "m1"),
    OsrFeature("time_remaining_minutes", "time", "calendar"),
    OsrFeature("gap_from_prior_close", "opening_auction", "daily_m1"),
    OsrFeature("gap_from_prior_low", "opening_auction", "daily_m1"),
    OsrFeature("opening_range_width_atr", "opening_auction", "daily_m1"),
    OsrFeature("opening_return", "opening_auction", "m1"),
    OsrFeature("early_drawdown", "failed_auction", "m1"),
    OsrFeature("sweep_depth", "failed_auction", "m1"),
    OsrFeature("recovery_from_low", "failed_auction", "m1"),
    OsrFeature("recovery_speed_bars", "failed_auction", "m1"),
    OsrFeature("reclaim_body_ratio", "reclaim_quality", "m1"),
    OsrFeature("reclaim_close_location", "reclaim_quality", "m1"),
    OsrFeature("reclaim_lower_wick_ratio", "reclaim_quality", "m1"),
    OsrFeature("return_1m", "reclaim_quality", "m1"),
    OsrFeature("return_3m", "reclaim_quality", "m1"),
    OsrFeature("price_to_open", "reclaim_context", "m1"),
    OsrFeature("price_to_vwap", "reclaim_context", "m1"),
    OsrFeature("vwap_slope_5m", "reclaim_context", "m1"),
    OsrFeature("vwap_reclaim_bars", "reclaim_context", "m1"),
    OsrFeature("tod_rvol", "participation", "m1_history"),
    OsrFeature("volume_acceleration_3m", "participation", "m1"),
    OsrFeature("cross_sectional_return_rank", "cross_sectional", "universe_m1"),
    OsrFeature("cross_sectional_recovery_rank", "cross_sectional", "universe_m1"),
    OsrFeature("cross_sectional_breadth", "cross_sectional", "universe_m1"),
    OsrFeature("cross_sectional_dispersion", "cross_sectional", "universe_m1"),
    OsrFeature("prior_day_range_atr", "daily_context", "daily"),
    OsrFeature("daily_extension_atr", "daily_context", "daily_m1"),
    OsrFeature("median_turnover_20d", "execution", "daily"),
    OsrFeature("impact_proxy", "execution", "m1"),
    OsrFeature("modeled_round_trip_cost_pct", "execution", "cost_model"),
)


@dataclass(frozen=True)
class OsrContract:
    version: str = "osr-contract-v1"
    strategy_version: str = "OSR_v1_opening_sweep_reclaim"
    signal_family: str = "failed_opening_downside_auction_mean_reversion"
    primary_track: str = "same_session_long_nse_cash"
    signal_interval_minutes: int = 1
    execution_interval_minutes: int = 1
    opening_range_minutes: int = 15
    earliest_decision_time: str = "09:30"
    latest_decision_time: str = "11:15"
    entry_modes: tuple[str, ...] = ENTRY_MODES
    geometries: tuple[OsrGeometry, ...] = GEOMETRIES
    features: tuple[OsrFeature, ...] = FEATURES
    minimum_gap_down_pct: float = 0.003
    maximum_gap_down_pct: float = 0.025
    minimum_sweep_depth_pct: float = 0.001
    maximum_initial_drawdown_pct: float = 0.03
    minimum_reclaim_close_location: float = 0.65
    entry_valid_minutes: int = 2
    maximum_chase_pct: float = 0.001
    maximum_selected_calls_per_session: int = 3
    top_k_policies: tuple[int, ...] = TOP_K_POLICIES
    maximum_geometries: int = 3
    research_minimum_gross_reward_risk: float = 1.0
    production_minimum_net_reward_risk: float = 2.0
    production_compatible: bool = False

    def __post_init__(self) -> None:
        if self.signal_interval_minutes != 1 or self.execution_interval_minutes != 1:
            raise ValueError("OSR Milestone 0 is frozen to M1 signal and execution bars")
        if self.opening_range_minutes != 15:
            raise ValueError("OSR opening range must remain 15 completed minutes")
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
            raise ValueError("every OSR geometry must have gross reward/risk above one")
        if not 0 < self.minimum_gap_down_pct < self.maximum_gap_down_pct < 0.10:
            raise ValueError("gap-down bounds are invalid")
        if not 0 < self.minimum_sweep_depth_pct < self.maximum_initial_drawdown_pct < 0.10:
            raise ValueError("sweep and drawdown bounds are invalid")
        if not 0.5 < self.minimum_reclaim_close_location <= 1.0:
            raise ValueError("reclaim close location must be in (0.5, 1]")
        if self.entry_valid_minutes != 2 or not 0 < self.maximum_chase_pct <= 0.002:
            raise ValueError("entry validity or chase policy changed")
        if self.maximum_selected_calls_per_session != 3 or self.top_k_policies != TOP_K_POLICIES:
            raise ValueError("OSR must report frozen top-one, top-two and top-three policies")
        feature_names = [feature.name for feature in self.features]
        if not feature_names or len(feature_names) != len(set(feature_names)):
            raise ValueError("feature registry must be non-empty and unique")
        if self.production_compatible:
            raise ValueError("OSR research cannot silently claim production compatibility")
        if self.production_minimum_net_reward_risk != 2.0:
            raise ValueError("the known production reward/risk gate must remain explicit")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


@dataclass(frozen=True)
class OsrResearchProtocol:
    version: str = "osr-accuracy-50-v1"
    strict_success: str = (
        "prediction_before_entry_then_executable_fill_then_target_before_stop_or_deadline_"
        "and_net_pnl_gt_0"
    )
    initial_pipeline_trial_budget: int = 12
    minimum_observed_strict_success_rate: float = 0.50
    minimum_wilson95_lower: float = 0.40
    minimum_resolved_selected_fills: int = 100
    minimum_active_sessions: int = 30
    minimum_active_session_coverage: float = 0.40
    maximum_unresolved_selected_calls: int = 0
    minimum_mean_net_r: float = 0.0
    minimum_random_advantage_r: float = 0.10
    maximum_single_symbol_win_share: float = 0.25
    evidence_classes: tuple[str, ...] = EVIDENCE_CLASSES
    existing_120_sessions_evidence_class: str = "consumed_historical_development"
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
            self.maximum_single_symbol_win_share,
            self.ultimate_minimum_observed_rate,
        )
        if any(not 0 <= value <= 1 for value in proportions):
            raise ValueError("accuracy, coverage, and concentration gates must be proportions")
        if self.minimum_observed_strict_success_rate != 0.50:
            raise ValueError("OSR must preserve the 50% development threshold")
        if self.minimum_wilson95_lower != 0.40:
            raise ValueError("OSR must preserve the 40% Wilson lower-bound gate")
        if self.minimum_resolved_selected_fills < 100 or self.minimum_active_sessions < 30:
            raise ValueError("sample and active-session gates cannot be weakened")
        if self.maximum_unresolved_selected_calls != 0:
            raise ValueError("unresolved selected calls must fail closed")
        if not 1 <= self.initial_pipeline_trial_budget <= 12:
            raise ValueError("OSR initial trial budget must be 1..12")
        if self.maximum_single_symbol_win_share > 0.25:
            raise ValueError("single-symbol concentration gate cannot be weakened")
        if self.evidence_classes != EVIDENCE_CLASSES:
            raise ValueError("evidence classes must remain ordered and explicit")
        if self.existing_120_sessions_evidence_class != self.evidence_classes[0]:
            raise ValueError("inspected history must remain development evidence")
        if not self.mandatory_stress_cases or self.minimum_random_advantage_r < 0.10:
            raise ValueError("stress and matched-random gates cannot be weakened")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


DEFAULT_OSR_CONTRACT = OsrContract()
DEFAULT_OSR_PROTOCOL = OsrResearchProtocol()


def protocol_bundle(
    contract: OsrContract = DEFAULT_OSR_CONTRACT,
    protocol: OsrResearchProtocol = DEFAULT_OSR_PROTOCOL,
) -> dict[str, Any]:
    result = {
        "contract": contract.to_dict(),
        "contract_sha256": contract.sha256,
        "protocol": protocol.to_dict(),
        "protocol_sha256": protocol.sha256,
        "feature_count": len(contract.features),
        "geometry_count": len(contract.geometries),
        "entry_mode_count": len(contract.entry_modes),
        "registered_pipeline_budget": protocol.initial_pipeline_trial_budget,
    }
    result["bundle_sha256"] = _sha(result)
    return result


def freeze_osr_protocol(root: Path = ROOT, output: Path = OUTPUT) -> dict[str, Any]:
    """Persist an immutable, research-only OSR Milestone-0 protocol artifact."""

    root, output = Path(root).resolve(), Path(output).resolve()
    plan_path = root / "docs/plan-osr-50pct-baseline.md"
    if not plan_path.is_file():
        raise ValueError("OSR plan is missing")
    run_id = uuid4().hex
    report_path = output / "osr/protocol/runs" / run_id / "report.json"
    bundle = protocol_bundle()
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "osr-milestone-0-v1",
        "status": "protocol_frozen_not_evaluated",
        "milestone": 0,
        "artifact_path": str(report_path),
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_implemented": False,
        "canonical_baseline": {
            "name": "AEM_v1_frozen_50_stock_cohort",
            "dataset_id": "ec539cf66bea4c18ba994506f85a52d6",
            "resolved_fills": 693,
            "strict_wins": 149,
            "strict_success_rate": 149 / 693,
            "wilson95_lower": 0.18603500195342146,
            "mean_net_r": -0.2747079541350378,
        },
        "aem_v2_result": {
            "pipeline_run_id": "789dc32d4c2340419c9fad38c88a4920",
            "qualified_candidates": 0,
            "decision": "negative_development_result_no_baseline_change",
        },
        "plan_path": str(plan_path),
        "plan_sha256": digest(plan_path),
        "implementation_sha256": digest(Path(__file__)),
        **bundle,
        "next_milestone": "causal_opening_sweep_reclaim_event_and_outcome_engine",
        "limitations": [
            "This artifact freezes hypotheses and evidence gates; it reports no OSR result.",
            "All existing 120-session AEM history is consumed development evidence.",
            "The initial cohort is the liquid 50-stock pilot, not the whole NSE universe.",
            "The quick-profit geometries are not compatible with the production 2.0 net R:R gate.",
            "No production scanner, manager, risk rule, alert, dashboard, or order path reads OSR.",
        ],
    }
    write_json(report_path, report)
    write_json(
        output / "osr/protocol/latest.json",
        {"id": run_id, "path": str(report_path), "bundle_sha256": bundle["bundle_sha256"]},
    )
    return report
