"""Canonical read-only AEM accuracy scorecard shared by artifacts and dashboards."""

from __future__ import annotations

import math
from typing import Any

CORE_METRICS = (
    "calls_issued",
    "filled_calls",
    "unfilled_calls",
    "unresolved_calls",
    "strict_wins",
    "strict_success_rate",
    "wilson95_lower",
    "mean_net_r",
    "zero_call_sessions",
    "active_session_coverage",
    "sessions_at_least_70pct",
    "sessions_at_least_80pct",
)

PROMOTION_GATES = (
    "matched_random_advantage",
    "stress_economics",
    "portfolio_replay",
    "prospective_evidence",
)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _metric(label: str, value: int | float | None, unit: str, denominator: int | None) -> dict:
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "denominator": denominator,
        "available": value is not None,
    }


def _pending_gate(label: str, requirement: str) -> dict:
    return {
        "label": label,
        "status": "pending",
        "passed": None,
        "value": None,
        "requirement": requirement,
        "source_id": None,
    }


def _require_matching_source(report: dict, dataset_id: str, name: str) -> None:
    if report.get("dataset_id") != dataset_id:
        raise ValueError(f"{name} evidence does not match the scorecard dataset")


def build_accuracy_scorecard(
    manifest: dict[str, Any],
    *,
    benchmark_report: dict[str, Any] | None = None,
    stress_report: dict[str, Any] | None = None,
    portfolio_report: dict[str, Any] | None = None,
    prospective_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable metric vocabulary without inventing absent evidence."""

    diagnostics = manifest["diagnostics"]
    overall = diagnostics["overall"]
    sessions = diagnostics["session_coverage"]
    protocol = manifest["accuracy_protocol"]
    dataset_id = manifest["id"]
    evaluation_sessions = _count(sessions["evaluation_sessions"], "evaluation_sessions")
    active_sessions = _count(sessions["active_sessions"], "active_sessions")
    complete_active = _count(
        sessions["fully_resolved_active_sessions"], "fully_resolved_active_sessions"
    )
    if active_sessions > evaluation_sessions or complete_active > active_sessions:
        raise ValueError("session scorecard counts are inconsistent")
    calls = _count(overall["trade_decisions"], "trade_decisions")
    fills = _count(overall["resolved_trades"], "resolved_trades")
    unfilled = _count(overall["unfilled_decisions"], "unfilled_decisions")
    unresolved = _count(overall["unresolved_decisions"], "unresolved_decisions")
    wins = _count(overall["strict_successes"], "strict_successes")
    if fills + unfilled + unresolved != calls or wins > fills:
        raise ValueError("call outcome counts are inconsistent")
    rate = overall["strict_success_rate"]
    if rate is not None:
        rate = _finite(rate, "strict_success_rate")
        if not 0 <= rate <= 1 or abs(rate - wins / fills) > 1e-12:
            raise ValueError("strict success rate contradicts win and fill counts")
    elif fills:
        raise ValueError("filled calls require a strict success rate")
    wilson = overall["strict_success_wilson95"]
    lower = wilson["lower"]
    upper = wilson["upper"]
    if lower is not None:
        lower, upper = _finite(lower, "wilson lower"), _finite(upper, "wilson upper")
        if not 0 <= lower <= upper <= 1:
            raise ValueError("Wilson interval is invalid")
    mean_net_r = overall["mean_net_r"]
    if mean_net_r is not None:
        mean_net_r = _finite(mean_net_r, "mean_net_r")
    zero_calls = _count(sessions["zero_trade_decision_sessions"], "zero_call_sessions")
    session_70 = sessions["active_sessions_at_least_70pct"]
    session_80 = sessions["active_sessions_at_least_80pct"]
    for name, value in (
        ("sessions_at_least_70pct", session_70),
        ("sessions_at_least_80pct", session_80),
    ):
        if value is not None and not 0 <= _finite(value, name) <= 1:
            raise ValueError(f"{name} must be a proportion")

    core = {
        "calls_issued": _metric("Calls issued", calls, "count", evaluation_sessions),
        "filled_calls": _metric("Filled calls", fills, "count", calls),
        "unfilled_calls": _metric("Unfilled calls", unfilled, "count", calls),
        "unresolved_calls": _metric("Unresolved calls", unresolved, "count", calls),
        "strict_wins": _metric("Strict wins", wins, "count", fills),
        "strict_success_rate": _metric("Strict success rate", rate, "proportion", fills),
        "wilson95_lower": _metric("Wilson 95% lower bound", lower, "proportion", fills),
        "mean_net_r": _metric("Mean net R after costs", mean_net_r, "r", fills),
        "zero_call_sessions": _metric(
            "Zero-call sessions", zero_calls, "count", evaluation_sessions
        ),
        "active_session_coverage": _metric(
            "Active-session coverage",
            active_sessions / evaluation_sessions if evaluation_sessions else None,
            "proportion",
            evaluation_sessions,
        ),
        "sessions_at_least_70pct": _metric(
            "Active sessions reaching 70%", session_70, "proportion", complete_active
        ),
        "sessions_at_least_80pct": _metric(
            "Active sessions reaching 80%", session_80, "proportion", complete_active
        ),
    }
    gates = {
        "matched_random_advantage": _pending_gate(
            "Matched-random advantage",
            f">= {protocol['minimum_random_advantage_r']:.2f}R per attempt",
        ),
        "stress_economics": _pending_gate(
            "Worst registered stress", "Mean net R must remain positive"
        ),
        "portfolio_replay": _pending_gate(
            "Portfolio-constrained replay", "Net R must remain positive after constraints"
        ),
        "prospective_evidence": _pending_gate(
            "Prospective evidence",
            (
                f">= {protocol['minimum_prospective_resolved']} resolved calls and "
                f">= {protocol['minimum_prospective_observed_rate']:.0%} strict success"
            ),
        ),
    }
    if benchmark_report is not None:
        _require_matching_source(benchmark_report, dataset_id, "benchmark")
        comparison = benchmark_report.get("comparison", {}).get("comparison")
        if comparison is None:
            raise ValueError("benchmark report has no completed comparison")
        advantage = _finite(
            comparison["actual_minus_null_mean_net_r"], "matched-random advantage"
        )
        threshold = _finite(protocol["minimum_random_advantage_r"], "random threshold")
        gates["matched_random_advantage"].update(
            status="pass" if advantage >= threshold else "fail",
            passed=advantage >= threshold,
            value=advantage,
            source_id=benchmark_report.get("id"),
        )
    if stress_report is not None:
        _require_matching_source(stress_report, dataset_id, "stress")
        value = _finite(stress_report["minimum_mean_net_r"], "stress minimum mean net R")
        gates["stress_economics"].update(
            status="pass" if value > 0 else "fail",
            passed=value > 0,
            value=value,
            source_id=stress_report.get("id"),
        )
    if portfolio_report is not None:
        _require_matching_source(portfolio_report, dataset_id, "portfolio")
        value = _finite(
            portfolio_report["mean_net_r_after_constraints"], "portfolio mean net R"
        )
        gates["portfolio_replay"].update(
            status="pass" if value > 0 else "fail",
            passed=value > 0,
            value=value,
            source_id=portfolio_report.get("id"),
        )
    if prospective_report is not None:
        if prospective_report.get("protocol_sha256") != manifest["accuracy_protocol_sha256"]:
            raise ValueError("prospective evidence does not match the accuracy protocol")
        resolved = _count(prospective_report["resolved_trades"], "prospective resolved")
        observed = _finite(prospective_report["strict_success_rate"], "prospective rate")
        lower_bound = _finite(
            prospective_report["strict_success_wilson95_lower"], "prospective Wilson lower"
        )
        active = _count(prospective_report["active_sessions"], "prospective active sessions")
        passed = (
            resolved >= protocol["minimum_prospective_resolved"]
            and active >= protocol["minimum_prospective_active_sessions"]
            and observed >= protocol["minimum_prospective_observed_rate"]
            and lower_bound >= protocol["minimum_prospective_wilson95_lower"]
        )
        gates["prospective_evidence"].update(
            status="pass" if passed else "fail",
            passed=passed,
            value={
                "resolved_trades": resolved,
                "active_sessions": active,
                "strict_success_rate": observed,
                "wilson95_lower": lower_bound,
            },
            source_id=prospective_report.get("id"),
        )
    if tuple(core) != CORE_METRICS or tuple(gates) != PROMOTION_GATES:
        raise AssertionError("canonical scorecard vocabulary changed")
    return {
        "version": "aem-scorecard-v1",
        "dataset_id": dataset_id,
        "protocol_sha256": manifest["accuracy_protocol_sha256"],
        "eligible_for_live": False,
        "core": core,
        "confidence_interval": {"confidence": 0.95, "lower": lower, "upper": upper},
        "promotion_gates": gates,
        "all_promotion_gates_pass": all(gate["passed"] is True for gate in gates.values()),
        "improvement_policy": {
            "primary_goal": "Improve strict predictive accuracy and its Wilson lower bound.",
            "required_comparisons": [
                "strict_success_rate",
                "wilson95_lower",
                "mean_net_r",
                "active_session_coverage",
                "sessions_at_least_70pct",
                "sessions_at_least_80pct",
            ],
            "constraints": [
                "Do not claim improvement by silently excluding losses or unresolved calls.",
                "Do not claim improvement through excessive abstention or worse "
                "after-cost economics.",
                "Every experiment must identify its frozen baseline dataset and report deltas.",
            ],
        },
        "definitions": {
            "calls_issued": "Every TRADE decision, whether filled or not.",
            "filled_calls": "Resolved executable fills; the strict-success denominator.",
            "active_sessions": "Sessions with at least one resolved fill.",
            "pending_gate": "Required evidence artifact is absent; pending never means pass.",
        },
    }
