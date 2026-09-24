import copy
import json

import pytest
from tradedesk_lab.aem_accuracy_protocol import (
    DEFAULT_AEM_ACCURACY_PROTOCOL,
    AemAccuracyProtocol,
    assess_accuracy,
)


def _passing():
    return {
        "resolved_trades": 500,
        "oos_resolved_trades": 100,
        "prospective_resolved_trades": 100,
        "prospective_active_sessions": 40,
        "strict_success_rate": 0.84,
        "strict_success_wilson95_lower": 0.75,
        "mean_net_r": 0.15,
        "random_baseline_mean_net_r": 0.04,
        "active_sessions_at_least_70pct": 0.80,
        "active_sessions_at_least_80pct": 0.80,
        "unresolved_selected_calls": 0,
        "stress_mean_net_r": 0.01,
        "zero_call_sessions": 10,
        "evaluation_sessions": 50,
    }


def test_frozen_protocol_is_stable_json_and_matches_existing_evidence_gate():
    protocol = DEFAULT_AEM_ACCURACY_PROTOCOL
    assert protocol.reported_top_k_policies == (1, 2, 3)
    assert protocol.minimum_total_resolved == 500
    assert protocol.minimum_oos_resolved == 100
    assert protocol.minimum_eligibility_observed_rate == 0.80
    assert protocol.minimum_random_advantage_r == 0.10
    assert len(protocol.sha256) == 64
    json.dumps(protocol.to_dict(), allow_nan=False)


def test_prospective_review_requires_accuracy_uncertainty_sessions_and_economics():
    evidence = _passing()
    result = assess_accuracy(evidence, stage="prospective_review")
    assert result["passed"] is True
    assert result["availability"]["active_session_coverage"] == 0.8
    for field, value, failure in (
        ("prospective_resolved_trades", 99, "prospective_resolved"),
        ("prospective_active_sessions", 29, "prospective_active_sessions"),
        ("strict_success_rate", 0.79, "prospective_observed_rate"),
        ("strict_success_wilson95_lower", 0.69, "prospective_wilson_lower"),
        ("active_sessions_at_least_70pct", 0.79, "sessions_at_least_70pct"),
        ("active_sessions_at_least_80pct", 0.79, "sessions_at_least_80pct"),
        ("mean_net_r", 0.0, "positive_mean_net_r"),
        ("stress_mean_net_r", 0.0, "positive_stress_mean_net_r"),
        ("unresolved_selected_calls", 1, "no_unresolved_selected_calls"),
    ):
        failing = copy.deepcopy(evidence)
        failing[field] = value
        result = assess_accuracy(failing, stage="prospective_review")
        assert result["passed"] is False and failure in result["failures"]


def test_eligibility_requires_existing_gate_plus_session_and_stress_quality():
    evidence = _passing()
    assert assess_accuracy(evidence, stage="eligibility")["passed"] is True
    evidence["random_baseline_mean_net_r"] = 0.051
    result = assess_accuracy(evidence, stage="eligibility")
    assert result["passed"] is False
    assert "random_advantage" in result["failures"]


def test_zero_calls_are_visible_and_never_create_success():
    evidence = _passing()
    evidence["evaluation_sessions"] = 0
    evidence["zero_call_sessions"] = 0
    evidence["prospective_resolved_trades"] = 0
    evidence["prospective_active_sessions"] = 0
    evidence["strict_success_rate"] = 0.0
    evidence["strict_success_wilson95_lower"] = 0.0
    result = assess_accuracy(evidence, stage="prospective_review")
    assert result["passed"] is False
    assert result["availability"]["active_session_coverage"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strict_success_rate", float("nan")),
        ("strict_success_wilson95_lower", 1.1),
        ("resolved_trades", -1),
        ("evaluation_sessions", 2.5),
        ("zero_call_sessions", True),
    ],
)
def test_malformed_evidence_fails_closed(field, value):
    evidence = _passing()
    evidence[field] = value
    with pytest.raises(ValueError):
        assess_accuracy(evidence, stage="eligibility")


def test_missing_evidence_and_invalid_protocols_are_rejected():
    evidence = _passing()
    evidence.pop("stress_mean_net_r")
    with pytest.raises(ValueError, match="missing"):
        assess_accuracy(evidence, stage="eligibility")
    with pytest.raises(ValueError):
        AemAccuracyProtocol(session_thresholds=(0.69, 0.80))
    with pytest.raises(ValueError, match="exceed"):
        malformed = _passing()
        malformed["zero_call_sessions"] = 51
        assess_accuracy(malformed, stage="prospective_review")
