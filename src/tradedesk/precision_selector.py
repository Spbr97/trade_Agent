"""Research-only second-stage selector that can reject calls, never promote them."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from tradedesk.engine.scoring import wilson_lower_bound

SELECTOR_VERSION = "precision-selector-v1"
CONFIDENCE_THRESHOLDS = (0.60, 0.70, 0.80)
TOP_K_LIMITS = (1, 3, 5)
MIN_PATTERN_ROWS = 5
FAILURE_PATTERN_RATE = 0.60


@dataclass(frozen=True)
class SelectorPolicy:
    market: str
    failed_patterns: tuple[str, ...]
    minimum_rule_score: float = 70.0
    minimum_relative_strength: float = 50.0
    minimum_net_rr_t1: float = 0.0
    minimum_atr_pct: float = 0.003
    maximum_atr_pct: float = 0.15
    maximum_cost_fraction: float = 0.02
    version: str = SELECTOR_VERSION


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confidence_band(probability: float) -> str:
    if probability < 0.60:
        return "lt60"
    if probability < 0.70:
        return "60_69"
    if probability < 0.80:
        return "70_79"
    return "80_plus"


def _pattern_key(row: Mapping[str, Any], probability: float) -> str:
    return "|".join(
        (
            str(row.get("setup") or "unavailable"),
            str(row.get("regime") or "unavailable"),
            str(row.get("sector") or "unavailable"),
            _confidence_band(probability),
        )
    )


def fit_selector_policy(
    rows: Sequence[Mapping[str, Any]], probabilities: Mapping[str, float], *, market: str
) -> SelectorPolicy:
    """Fit only a shrink-free failure-pattern blacklist; all other gates are frozen."""

    patterns: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        probability = _finite(probabilities.get(str(row["signal_id"])))
        if probability is None:
            continue
        patterns[_pattern_key(row, probability)].append(int(row["label"]))
    failed = tuple(
        sorted(
            key
            for key, labels in patterns.items()
            if len(labels) >= MIN_PATTERN_ROWS
            and 1.0 - float(np.mean(labels)) >= FAILURE_PATTERN_RATE
        )
    )
    return SelectorPolicy(
        market=market,
        failed_patterns=failed,
        maximum_atr_pct=0.20 if market == "crypto" else 0.08,
        maximum_cost_fraction=0.025 if market == "crypto" else 0.015,
    )


def selector_decision(
    row: Mapping[str, Any], probability: float | None, policy: SelectorPolicy
) -> dict[str, Any]:
    """Return a transparent research decision; rejection authority only."""

    reasons: list[str] = []
    if row.get("evidence_role") != "recommended":
        reasons.append("not_a_qualified_call")
    p = _finite(probability)
    if p is None:
        reasons.append("calibrated_probability_unavailable")
        p = 0.0
    features = row.get("features") or {}
    rule_score = _finite(features.get("decision.rule_score"))
    relative_strength = _finite(features.get("context.relative_strength_percentile"))
    atr_pct = _finite(features.get("context.atr_pct"))
    turnover = _finite(features.get("context.average_turnover"))
    cost = _finite(features.get("execution.estimated_round_trip_cost"))
    position_value = _finite(features.get("execution.position_value"))
    net_rr_t1 = _finite(features.get("execution.net_rr_t1"))
    if rule_score is None or rule_score < policy.minimum_rule_score:
        reasons.append("rule_score_below_precision_floor")
    if relative_strength is None or relative_strength < policy.minimum_relative_strength:
        reasons.append("relative_strength_below_precision_floor_or_unavailable")
    if atr_pct is None or not policy.minimum_atr_pct <= atr_pct <= policy.maximum_atr_pct:
        reasons.append("volatility_outside_precision_range_or_unavailable")
    if turnover is None or turnover <= 0:
        reasons.append("liquidity_unavailable")
    cost_fraction = (
        cost / position_value
        if cost is not None and position_value is not None and position_value > 0
        else None
    )
    if cost_fraction is None or cost_fraction > policy.maximum_cost_fraction:
        reasons.append("estimated_slippage_or_cost_too_high_or_unavailable")
    if net_rr_t1 is None or net_rr_t1 <= policy.minimum_net_rr_t1:
        reasons.append("no_positive_remaining_movement_after_costs")
    pattern = _pattern_key(row, p)
    if pattern in policy.failed_patterns:
        reasons.append("similar_historical_failure_pattern")

    # Existing scanner components already encode trend, sector, volume/pattern confirmation,
    # room to resistance, reward:risk and regime. They may improve ranking but missing
    # optional components cannot turn a rejection into an acceptance.
    components = [
        _finite(features.get(f"context.score_components.{name}"))
        for name in ("trend", "sector", "pattern", "room", "net_rr", "regime")
    ]
    observed_components = [value for value in components if value is not None]
    component_score = float(np.mean(observed_components)) if observed_components else 0.0
    precision_score = (
        0.60 * p
        + 0.20 * ((rule_score or 0.0) / 100.0)
        + 0.10 * ((relative_strength or 0.0) / 100.0)
        + 0.10 * component_score
        - min(cost_fraction or 0.0, 0.10)
    )
    return {
        "signal_id": str(row["signal_id"]),
        "eligible_for_ranking": not reasons,
        "precision_score": precision_score,
        "probability": p,
        "reasons": reasons,
        "failed_pattern": pattern if pattern in policy.failed_patterns else None,
        "authority": "research_only_reject_or_downgrade",
    }


def _operating_point_score(
    selected: list[Mapping[str, Any]], qualified: list[Mapping[str, Any]], sessions: list[str]
) -> dict[str, Any]:
    labels = [int(row["label"]) for row in selected]
    wins = sum(labels)
    net_values = [
        number for row in selected if (number := _finite(row.get("net_r"))) is not None
    ]
    selected_sessions = {str(row.get("armed_on")) for row in selected}
    maximum_losing_streak = 0
    streak = 0
    for label in labels:
        streak = 0 if label else streak + 1
        maximum_losing_streak = max(maximum_losing_streak, streak)
    return {
        "selected": len(selected),
        "wins": wins,
        "strict_accuracy": wins / len(selected) if selected else None,
        "wilson_95_low": wilson_lower_bound(wins, len(selected)) if selected else None,
        "mean_net_r": float(np.mean(net_values)) if net_values else None,
        "coverage_of_qualified": len(selected) / len(qualified) if qualified else 0.0,
        "calls_per_session": len(selected) / len(sessions) if sessions else 0.0,
        "sessions_with_calls": len(selected_sessions),
        "no_call_sessions": len(sessions) - len(selected_sessions),
        "no_call_frequency": (
            (len(sessions) - len(selected_sessions)) / len(sessions) if sessions else 1.0
        ),
        "maximum_losing_streak": maximum_losing_streak,
        "passes_final_accuracy_gate": bool(
            selected
            and wins / len(selected) >= 0.80
            and wilson_lower_bound(wins, len(selected)) >= 0.70
            and net_values
            and float(np.mean(net_values)) > 0
        ),
        "live_authority": False,
    }


def evaluate_precision_selector(
    development_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    development_probabilities: Mapping[str, float],
    test_probabilities: Mapping[str, float],
    *,
    market: str,
) -> dict[str, Any]:
    """Fit on development and evaluate all preregistered operating points once."""

    policy = fit_selector_policy(development_rows, development_probabilities, market=market)
    qualified = [row for row in test_rows if row.get("evidence_role") == "recommended"]
    sessions = sorted({str(row.get("armed_on")) for row in qualified})
    decisions = {
        str(row["signal_id"]): selector_decision(
            row, test_probabilities.get(str(row["signal_id"])), policy
        )
        for row in test_rows
    }
    rankable = [
        row
        for row in qualified
        if decisions[str(row["signal_id"])]["eligible_for_ranking"]
    ]
    rankable.sort(
        key=lambda row: (
            str(row.get("armed_on")),
            -float(decisions[str(row["signal_id"])]["precision_score"]),
            str(row["signal_id"]),
        )
    )
    operating_points: dict[str, Any] = {}
    for top_k in TOP_K_LIMITS:
        selected = []
        for session in sessions:
            session_rows = [row for row in rankable if str(row.get("armed_on")) == session]
            selected.extend(session_rows[:top_k])
        operating_points[f"top_{top_k}_per_session"] = _operating_point_score(
            selected, qualified, sessions
        )
    for threshold in CONFIDENCE_THRESHOLDS:
        selected = [
            row
            for row in rankable
            if decisions[str(row["signal_id"])]["probability"] >= threshold
        ]
        operating_points[f"confidence_{int(threshold * 100)}"] = _operating_point_score(
            selected, qualified, sessions
        )
    rejections: dict[str, int] = defaultdict(int)
    for decision in decisions.values():
        for reason in decision["reasons"]:
            rejections[reason] += 1
    return {
        "version": SELECTOR_VERSION,
        "market": market,
        "policy": asdict(policy),
        "test_rows": len(test_rows),
        "qualified_test_rows": len(qualified),
        "rankable_test_rows": len(rankable),
        "rejection_reasons": dict(sorted(rejections.items())),
        "operating_points": operating_points,
        "decisions": decisions,
        "can_only_remove_or_downgrade": True,
        "rejected_calls_promoted": False,
        "selected_live_policy": None,
        "active_model_changed": False,
        "promotion_authorized": False,
    }
