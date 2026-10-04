from __future__ import annotations

import json
from pathlib import Path

from tradedesk_lab.accuracy_prospective_qualification import (
    canonical_sha256,
    evaluate_qualification,
    run_qualification,
)


def _fixture(*, pass_all: bool = True):
    activation = {
        "version": "accuracy-prospective-shadow-v1",
        "candidate": {
            "setup": "trend_pullback",
            "entry_mode": "next_session_open",
            "probability_threshold": 0.55,
            "top_k_per_arming_session": 2,
        },
    }
    accuracy = 0.85
    expectancy = 0.12
    m8_gates = {
        "resolved_calls": True,
        "active_sessions": True,
        "accuracy": pass_all,
        "wilson_lower_bound": pass_all,
        "session_target_rate": pass_all,
        "expectancy_r": pass_all,
    }
    m8_summary = {
        "status": "prospective_pass" if pass_all else "prospective_fail",
        "selected_calls": 100,
        "resolved_calls": 100,
        "wins": 85,
        "accuracy": accuracy,
        "wilson_lower_bound": 0.77,
        "active_sessions": 30,
        "session_target_rate": 0.73,
        "expectancy_r": expectancy,
        "gate_checks": m8_gates,
    }
    m8 = {"activation": activation, "summary": m8_summary}
    m9 = {
        "version": "accuracy-prospective-integrity-v1",
        "shadow_activation": activation,
        "shadow_summary": m8_summary,
        "integrity": {"passed": True},
        "double_slippage_stress": {
            "status": "complete",
            "resolved_calls": 100,
            "accuracy": 0.82,
            "expectancy_r": 0.05,
            "positive_expectancy": True,
        },
        "uncertainty": {
            "session_cluster_lower_95": 0.72,
            "week_cluster_lower_95": 0.71,
        },
        "review_ready": pass_all,
    }
    control_gates = {
        "resolved_calls": True,
        "active_sessions": True,
        "advantage_r": pass_all,
        "p_value": pass_all,
    }
    activation_hash = canonical_sha256(activation)
    m10 = {
        "version": "accuracy-prospective-control-v1",
        "m8_activation_sha256": activation_hash,
        "outcome_parity": {"passed": True, "checked_selected_calls": 100},
        "summary": {
            "status": "selection_control_pass" if pass_all else "selection_control_fail",
            "resolved_model_calls": 100,
            "mature_sessions": 30,
            "model_accuracy": accuracy,
            "model_expectancy_r": expectancy,
            "selection_advantage_r": 0.11 if pass_all else 0.02,
            "p_value": 0.01 if pass_all else 0.40,
            "gate_checks": control_gates,
        },
    }
    m11 = {
        "version": "accuracy-prospective-timing-v1",
        "m8_activation_sha256": activation_hash,
        "source_integrity": {"passed": True},
        "summary": {
            "status": "random_timing_pass" if pass_all else "random_timing_fail",
            "paired_resolved_calls": 100,
            "active_sessions": 30,
            "model_accuracy": accuracy,
            "model_expectancy_r": expectancy,
            "timing_advantage_r": 0.12 if pass_all else 0.01,
            "p_value": 0.01 if pass_all else 0.50,
            "gate_checks": control_gates,
        },
    }
    return m8, m9, m10, m11


def test_complete_pass_only_authorizes_human_review() -> None:
    report = evaluate_qualification(*_fixture(), created_at="2026-10-04T00:00:00+00:00")
    assert report["status"] == "human_review_authorized"
    assert report["qualification_passed"] is True
    assert report["review_authorized"] is True
    assert report["baseline_improved"] is False
    assert report["eligible_for_live"] is False
    assert report["canonical_baseline"]["strict_success_rate"] == 149 / 693
    assert report["locked_historical_challenger"]["evidence_class"] == (
        "historical_locked_not_prospective"
    )


