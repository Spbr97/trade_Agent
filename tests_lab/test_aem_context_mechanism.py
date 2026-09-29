import json
from dataclasses import replace

import pandas as pd
import pytest
from tradedesk_lab.aem_context_mechanism import (
    DEFAULT_CONTEXT_MECHANISM_PROTOCOL,
    ContextMechanismProtocol,
    evaluate_context_mechanism,
    freeze_context_mechanism_protocol,
    prepare_mechanism_population,
    rule_mask,
)


def _sessions():
    return [f"2026-01-{index:03d}" for index in range(1, 121)]


def _strong_population():
    rows = []
    for session_index, session in enumerate(_sessions()):
        for event_index, aligned in enumerate((True, False)):
            value = 0.01 if aligned else -0.01
            row = {
                "event_id": f"event-{session_index:03d}-{event_index}",
                "scrip_code": f"NSE_{event_index + 1}",
                "session_date": session,
                "decision": "TRADE",
                "available_at": f"{session}T09:{20 + event_index:02d}:00+05:30",
                "context_joined": True,
                "context_return_3m_available": True,
                "context_return_5m_available": True,
                "resolved": True,
                "strict_success": aligned,
                "net_r": 0.5 if aligned else -1.0,
            }
            for window in (3, 5):
                for slug in ("nifty50", "bank_nifty", "nifty_financial"):
                    row[f"{slug}_return_{window}m"] = value
            rows.append(row)
    return pd.DataFrame(rows)


def _synthetic_protocol(**changes):
    values = {
        "expected_trade_attempts": 240,
        "expected_resolved_fills": 240,
        "expected_strict_wins": 120,
        "expected_baseline_rate": 0.5,
        "expected_baseline_mean_net_r": -0.25,
    }
    values.update(changes)
    return replace(DEFAULT_CONTEXT_MECHANISM_PROTOCOL, **values)


def _feature_and_event_inputs():
    population = _strong_population()
    features = population.drop(
        columns=["resolved", "strict_success", "net_r"]
    ).copy()
    events = population[
        ["event_id", "scrip_code", "session_date", "decision"]
    ].copy()
    events["status"] = "resolved"
    events["strict_success"] = population.strict_success
    events["net_r"] = population.net_r
    return features, events


def test_protocol_freezes_rules_controls_sample_floors_and_seed():
    protocol = DEFAULT_CONTEXT_MECHANISM_PROTOCOL
    assert [rule.id for rule in protocol.rules] == [
        "nifty50_positive_5m",
        "majority_positive_5m",
        "all_positive_5m",
        "all_positive_3m",
    ]
    assert protocol.evaluation_sessions == 120
    assert protocol.fold_count == 3
    assert protocol.fold_sessions == 40
    assert protocol.shuffle_repetitions == 256
    assert protocol.shuffle_seed == 20260930
    assert protocol.temporal_placebo == "within_session_circular_next_event_rotation"
    assert protocol.minimum_resolved_fills == 100
    assert protocol.minimum_active_sessions == 48
    assert protocol.minimum_active_session_coverage == 0.40
    assert protocol.maximum_shuffle_empirical_p == 0.05
    assert protocol.sha256 == ContextMechanismProtocol().sha256
    with pytest.raises(ValueError, match="cannot be weakened"):
        replace(protocol, minimum_resolved_fills=99)


def test_rule_masks_are_exact_and_fail_closed_on_missing_returns():
    frame = _strong_population().iloc[:2].copy()
    for rule in DEFAULT_CONTEXT_MECHANISM_PROTOCOL.rules:
        assert rule_mask(frame, rule).tolist() == [True, False]
    frame.loc[0, "nifty50_return_5m"] = None
    with pytest.raises(ValueError, match="missing values"):
        rule_mask(frame, DEFAULT_CONTEXT_MECHANISM_PROTOCOL.rules[0])


def test_population_join_rejects_outcomes_in_feature_artifact():
    features, events = _feature_and_event_inputs()
    features["net_r"] = 0.0
    with pytest.raises(ValueError, match="forbidden outcomes"):
        prepare_mechanism_population(
            features,
            events,
            sessions=_sessions(),
            protocol=_synthetic_protocol(),
        )


def test_population_join_preserves_canonical_identity_and_outcomes():
    features, events = _feature_and_event_inputs()
    population = prepare_mechanism_population(
        features,
        events,
        sessions=_sessions(),
        protocol=_synthetic_protocol(),
    )
    assert len(population) == 240
    assert population.resolved.all()
    assert int(population.strict_success.sum()) == 120
    assert population.net_r.mean() == -0.25


def test_strong_alignment_passes_every_preregistered_control():
    trials, folds, shuffles, summary = evaluate_context_mechanism(
        _strong_population(),
        sessions=_sessions(),
        protocol=_synthetic_protocol(),
    )
    assert len(trials) == 4
    assert trials.passed.all()
    assert set(summary["qualified_rules"]) == set(trials.rule_id)
    assert summary["selected_rule"] == "nifty50_positive_5m"
    assert summary["mechanism_passed"] is True
    assert (trials.strict_success_rate == 1.0).all()
    assert (trials.mean_net_r == 0.5).all()
    assert (folds.accuracy_delta > 0).all()
    assert (folds.mean_net_r_delta > 0).all()
    assert len(shuffles) == 4 * 256


def test_empty_selection_fails_closed_without_crashing():
    population = _strong_population()
    for column in [
        name
        for name in population
        if "_return_" in name and not name.endswith("_available")
    ]:
        population[column] = -0.01
    protocol = _synthetic_protocol(
        rules=(DEFAULT_CONTEXT_MECHANISM_PROTOCOL.rules[0],)
    )
    trials, _, _, summary = evaluate_context_mechanism(
        population, sessions=_sessions(), protocol=protocol
    )
    assert trials.loc[0, "resolved_fills"] == 0
    assert not bool(trials.loc[0, "passed"])
    assert trials.loc[0, "shuffle_accuracy_empirical_p"] == 1.0
    assert summary["mechanism_passed"] is False


def test_freeze_writes_protocol_without_opening_outcomes(tmp_path):
    root, output = tmp_path / "root", tmp_path / "output"
    plan = root / "docs/plan-aem-index-context-accuracy.md"
    evidence = root / "docs/evidence/aem-context-integrity.json"
    test_file = root / "tests_lab/test_aem_context_mechanism.py"
    plan.parent.mkdir(parents=True)
    evidence.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    plan.write_text("# frozen mechanism plan\n", encoding="utf-8")
    evidence.write_text(
        json.dumps(
            {
                "run_id": "integrity-run",
                "context_features_sha256": "features-sha",
                "decision": {
                    "price_context_ready_for_mechanism_check": True,
                    "vwap_context_ready": False,
                },
            }
        ),
        encoding="utf-8",
    )
    test_file.write_text("# frozen synthetic tests\n", encoding="utf-8")

    report = freeze_context_mechanism_protocol(root, output)

    assert report["status"] == "protocol_frozen_no_outcome_evaluation"
    assert report["mechanism_evaluated"] is False
    assert report["baseline_improved"] is False
    assert report["decision"]["run_mechanism_once"] is True
    assert report["decision"]["change_live_behavior"] is False
    assert report["protocol_sha256"] == DEFAULT_CONTEXT_MECHANISM_PROTOCOL.sha256
    latest = json.loads(
        (output / "aem_context/mechanism/protocol/latest.json").read_text()
    )
    assert latest["id"] == report["id"]
