import pytest
from tradedesk_lab.aem_scorecard import CORE_METRICS, PROMOTION_GATES, build_accuracy_scorecard


def _manifest():
    return {
        "id": "a" * 32,
        "accuracy_protocol_sha256": "protocol",
        "accuracy_protocol": {
            "minimum_random_advantage_r": 0.1,
            "minimum_prospective_resolved": 100,
            "minimum_prospective_active_sessions": 30,
            "minimum_prospective_observed_rate": 0.8,
            "minimum_prospective_wilson95_lower": 0.7,
        },
        "diagnostics": {
            "overall": {
                "trade_decisions": 12,
                "resolved_trades": 10,
                "unfilled_decisions": 2,
                "unresolved_decisions": 0,
                "strict_successes": 8,
                "strict_success_rate": 0.8,
                "strict_success_wilson95": {"confidence": 0.95, "lower": 0.49, "upper": 0.94},
                "mean_net_r": 0.2,
            },
            "session_coverage": {
                "evaluation_sessions": 10,
                "active_sessions": 8,
                "fully_resolved_active_sessions": 8,
                "zero_trade_decision_sessions": 2,
                "active_sessions_at_least_70pct": 0.75,
                "active_sessions_at_least_80pct": 0.625,
            },
        },
    }


def test_scorecard_has_one_frozen_vocabulary_and_pending_gates():
    scorecard = build_accuracy_scorecard(_manifest())
    assert tuple(scorecard["core"]) == CORE_METRICS
    assert tuple(scorecard["promotion_gates"]) == PROMOTION_GATES
    assert scorecard["core"]["calls_issued"]["value"] == 12
    assert scorecard["core"]["active_session_coverage"]["value"] == 0.8
    assert scorecard["core"]["strict_success_rate"]["denominator"] == 10
    assert {gate["status"] for gate in scorecard["promotion_gates"].values()} == {"pending"}
    assert scorecard["all_promotion_gates_pass"] is False
    assert scorecard["improvement_policy"]["required_comparisons"] == [
        "strict_success_rate",
        "wilson95_lower",
        "mean_net_r",
        "active_session_coverage",
        "sessions_at_least_70pct",
        "sessions_at_least_80pct",
    ]


def test_scorecard_attaches_matching_gate_evidence_without_changing_core():
    manifest = _manifest()
    scorecard = build_accuracy_scorecard(
        manifest,
        benchmark_report={
            "id": "benchmark",
            "dataset_id": manifest["id"],
            "comparison": {"comparison": {"actual_minus_null_mean_net_r": 0.12}},
        },
        stress_report={"id": "stress", "dataset_id": manifest["id"], "minimum_mean_net_r": -0.01},
        portfolio_report={
            "id": "portfolio",
            "dataset_id": manifest["id"],
            "mean_net_r_after_constraints": 0.05,
        },
        prospective_report={
            "id": "forward",
            "protocol_sha256": manifest["accuracy_protocol_sha256"],
            "resolved_trades": 120,
            "active_sessions": 35,
            "strict_success_rate": 0.82,
            "strict_success_wilson95_lower": 0.73,
        },
    )
    assert scorecard["promotion_gates"]["matched_random_advantage"]["passed"] is True
    assert scorecard["promotion_gates"]["stress_economics"]["passed"] is False
    assert scorecard["promotion_gates"]["portfolio_replay"]["passed"] is True
    assert scorecard["promotion_gates"]["prospective_evidence"]["passed"] is True
    assert scorecard["all_promotion_gates_pass"] is False


def test_scorecard_rejects_mismatched_or_contradictory_evidence():
    manifest = _manifest()
    with pytest.raises(ValueError, match="does not match"):
        build_accuracy_scorecard(
            manifest,
            benchmark_report={
                "dataset_id": "b" * 32,
                "comparison": {"comparison": {"actual_minus_null_mean_net_r": 0.2}},
            },
        )
    manifest["diagnostics"]["overall"]["strict_successes"] = 9
    with pytest.raises(ValueError, match="contradicts"):
        build_accuracy_scorecard(manifest)