def test_missing_component_is_not_available_never_pass() -> None:
    m8, m9, m10, _ = _fixture()
    report = evaluate_qualification(m8, m9, m10, None)
    assert report["status"] == "not_available"
    assert report["components"]["m11_timing_control"]["available"] is False
    assert report["gate_checks"]["all_components_available"] is False
    assert report["qualification_passed"] is False
    assert report["eligible_for_live"] is False


def test_mature_failed_gate_rejects_candidate() -> None:
    report = evaluate_qualification(*_fixture(pass_all=False))
    assert report["status"] == "prospective_rejected"
    assert report["components"]["m8_accuracy"]["ready"] is True
    assert report["components"]["m8_accuracy"]["passed"] is False
    assert report["review_authorized"] is False


def test_missing_gate_key_cannot_be_interpreted_as_pass() -> None:
    m8, m9, m10, m11 = _fixture()
    del m10["summary"]["gate_checks"]["p_value"]
    report = evaluate_qualification(m8, m9, m10, m11)
    assert report["status"] == "prospective_rejected"
    assert report["components"]["m10_selection_control"]["passed"] is False
    assert report["components"]["m10_selection_control"]["gates"]["p_value"] is False
    assert report["qualification_passed"] is False


def test_collecting_is_distinct_from_failure() -> None:
    m8, m9, m10, m11 = _fixture()
    m8["summary"].update(
        status="collecting_insufficient_evidence",
        selected_calls=0,
        resolved_calls=0,
        wins=0,
        accuracy=None,
        wilson_lower_bound=None,
        active_sessions=0,
        session_target_rate=None,
        expectancy_r=None,
    )
    m8["summary"]["gate_checks"] = {name: False for name in m8["summary"]["gate_checks"]}
    m9["shadow_summary"] = m8["summary"]
    m9["double_slippage_stress"] = {
        "status": "complete",
        "resolved_calls": 0,
        "accuracy": None,
        "expectancy_r": None,
        "positive_expectancy": False,
    }
    m9["uncertainty"] = {
        "session_cluster_lower_95": None,
        "week_cluster_lower_95": None,
    }
    m9["review_ready"] = False
    for control in (m10, m11):
        summary = control["summary"]
        summary["status"] = "collecting_insufficient_evidence"
        summary["gate_checks"] = {name: False for name in summary["gate_checks"]}
        summary["model_accuracy"] = None
        summary["model_expectancy_r"] = None
    m10["summary"].update(resolved_model_calls=0, mature_sessions=0)
    m10["outcome_parity"]["checked_selected_calls"] = 0
    m11["summary"].update(paired_resolved_calls=0, active_sessions=0)
    report = evaluate_qualification(m8, m9, m10, m11)
    assert report["status"] == "collecting_insufficient_evidence"
    assert report["gate_checks"]["all_components_ready"] is False
    assert report["baseline_improved"] is False


def test_candidate_identity_mismatch_is_degraded() -> None:
    m8, m9, m10, m11 = _fixture()
    m11["m8_activation_sha256"] = "0" * 64
    report = evaluate_qualification(m8, m9, m10, m11)
    assert report["status"] == "degraded"
    assert "m11_activation" in report["parity"]["failures"]
    assert report["review_authorized"] is False


def test_complete_evidence_requires_exact_call_metric_parity() -> None:
    m8, m9, m10, m11 = _fixture()
    m10["summary"]["model_expectancy_r"] = 0.13
    report = evaluate_qualification(m8, m9, m10, m11)
    assert report["status"] == "degraded"
    assert "m10_expectancy" in report["parity"]["failures"]
    assert report["gate_checks"]["candidate_and_evaluation_parity"] is False


def test_runner_writes_explicit_unavailable_report(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("m8.json", "m9.json", "m10.json", "m11.json")]
    report = run_qualification(
        output=tmp_path / "out",
        m8_path=paths[0],
        m9_path=paths[1],
        m10_path=paths[2],
        m11_path=paths[3],
    )
    saved = json.loads((tmp_path / "out" / "latest.json").read_text(encoding="utf-8"))
    assert report["status"] == saved["status"] == "not_available"
    assert all(not row["available"] for row in saved["source_artifacts"].values())
    assert saved["qualification_passed"] is False
