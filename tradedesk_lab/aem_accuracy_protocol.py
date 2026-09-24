"""Frozen accuracy and availability gates for same-session AEM research.

This module evaluates evidence; it cannot create a signal or make a strategy live.
Historical, OOS and prospective counts remain separate inputs so development results
cannot be presented as fresh evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class AemAccuracyProtocol:
    version: str = "aem-accuracy-v1"
    primary_track: str = "same_session"
    strategy_version: str = "AEM_v1"
    label_version: str = "aem-quick-net-target-v2"
    strict_success: str = "executable_fill_then_target_before_stop_or_deadline_and_net_pnl_gt_0"
    max_selected_calls_per_session: int = 3
    reported_top_k_policies: tuple[int, ...] = (1, 2, 3)
    session_thresholds: tuple[float, ...] = (0.70, 0.80)
    minimum_session_target_fraction: float = 0.80
    minimum_prospective_resolved: int = 100
    minimum_prospective_active_sessions: int = 30
    minimum_prospective_observed_rate: float = 0.80
    minimum_prospective_wilson95_lower: float = 0.70
    minimum_total_resolved: int = 500
    minimum_oos_resolved: int = 100
    minimum_eligibility_observed_rate: float = 0.80
    minimum_mean_net_r: float = 0.0
    minimum_random_advantage_r: float = 0.10
    unresolved_selected_allowed: int = 0
    stress_cases: tuple[str, ...] = (
        "base_costs",
        "costs_1.25x",
        "costs_1.5x",
        "slippage_2x",
        "one_bar_delay",
        "missed_fills",
    )

    def __post_init__(self) -> None:
        if self.primary_track != "same_session" or self.max_selected_calls_per_session != 3:
            raise ValueError("AEM accuracy v1 is frozen to same-session top-1/2/3 selection")
        if self.reported_top_k_policies != (1, 2, 3):
            raise ValueError("top-k policies must remain 1, 2 and 3")
        rates = (
            *self.session_thresholds,
            self.minimum_session_target_fraction,
            self.minimum_prospective_observed_rate,
            self.minimum_prospective_wilson95_lower,
            self.minimum_eligibility_observed_rate,
        )
        if any(not 0 <= value <= 1 for value in rates):
            raise ValueError("accuracy thresholds must be proportions")
        if self.session_thresholds != (0.70, 0.80):
            raise ValueError("session thresholds must remain 70% and 80%")
        if (
            self.minimum_prospective_resolved < 1
            or self.minimum_prospective_active_sessions < 1
            or self.minimum_total_resolved < self.minimum_prospective_resolved
            or self.minimum_oos_resolved < self.minimum_prospective_resolved
        ):
            raise ValueError("sample-size gates are inconsistent")
        if self.unresolved_selected_allowed != 0 or not self.stress_cases:
            raise ValueError(
                "the protocol must fail closed on unresolved calls and stress economics"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_AEM_ACCURACY_PROTOCOL = AemAccuracyProtocol()


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def assess_accuracy(
    evidence: dict[str, Any],
    *,
    stage: Literal["prospective_review", "eligibility"],
    protocol: AemAccuracyProtocol = DEFAULT_AEM_ACCURACY_PROTOCOL,
) -> dict[str, Any]:
    """Apply every registered gate and return explicit failures.

    Required economic values are mean net R per selected resolved fill. The random
    baseline uses the same population and execution contract. Session fractions use
    fully resolved active sessions; zero-call sessions stay in availability reporting.
    """
    required = {
        "resolved_trades",
        "oos_resolved_trades",
        "prospective_resolved_trades",
        "prospective_active_sessions",
        "strict_success_rate",
        "strict_success_wilson95_lower",
        "mean_net_r",
        "random_baseline_mean_net_r",
        "active_sessions_at_least_70pct",
        "active_sessions_at_least_80pct",
        "unresolved_selected_calls",
        "stress_mean_net_r",
        "zero_call_sessions",
        "evaluation_sessions",
    }
    missing = required.difference(evidence)
    if missing:
        raise ValueError(f"accuracy evidence is missing {sorted(missing)}")
    counts = {
        name: _count(evidence[name], name)
        for name in (
            "resolved_trades",
            "oos_resolved_trades",
            "prospective_resolved_trades",
            "prospective_active_sessions",
            "unresolved_selected_calls",
            "zero_call_sessions",
            "evaluation_sessions",
        )
    }
    if counts["zero_call_sessions"] > counts["evaluation_sessions"]:
        raise ValueError("zero-call sessions exceed the evaluation calendar")
    values = {
        name: _finite(evidence[name], name)
        for name in (
            "strict_success_rate",
            "strict_success_wilson95_lower",
            "mean_net_r",
            "random_baseline_mean_net_r",
            "active_sessions_at_least_70pct",
            "active_sessions_at_least_80pct",
            "stress_mean_net_r",
        )
    }
    for name in (
        "strict_success_rate",
        "strict_success_wilson95_lower",
        "active_sessions_at_least_70pct",
        "active_sessions_at_least_80pct",
    ):
        if not 0 <= values[name] <= 1:
            raise ValueError(f"{name} must be a proportion")

    checks: dict[str, bool] = {
        "no_unresolved_selected_calls": counts["unresolved_selected_calls"]
        <= protocol.unresolved_selected_allowed,
        "positive_mean_net_r": values["mean_net_r"] > protocol.minimum_mean_net_r,
        "positive_stress_mean_net_r": values["stress_mean_net_r"] > protocol.minimum_mean_net_r,
        "sessions_at_least_70pct": values["active_sessions_at_least_70pct"]
        >= protocol.minimum_session_target_fraction,
        "sessions_at_least_80pct": values["active_sessions_at_least_80pct"]
        >= protocol.minimum_session_target_fraction,
    }
    if stage == "prospective_review":
        checks.update(
            prospective_resolved=counts["prospective_resolved_trades"]
            >= protocol.minimum_prospective_resolved,
            prospective_active_sessions=counts["prospective_active_sessions"]
            >= protocol.minimum_prospective_active_sessions,
            prospective_observed_rate=values["strict_success_rate"]
            >= protocol.minimum_prospective_observed_rate,
            prospective_wilson_lower=values["strict_success_wilson95_lower"]
            >= protocol.minimum_prospective_wilson95_lower,
        )
    elif stage == "eligibility":
        checks.update(
            total_resolved=counts["resolved_trades"] >= protocol.minimum_total_resolved,
            oos_resolved=counts["oos_resolved_trades"] >= protocol.minimum_oos_resolved,
            observed_rate=values["strict_success_rate"]
            >= protocol.minimum_eligibility_observed_rate,
            random_advantage=values["mean_net_r"] - values["random_baseline_mean_net_r"]
            >= protocol.minimum_random_advantage_r,
        )
    else:  # pragma: no cover - Literal protects typed callers
        raise ValueError("unknown accuracy assessment stage")
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "stage": stage,
        "protocol_version": protocol.version,
        "protocol_sha256": protocol.sha256,
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "availability": {
            "evaluation_sessions": counts["evaluation_sessions"],
            "zero_call_sessions": counts["zero_call_sessions"],
            "active_session_coverage": (
                (counts["evaluation_sessions"] - counts["zero_call_sessions"])
                / counts["evaluation_sessions"]
                if counts["evaluation_sessions"]
                else None
            ),
        },
    }
