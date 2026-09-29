"""Frozen Milestone-0 contract for Same-Slot Micro-Momentum research.

SSM tests a published return-periodicity mechanism: a stock's return during a
particular half-hour may predict its return during that same half-hour on later
sessions.  This module freezes a research-only contract.  It cannot generate a
signal, resolve an outcome, or change production behavior.
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

PREDICTOR_MODES = ("lag1_cross_sectional", "multi_day_persistence")
SELECTOR_KINDS = ("transparent_rank_gate", "regularized_logistic")
SLOT_STARTS = (
    "09:45",
    "10:15",
    "10:45",
    "11:15",
    "11:45",
    "12:15",
    "12:45",
    "13:15",
    "13:45",
    "14:15",
    "14:45",
)
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
        "filled",
        "gross_pnl",
        "gross_r",
        "label",
        "net_pnl",
        "net_r",
        "outcome",
        "outcome_reason",
        "status",
        "strict_success",
        "target_hit",
    }
)


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class SsmGeometry:
    id: str
    target_pct: float
    stop_pct: float
    max_hold_minutes: int = 30

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", self.id):
            raise ValueError("geometry id must be lowercase snake case")
        values = (self.target_pct, self.stop_pct)
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
            raise ValueError("geometry percentages must be finite numbers")
        if not 0 < self.target_pct <= self.stop_pct <= 0.005:
            raise ValueError("SSM geometry requires 0 < target <= stop <= 0.5%")
        if self.gross_reward_risk < 2 / 3:
            raise ValueError("SSM gross reward/risk cannot be below two thirds")
        if self.max_hold_minutes != 30:
            raise ValueError("SSM exits must remain inside the predicted half-hour")

    @property
    def gross_reward_risk(self) -> float:
        return self.target_pct / self.stop_pct


@dataclass(frozen=True)
class SsmFeature:
    name: str
    group: str
    source: str
    available_at: str = "before_slot_entry"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", self.name):
            raise ValueError("feature name must be lowercase snake case")
        if self.name in FORBIDDEN_PREDICTION_FIELDS:
            raise ValueError(f"outcome or execution field is forbidden: {self.name}")
        if self.available_at != "before_slot_entry":
            raise ValueError("SSM features must be available before slot entry")


GEOMETRIES = (
    SsmGeometry("slot_20_30_30", target_pct=0.0020, stop_pct=0.0030),
    SsmGeometry("slot_25_30_30", target_pct=0.0025, stop_pct=0.0030),
    SsmGeometry("slot_30_30_30", target_pct=0.0030, stop_pct=0.0030),
)

FEATURES = (
    SsmFeature("slot_index", "time", "calendar"),
    SsmFeature("minutes_from_open", "time", "calendar"),
    SsmFeature("lag1_same_slot_return", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag2_same_slot_return", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag3_same_slot_return", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag5_mean_same_slot_return", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag20_mean_same_slot_return", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag40_mean_same_slot_return", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag5_positive_share", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag20_positive_share", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag40_positive_share", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("same_slot_return_std_40", "return_periodicity", "prior_m1_sessions"),
    SsmFeature("lag1_cross_sectional_rank", "cross_sectional", "prior_universe_m1"),
    SsmFeature("lag5_cross_sectional_rank", "cross_sectional", "prior_universe_m1"),
    SsmFeature("lag20_cross_sectional_rank", "cross_sectional", "prior_universe_m1"),
    SsmFeature("same_slot_cross_sectional_dispersion", "cross_sectional", "prior_universe_m1"),
    SsmFeature("lag5_same_slot_volume_ratio", "participation", "prior_m1_sessions"),
    SsmFeature("lag20_same_slot_turnover", "participation", "prior_m1_sessions"),
    SsmFeature("pre_slot_day_return", "current_context", "completed_current_m1"),
    SsmFeature("pre_slot_cross_sectional_rank", "current_context", "completed_universe_m1"),
    SsmFeature("pre_slot_breadth", "current_context", "completed_universe_m1"),
    SsmFeature("median_turnover_20d", "execution", "prior_daily"),
    SsmFeature("impact_proxy", "execution", "completed_current_m1"),
    SsmFeature("modeled_round_trip_cost_pct", "execution", "cost_model"),
)


@dataclass(frozen=True)
class SsmContract:
    version: str = "ssm-contract-v1"
    strategy_version: str = "SSM_v1_same_slot_micro_momentum"
    signal_family: str = "interday_same_half_hour_cross_sectional_momentum"
    primary_track: str = "same_session_long_nse_cash"
    bar_minutes: int = 1
    slot_minutes: int = 30
    slot_starts: tuple[str, ...] = SLOT_STARTS
    predictor_modes: tuple[str, ...] = PREDICTOR_MODES
    selector_kinds: tuple[str, ...] = SELECTOR_KINDS
    geometries: tuple[SsmGeometry, ...] = GEOMETRIES
    features: tuple[SsmFeature, ...] = FEATURES
    history_lookback_sessions: int = 40
    minimum_history_sessions: int = 20
    minimum_cross_sectional_peers: int = 30
    transparent_rank_threshold: float = 0.90
    minimum_positive_persistence_share: float = 0.65
    decision_lead_minutes: int = 1
    maximum_selected_calls_per_session: int = 3
    top_k_policies: tuple[int, ...] = TOP_K_POLICIES
    side: str = "long"
    production_minimum_net_reward_risk: float = 2.0
    production_compatible: bool = False

    def __post_init__(self) -> None:
        if self.bar_minutes != 1 or self.slot_minutes != 30:
            raise ValueError("SSM is frozen to M1 bars and non-overlapping half-hours")
        if self.slot_starts != SLOT_STARTS or len(set(self.slot_starts)) != len(self.slot_starts):
            raise ValueError("half-hour starts must equal the frozen ordered registry")
        if self.predictor_modes != PREDICTOR_MODES:
            raise ValueError("predictor modes must equal the frozen ordered registry")
        if self.selector_kinds != SELECTOR_KINDS:
            raise ValueError("selector kinds must equal the frozen ordered registry")
        if len(self.geometries) != 3 or len({item.id for item in self.geometries}) != 3:
            raise ValueError("SSM must retain exactly three unique geometries")
        if self.history_lookback_sessions != 40 or self.minimum_history_sessions != 20:
            raise ValueError("SSM history windows cannot change inside v1")
        if self.minimum_cross_sectional_peers < 30:
            raise ValueError("SSM requires at least 30 same-slot peers")
        if not 0.5 < self.transparent_rank_threshold < 1.0:
            raise ValueError("rank threshold must select only the upper cross-section")
        if not 0.5 < self.minimum_positive_persistence_share <= 1.0:
            raise ValueError("persistence share must require a positive majority")
        if self.decision_lead_minutes != 1:
            raise ValueError("prediction must be frozen before the entry minute")
        if self.maximum_selected_calls_per_session != 3 or self.top_k_policies != TOP_K_POLICIES:
            raise ValueError("SSM must report frozen top-one, top-two and top-three policies")
        if self.side != "long":
            raise ValueError("SSM v1 is long-only for direct comparison with AEM v1")
        feature_names = [feature.name for feature in self.features]
        if len(feature_names) != 24 or len(feature_names) != len(set(feature_names)):
            raise ValueError("SSM must retain exactly 24 unique causal features")
        if self.production_compatible:
            raise ValueError("SSM research cannot silently claim production compatibility")
        if self.production_minimum_net_reward_risk != 2.0:
            raise ValueError("the known production reward/risk gate must remain explicit")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


@dataclass(frozen=True)
class SsmResearchProtocol:
    version: str = "ssm-accuracy-50-v1"
    strict_success: str = (
        "prediction_before_slot_then_executable_fill_then_target_before_stop_or_slot_end_"
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
    maximum_single_slot_win_share: float = 0.35
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
            self.maximum_single_slot_win_share,
            self.ultimate_minimum_observed_rate,
        )
        if any(not 0 <= value <= 1 for value in proportions):
            raise ValueError("accuracy, coverage, and concentration gates must be proportions")
        if self.minimum_observed_strict_success_rate != 0.50:
            raise ValueError("SSM must preserve the 50% development threshold")
        if self.minimum_wilson95_lower != 0.40:
            raise ValueError("SSM must preserve the 40% Wilson lower-bound gate")
        if self.minimum_resolved_selected_fills < 100 or self.minimum_active_sessions < 30:
            raise ValueError("sample and active-session gates cannot be weakened")
        if self.maximum_unresolved_selected_calls != 0:
            raise ValueError("unresolved selected calls must fail closed")
        if self.initial_pipeline_trial_budget != 12:
            raise ValueError("SSM initial trial budget must remain exactly 12")
        if self.maximum_single_symbol_win_share > 0.25:
            raise ValueError("single-symbol concentration gate cannot be weakened")
        if self.maximum_single_slot_win_share > 0.35:
            raise ValueError("single-slot concentration gate cannot be weakened")
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


DEFAULT_SSM_CONTRACT = SsmContract()
DEFAULT_SSM_PROTOCOL = SsmResearchProtocol()


def protocol_bundle(
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
    protocol: SsmResearchProtocol = DEFAULT_SSM_PROTOCOL,
) -> dict[str, Any]:
    result = {
        "contract": contract.to_dict(),
        "contract_sha256": contract.sha256,
        "protocol": protocol.to_dict(),
        "protocol_sha256": protocol.sha256,
        "feature_count": len(contract.features),
        "geometry_count": len(contract.geometries),
        "predictor_mode_count": len(contract.predictor_modes),
        "selector_kind_count": len(contract.selector_kinds),
        "slot_count": len(contract.slot_starts),
        "registered_pipeline_budget": protocol.initial_pipeline_trial_budget,
    }
    result["bundle_sha256"] = _sha(result)
    return result


def freeze_ssm_protocol(root: Path = ROOT, output: Path = OUTPUT) -> dict[str, Any]:
    """Persist an immutable, research-only SSM Milestone-0 protocol artifact."""

    root, output = Path(root).resolve(), Path(output).resolve()
    plan_path = root / "docs/plan-ssm-50pct-baseline.md"
    if not plan_path.is_file():
        raise ValueError("SSM plan is missing")
    run_id = uuid4().hex
    report_path = output / "ssm/protocol/runs" / run_id / "report.json"
    bundle = protocol_bundle()
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "ssm-milestone-0-v1",
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
        "prior_challenger": {
            "name": "OSR_v1",
            "pipeline_run_id": "d4ec1201a3394548bd4299d8f17647e4",
            "qualified_candidates": 0,
            "decision": "rejected_no_baseline_change",
        },
        "evidence_basis": [
            {
                "title": (
                    "Short-Term Return Predictability and Repetitive Institutional "
                    "Net Order Activity"
                ),
                "doi": "10.1111/jfir.12131",
                "market": "NSE India",
            },
            {
                "title": "Intraday Patterns in the Cross-section of Stock Returns",
                "doi": "10.1111/j.1540-6261.2010.01573.x",
                "market": "United States",
            },
        ],
        "plan_path": str(plan_path),
        "plan_sha256": digest(plan_path),
        "implementation_sha256": digest(Path(__file__)),
        **bundle,
        "next_milestone": "causal_same_slot_feature_event_and_outcome_engine",
        "limitations": [
            (
                "Published return predictability is a hypothesis source, not proof of "
                "tradable edge in current NSE data."
            ),
            (
                "The local data contain OHLCV, not masked institutional order identities "
                "or quote midpoints."
            ),
            "All existing 120-session history is consumed development evidence.",
            "The initial cohort is the liquid 50-stock pilot, not the whole NSE universe.",
            "Two research geometries are below the production 2.0 net R:R gate.",
            "No production scanner, manager, risk rule, alert, dashboard, or order path reads SSM.",
        ],
    }
    write_json(report_path, report)
    write_json(
        output / "ssm/protocol/latest.json",
        {"id": run_id, "path": str(report_path), "bundle_sha256": bundle["bundle_sha256"]},
    )
    return report
