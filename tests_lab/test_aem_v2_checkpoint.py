import json
from pathlib import Path

from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import DEFAULT_EVENT_ENGINE_CONTRACT
from tradedesk_lab.artifacts import digest


def test_milestone_one_evidence_matches_engine_and_test_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/aem-v2-engine.json").read_text(encoding="utf-8"))

    assert evidence["status"] == "causal_engine_implemented_not_evaluated"
    assert evidence["milestone"] == 1
    assert evidence["opportunity_engine_implemented"] is True
    assert evidence["outcome_engine_implemented"] is True
    assert evidence["selector_implemented"] is False
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["event_contract_sha256"] == DEFAULT_EVENT_ENGINE_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_events.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_events.py")
    assert evidence["decision"]["change_live_behavior"] is False


def test_milestone_two_feature_evidence_matches_source_and_is_complete():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/aem-v2-features.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "feature_registry_implemented_not_evaluated"
    assert evidence["milestone"] == 2
    assert evidence["feature_registry_implemented"] is True
    assert evidence["integrity_audit_implemented"] is True
    assert evidence["development_dataset_frozen"] is True
    assert evidence["selector_implemented"] is False
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["feature_count"] == len(DEFAULT_AEM_V2_CONTRACT.features)
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_features.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_features.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False


def test_milestone_three_precision_ladder_evidence_matches_source_and_is_partial():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/aem-v2-precision-ladder.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "selector_mechanics_implemented_not_evaluated"
    assert evidence["milestone"] == 3
    assert evidence["hard_veto_layer_implemented"] is True
    assert evidence["evidence_contributions_implemented"] is True
    assert evidence["oof_calibration_implemented"] is True
    assert evidence["absolute_threshold_and_ranking_implemented"] is True
    assert evidence["symbol_deduplication_implemented"] is True
    assert evidence["logistic_control_implemented"] is True
    assert evidence["tree_challenger_implemented"] is False
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_precision_ladder.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_precision_ladder.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False


def test_milestone_two_universe_audit_evidence_matches_source_and_is_real():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/aem-v2-universe-audit.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "universe_audit_complete"
    assert evidence["milestone"] == 2
    assert evidence["universe_symbols"] == 50
    assert evidence["evaluation_sessions"] == 120
    assert evidence["total_code_sessions"] == 6000
    assert evidence["included_code_sessions"] == 5999
    assert evidence["excluded_code_sessions"] == 1
    assert evidence["exclusion_reason_counts"] == {"incomplete_m1_session": 1}
    assert evidence["excluded_events"] == [
        {"scrip_code": "NSE_3063", "session": "2026-04-30", "reasons": ["incomplete_m1_session"]}
    ]
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_universe_audit.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_universe_audit.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False


def test_milestone_four_development_dataset_evidence_matches_source_and_is_real():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/aem-v2-development-dataset.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "development_dataset_frozen_not_evaluated"
    assert evidence["milestone"] == 4
    assert evidence["assembly_implemented"] is True
    assert evidence["real_run_completed"] is True
    assert evidence["real_results"]["included_code_sessions"] == 5999
    assert evidence["real_results"]["opportunities_found"] == 66668
    assert evidence["real_results"]["resolved_rows"] == 177351
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_development_experiment.py"
    )
    assert evidence["tests_sha256"] == digest(
        root / "tests_lab/test_aem_v2_development_experiment.py"
    )
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False


def test_milestone_four_pipeline_race_evidence_matches_source_and_is_real():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/aem-v2-pipeline-race.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "pipeline_race_evaluated"
    assert evidence["milestone"] == 4
    assert evidence["pipeline_race_implemented"] is True
    assert evidence["real_run_completed"] is True
    assert evidence["real_result"]["trial_budget"] == 24
    assert evidence["real_result"]["trials_evaluated"] == 24
    assert evidence["real_result"]["qualified_candidates"] == []
    assert evidence["algorithm_evaluated"] is True
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_pipeline_race.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_pipeline_race.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False


def test_milestone_four_stress_gates_evidence_matches_source_and_has_no_nominee():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/aem-v2-stress-gates.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "code_implemented_and_tested_no_nominated_candidate"
    assert evidence["milestone"] == 4
    assert evidence["stress_gates_implemented"] is True
    assert evidence["real_run_completed"] is False
    assert evidence["not_yet_completed"][0].startswith("a real stress replay")
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["mandatory_stress_cases"] == [
        "base_costs",
        "costs_1p25x",
        "costs_1p50x",
        "slippage_2x",
        "one_bar_delay",
        "adverse_missed_fills",
    ]
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_stress_gates.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_stress_gates.py")
    assert evidence["decision"]["change_live_behavior"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False
