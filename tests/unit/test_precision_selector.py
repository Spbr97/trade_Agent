from __future__ import annotations

from tradedesk.precision_selector import (
    SelectorPolicy,
    evaluate_precision_selector,
    fit_selector_policy,
    selector_decision,
)


def _row(
    signal_id: str,
    *,
    label: int = 1,
    session: str = "2026-09-01",
    role: str = "recommended",
    setup: str = "base_breakout",
) -> dict:  # type: ignore[type-arg]
    return {
        "signal_id": signal_id,
        "label": label,
        "armed_on": session,
        "setup": setup,
        "regime": "risk_on",
        "sector": "technology",
        "evidence_role": role,
        "net_r": 0.7 if label else -1.1,
        "features": {
            "decision.rule_score": 90.0,
            "context.relative_strength_percentile": 80.0,
            "context.atr_pct": 0.02,
            "context.average_turnover": 10_000_000.0,
            "execution.estimated_round_trip_cost": 5.0,
            "execution.position_value": 1_000.0,
            "execution.net_rr_t1": 0.7,
            "context.score_components.trend": 0.8,
            "context.score_components.sector": 0.8,
            "context.score_components.pattern": 0.8,
            "context.score_components.room": 0.8,
            "context.score_components.net_rr": 0.8,
            "context.score_components.regime": 1.0,
        },
    }


def test_selector_can_reject_but_never_promote_counterfactual() -> None:
    row = _row("rejected", role="counterfactual")
    decision = selector_decision(row, 0.99, SelectorPolicy("nse", ()))
    assert not decision["eligible_for_ranking"]
    assert "not_a_qualified_call" in decision["reasons"]
    assert decision["authority"] == "research_only_reject_or_downgrade"


def test_missing_risk_inputs_fail_closed() -> None:
    row = _row("missing")
    row["features"] = {"decision.rule_score": 95.0}
    decision = selector_decision(row, 0.95, SelectorPolicy("nse", ()))
    assert not decision["eligible_for_ranking"]
    assert "liquidity_unavailable" in decision["reasons"]
    assert "estimated_slippage_or_cost_too_high_or_unavailable" in decision["reasons"]


def test_recurrent_development_failure_pattern_is_blacklisted() -> None:
    development = [_row(f"loss-{i}", label=0) for i in range(6)]
    probabilities = {str(row["signal_id"]): 0.85 for row in development}
    policy = fit_selector_policy(development, probabilities, market="nse")
    decision = selector_decision(_row("future"), 0.85, policy)
    assert policy.failed_patterns
    assert "similar_historical_failure_pattern" in decision["reasons"]


def test_all_preregistered_operating_points_report_coverage_and_no_call_sessions() -> None:
    development = [
        _row(f"dev-{i}", label=i % 2, session=f"2026-08-{1 + i // 2:02d}")
        for i in range(12)
    ]
    test = [
        _row(f"test-{i}", label=int(i in {0, 2, 4}), session=f"2026-09-{1 + i // 2:02d}")
        for i in range(6)
    ]
    development_p = {str(row["signal_id"]): 0.55 + 0.3 * int(row["label"]) for row in development}
    test_p = {str(row["signal_id"]): 0.55 + 0.3 * int(row["label"]) for row in test}
    report = evaluate_precision_selector(
        development, test, development_p, test_p, market="nse"
    )
    assert set(report["operating_points"]) == {
        "top_1_per_session",
        "top_3_per_session",
        "top_5_per_session",
        "confidence_60",
        "confidence_70",
        "confidence_80",
    }
    assert report["operating_points"]["top_1_per_session"]["selected"] == 3
    assert report["operating_points"]["confidence_80"]["strict_accuracy"] == 1.0
    assert report["selected_live_policy"] is None
    assert report["can_only_remove_or_downgrade"] is True
    assert report["promotion_authorized"] is False
