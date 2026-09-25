import json
from dataclasses import replace
from pathlib import Path

import pytest
from tradedesk_lab import aem_v2_contract
from tradedesk_lab.aem_v2_contract import (
    DEFAULT_AEM_V2_CONTRACT,
    DEFAULT_AEM_V2_PROTOCOL,
    FORBIDDEN_PREDICTION_FIELDS,
    AemV2Contract,
    AemV2Feature,
    AemV2Geometry,
    freeze_aem_v2_protocol,
    protocol_bundle,
)
from tradedesk_lab.artifacts import digest


def test_frozen_contract_registers_causal_features_entries_and_geometries():
    contract = DEFAULT_AEM_V2_CONTRACT
    assert contract.entry_modes == (
        "anticipatory_impulse",
        "confirmed_pullback",
        "breakout_retest",
    )
    assert len(contract.features) == 34
    assert len(contract.geometries) == 3
    assert all(geometry.gross_reward_risk > 1 for geometry in contract.geometries)
    assert all(feature.available_at == "decision_time" for feature in contract.features)
    assert not FORBIDDEN_PREDICTION_FIELDS.intersection(
        feature.name for feature in contract.features
    )
    assert contract.production_compatible is False
    assert contract.production_minimum_net_reward_risk == 2.0


def test_protocol_freezes_fifty_percent_availability_and_economic_gates():
    protocol = DEFAULT_AEM_V2_PROTOCOL
    assert protocol.minimum_observed_strict_success_rate == 0.50
    assert protocol.minimum_wilson95_lower == 0.40
    assert protocol.minimum_resolved_selected_fills == 100
    assert protocol.minimum_active_sessions == 30
    assert protocol.minimum_active_session_coverage == 0.40
    assert protocol.maximum_unresolved_selected_calls == 0
    assert protocol.initial_pipeline_trial_budget == 24
    assert protocol.minimum_random_advantage_r == 0.10
    assert protocol.ultimate_minimum_observed_rate == 0.80


def test_bundle_fingerprints_are_stable_and_sensitive():
    first = protocol_bundle()
    second = protocol_bundle()
    assert first == second
    assert len(first["contract_sha256"]) == 64
    assert len(first["protocol_sha256"]) == 64
    assert len(first["bundle_sha256"]) == 64

    changed = replace(DEFAULT_AEM_V2_PROTOCOL, initial_pipeline_trial_budget=23)
    assert protocol_bundle(protocol=changed)["bundle_sha256"] != first["bundle_sha256"]


def test_contract_rejects_invalid_or_outcome_leaking_definitions():
    with pytest.raises(ValueError, match="stop < target"):
        AemV2Geometry("bad", target_pct=0.003, stop_pct=0.004, max_hold_minutes=20)
    with pytest.raises(ValueError, match="forbidden"):
        AemV2Feature("strict_success", "outcome", "event")
    with pytest.raises(ValueError, match="production compatibility"):
        AemV2Contract(production_compatible=True)
    with pytest.raises(ValueError, match="sample and active-session"):
        replace(DEFAULT_AEM_V2_PROTOCOL, minimum_resolved_selected_fills=99)


def test_freeze_protocol_writes_immutable_research_only_artifact(tmp_path):
    root, output = tmp_path / "root", tmp_path / "output"
    plan = root / "docs/plan-aem-v2-50pct-baseline.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# frozen plan\n", encoding="utf-8")

    report = freeze_aem_v2_protocol(root, output)
    saved = json.loads(Path(report["artifact_path"]).read_text(encoding="utf-8"))
    pointer = json.loads((output / "aem_v2/protocol/latest.json").read_text())

    assert saved == report
    assert report["status"] == "protocol_frozen_not_evaluated"
    assert report["eligible_for_live"] is False
    assert report["baseline_improved"] is False
    assert report["algorithm_implemented"] is False
    assert len(report["implementation_sha256"]) == 64
    assert report["canonical_baseline"]["strict_success_rate"] == pytest.approx(149 / 693)
    assert pointer["id"] == report["id"]
    assert pointer["bundle_sha256"] == report["bundle_sha256"]


def test_tracked_protocol_evidence_matches_the_frozen_contract_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/aem-v2-protocol.json").read_text(encoding="utf-8"))
    bundle = protocol_bundle()

    assert evidence["contract_sha256"] == bundle["contract_sha256"]
    assert evidence["protocol_sha256"] == bundle["protocol_sha256"]
    assert evidence["bundle_sha256"] == bundle["bundle_sha256"]
    # The roadmap advances after each milestone; the M0 plan fingerprint remains
    # the immutable M0 snapshot rather than being rewritten to match later checkboxes.
    assert len(evidence["plan_sha256"]) == 64
    assert evidence["implementation_sha256"] == digest(Path(aem_v2_contract.__file__))
