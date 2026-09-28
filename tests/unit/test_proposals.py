"""proposals.py: the machine-actionable layer review_queue.py deliberately doesn't have."""

from __future__ import annotations

from pathlib import Path

from tradedesk import proposals as p


def test_config_patch_round_trips_through_json(tmp_path: Path) -> None:
    payload = p.ConfigPatch(
        setup="base_breakout",
        market="nse",
        path="setups.base_breakout.rs_percentile_min",
        old_value=70,
        new_value=80,
    )
    proposal = p.new_proposal(
        p.ProposalKind.CONFIG_PATCH,
        payload,
        gauntlet_report={"strategy_name": "x", "stopped_at": None, "stages": {}},
        gauntlet_artifact_path="data/m14_m18/harness/x/report.json",
        gauntlet_artifact_sha256="abc123",
        evidence_summary="3 consecutive windows below floor",
    )
    written = p.write_proposal(
        proposal, item_id="nse:2026-01-01T00:00:00+05:30", directory=tmp_path
    )
    assert written.exists()

    reloaded = p.load_proposal(written)
    assert reloaded.kind == p.ProposalKind.CONFIG_PATCH
    assert isinstance(reloaded.payload, p.ConfigPatch)
    assert reloaded.payload.new_value == 80
    assert reloaded.gauntlet_artifact_sha256 == "abc123"
    assert reloaded.cooldown_until is None


def test_retire_setup_round_trips(tmp_path: Path) -> None:
    payload = p.RetireSetup(setup="nr7_breakout", market="crypto")
    proposal = p.new_proposal(
        p.ProposalKind.RETIRE_SETUP,
        payload,
        gauntlet_report={},
        gauntlet_artifact_path="x",
        gauntlet_artifact_sha256="y",
        evidence_summary="z",
    )
    written = p.write_proposal(
        proposal, item_id="crypto:weird/id:with\\slashes", directory=tmp_path
    )
    reloaded = p.load_proposal(written)
    assert isinstance(reloaded.payload, p.RetireSetup)
    assert reloaded.payload.replacement_setup_kind is None


def test_new_detector_code_round_trips_with_target_files(tmp_path: Path) -> None:
    payload = p.NewDetectorCode(
        setup_kind="mean_reversion_v1",
        market="nse",
        lab_module_path="tradedesk_lab/candidates/mean_reversion_v1.py",
        lab_experiment_id="exp1",
        lab_candidate_id="cand1",
        target_files=["src/tradedesk/setups/mean_reversion_v1.py"],
    )
    proposal = p.new_proposal(
        p.ProposalKind.NEW_DETECTOR,
        payload,
        gauntlet_report={},
        gauntlet_artifact_path="x",
        gauntlet_artifact_sha256="y",
        evidence_summary="z",
    )
    written = p.write_proposal(proposal, item_id="nse:2026-01-01", directory=tmp_path)
    reloaded = p.load_proposal(written)
    assert reloaded.payload.target_files == ["src/tradedesk/setups/mean_reversion_v1.py"]
