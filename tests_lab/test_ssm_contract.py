import json
from dataclasses import replace
from pathlib import Path

import pytest
from tradedesk_lab import ssm_contract
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.artifacts import digest
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT
from tradedesk_lab.ssm_contract import (
    DEFAULT_SSM_CONTRACT,
    DEFAULT_SSM_PROTOCOL,
    FORBIDDEN_PREDICTION_FIELDS,
    SsmContract,
    SsmFeature,
    SsmGeometry,
    freeze_ssm_protocol,
    protocol_bundle,
)


def test_ssm_contract_is_evidence_backed_structurally_distinct_and_causal():
    contract = DEFAULT_SSM_CONTRACT
    assert contract.signal_family == "interday_same_half_hour_cross_sectional_momentum"
    assert contract.predictor_modes == ("lag1_cross_sectional", "multi_day_persistence")
    assert not set(contract.predictor_modes).intersection(DEFAULT_AEM_V2_CONTRACT.entry_modes)
    assert contract.signal_family != DEFAULT_OSR_CONTRACT.signal_family
    assert contract.slot_minutes == 30
    assert len(contract.slot_starts) == 11
    assert len(contract.features) == 24
    assert len(contract.geometries) == 3
    assert all(2 / 3 <= item.gross_reward_risk <= 1 for item in contract.geometries)
    assert all(item.available_at == "before_slot_entry" for item in contract.features)
    assert not FORBIDDEN_PREDICTION_FIELDS.intersection(
        feature.name for feature in contract.features
    )
    assert contract.production_compatible is False
    assert contract.production_minimum_net_reward_risk == 2.0


def test_ssm_protocol_preserves_accuracy_availability_economics_and_concentration():
    protocol = DEFAULT_SSM_PROTOCOL
    assert protocol.initial_pipeline_trial_budget == 12
    assert protocol.minimum_observed_strict_success_rate == 0.50
    assert protocol.minimum_wilson95_lower == 0.40
    assert protocol.minimum_resolved_selected_fills == 100
    assert protocol.minimum_active_sessions == 30
    assert protocol.minimum_active_session_coverage == 0.40
    assert protocol.maximum_unresolved_selected_calls == 0
    assert protocol.minimum_random_advantage_r == 0.10
    assert protocol.maximum_single_symbol_win_share == 0.25
    assert protocol.maximum_single_slot_win_share == 0.35
    assert protocol.existing_120_sessions_evidence_class == "consumed_historical_development"
    assert protocol.ultimate_minimum_observed_rate == 0.80


def test_ssm_bundle_fingerprints_are_stable_sensitive_and_exactly_bounded():
    first = protocol_bundle()
    assert first == protocol_bundle()
    assert len(first["contract_sha256"]) == 64
    assert len(first["protocol_sha256"]) == 64
    assert len(first["bundle_sha256"]) == 64
    assert (
        first["predictor_mode_count"]
        * first["geometry_count"]
        * first["selector_kind_count"]
        == first["registered_pipeline_budget"]
        == 12
    )

    changed = replace(DEFAULT_SSM_CONTRACT, transparent_rank_threshold=0.91)
    assert protocol_bundle(contract=changed)["bundle_sha256"] != first["bundle_sha256"]


def test_ssm_contract_rejects_leakage_invalid_geometry_and_weakened_gates():
    with pytest.raises(ValueError, match="target <= stop"):
        SsmGeometry("bad", target_pct=0.004, stop_pct=0.003)
    with pytest.raises(ValueError, match="two thirds"):
        SsmGeometry("bad", target_pct=0.001, stop_pct=0.003)
    with pytest.raises(ValueError, match="forbidden"):
        SsmFeature("strict_success", "outcome", "event")
    with pytest.raises(ValueError, match="production compatibility"):
        SsmContract(production_compatible=True)
    with pytest.raises(ValueError, match="sample and active-session"):
        replace(DEFAULT_SSM_PROTOCOL, minimum_resolved_selected_fills=99)
    with pytest.raises(ValueError, match="single-slot"):
        replace(DEFAULT_SSM_PROTOCOL, maximum_single_slot_win_share=0.50)


def test_freeze_ssm_protocol_writes_research_only_artifact(tmp_path):
    root, output = tmp_path / "root", tmp_path / "output"
    plan = root / "docs/plan-ssm-50pct-baseline.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# frozen SSM plan\n", encoding="utf-8")

    report = freeze_ssm_protocol(root, output)
    saved = json.loads(Path(report["artifact_path"]).read_text(encoding="utf-8"))
    pointer = json.loads((output / "ssm/protocol/latest.json").read_text())

    assert saved == report
    assert report["status"] == "protocol_frozen_not_evaluated"
    assert report["eligible_for_live"] is False
    assert report["baseline_improved"] is False
    assert report["algorithm_implemented"] is False
    assert report["canonical_baseline"]["strict_success_rate"] == pytest.approx(149 / 693)
    assert report["prior_challenger"]["qualified_candidates"] == 0
    assert pointer["id"] == report["id"]
    assert pointer["bundle_sha256"] == report["bundle_sha256"]


def test_tracked_ssm_evidence_matches_frozen_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/ssm-protocol.json").read_text("utf-8"))
    bundle = protocol_bundle()

    assert evidence["status"] == "protocol_frozen_not_evaluated"
    assert evidence["milestone"] == 0
    assert evidence["contract_sha256"] == bundle["contract_sha256"]
    assert evidence["protocol_sha256"] == bundle["protocol_sha256"]
    assert evidence["bundle_sha256"] == bundle["bundle_sha256"]
    assert evidence["implementation_sha256"] == digest(Path(ssm_contract.__file__))
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_ssm_contract.py")
    assert evidence["plan_sha256"] == digest(root / "docs/plan-ssm-50pct-baseline.md")
    assert evidence["algorithm_implemented"] is False
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["decision"]["change_live_behavior"] is False
