"""self_review/apply.py: the only code that turns an approved proposal into a real
config/code change. Every test here runs against a throwaway git repo under tmp_path -
never the real one - via `apply()`'s injectable `root` parameter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tradedesk import review_queue as rq
from tradedesk.proposals import (
    ConfigPatch,
    NewDetectorCode,
    ProposalKind,
    RetireSetup,
    new_proposal,
    write_proposal,
)
from tradedesk.self_review import apply as ap

SIGNALS_PY_TEXT = '''"""Signal models."""

from enum import StrEnum


class SetupKind(StrEnum):
    BASE_BREAKOUT = "base_breakout"
    TREND_PULLBACK = "trend_pullback"
    NR7_BREAKOUT = "nr7_breakout"
'''

SETUPS_INIT_TEXT = '''"""Setups. One module per setup implementing the Setup protocol."""

from tradedesk.engine.signals import SetupKind
from tradedesk.setups.base import Setup, SetupContext
from tradedesk.setups.base_breakout import BaseBreakout
from tradedesk.setups.nr7_breakout import Nr7Breakout
from tradedesk.setups.trend_pullback import TrendPullback

REGISTRY: dict[SetupKind, Setup] = {
    SetupKind.BASE_BREAKOUT: BaseBreakout(),
    SetupKind.TREND_PULLBACK: TrendPullback(),
    SetupKind.NR7_BREAKOUT: Nr7Breakout(),
}

__all__ = ["REGISTRY", "Setup", "SetupContext", "SetupKind"]
'''

SETUPS_YAML_TEXT = """setups:
  base_breakout:
    enabled: true
    partial_at_r: 2.0
    partial_fraction: 1.0
  nr7_breakout:
    enabled: true
    partial_at_r: 2.0
entry:
  confirm_timeframe_minutes: 15
"""


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "setups.yaml").write_text(SETUPS_YAML_TEXT, encoding="utf-8")
    (root / "src" / "tradedesk" / "engine").mkdir(parents=True)
    (root / "src" / "tradedesk" / "engine" / "signals.py").write_text(
        SIGNALS_PY_TEXT, encoding="utf-8"
    )
    (root / "src" / "tradedesk" / "setups").mkdir(parents=True)
    (root / "src" / "tradedesk" / "setups" / "__init__.py").write_text(
        SETUPS_INIT_TEXT, encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True
    )
    return root


def _submit_and_approve(tmp_path: Path, root: Path) -> tuple[str, Path]:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"
    payload = ConfigPatch(
        setup="base_breakout", market="nse", path="setups.base_breakout.partial_at_r",
        old_value=2.0, new_value=2.5,
    )
    proposal = new_proposal(
        ProposalKind.CONFIG_PATCH, payload,
        gauntlet_report={"stopped_at": None, "kill_criteria": {"passed": True}},
        gauntlet_artifact_path="x", gauntlet_artifact_sha256="y",
        evidence_summary="3 consecutive windows below floor",
    )
    item = rq.add_item("nse", "TUNE base_breakout", "detail", "proposal", path=review_path)
    proposal_path = write_proposal(proposal, item_id=item.id, directory=proposals_dir)
    rows = rq.load_queue(review_path)
    rows[item.id].proposal_ref = str(proposal_path)
    rq.save_queue(rows, review_path)
    rq.decide(item.id, "approved", path=review_path)
    return item.id, review_path


def test_apply_refuses_a_pending_item(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    review_path = tmp_path / "queue.jsonl"
    item = rq.add_item("nse", "t", "d", "p", path=review_path)
    with pytest.raises(ap.ApplyRefused, match="not approved"):
        ap.apply(item.id, root=root, review_path=review_path)


def test_apply_config_patch_writes_yaml_commits_and_records_evidence(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    item_id, review_path = _submit_and_approve(tmp_path, root)

    result = ap.apply(
        item_id,
        root=root,
        review_path=review_path,
        applied_dir=tmp_path / "applied",
        applied_log=tmp_path / "log.md",
    )

    text = (root / "config" / "setups.yaml").read_text(encoding="utf-8")
    assert "partial_at_r: 2.5" in text
    assert "enabled: true" in text  # untouched lines survive verbatim
    assert "partial_fraction: 1.0" in text

    assert result.git_commit_sha is not None
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    assert "[self-review] tune base_breakout" in log

    applied_files = list((tmp_path / "applied").glob("*.json"))
    assert len(applied_files) == 1
    assert (tmp_path / "log.md").read_text(encoding="utf-8")


def test_apply_refuses_to_write_outside_the_allowed_set(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    with pytest.raises(ap.ApplyRefused, match="refusing to write"):
        ap._guard_path(root / "src" / "tradedesk" / "broker" / "indstocks" / "auth.py", root)


def _submit_and_approve_retirement(
    tmp_path: Path, root: Path, *, replacement_source: str | None = None
) -> tuple[str, Path]:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"
    payload = RetireSetup(
        setup="nr7_breakout", market="nse", replacement_source=replacement_source
    )
    proposal = new_proposal(
        ProposalKind.RETIRE_SETUP, payload,
        gauntlet_report={}, gauntlet_artifact_path="", gauntlet_artifact_sha256="",
        evidence_summary="3 consecutive windows below floor",
    )
    item = rq.add_item("nse", "RETIRE nr7_breakout", "detail", "proposal", path=review_path)
    proposal_path = write_proposal(proposal, item_id=item.id, directory=proposals_dir)
    rows = rq.load_queue(review_path)
    rows[item.id].proposal_ref = str(proposal_path)
    rq.save_queue(rows, review_path)
    rq.decide(item.id, "approved", path=review_path)
    return item.id, review_path


def test_apply_retire_setup_retires_on_that_market_only(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    item_id, review_path = _submit_and_approve_retirement(tmp_path, root)

    result = ap.apply(
        item_id, root=root, review_path=review_path,
        applied_dir=tmp_path / "applied", applied_log=tmp_path / "log.md",
    )

    text = (root / "config" / "setups.yaml").read_text(encoding="utf-8")
    assert "RETIRED on nse" in text
    assert "item " + item_id in text
    lines = text.splitlines()
    nr7_index = next(i for i, line in enumerate(lines) if line.strip() == "nr7_breakout:")
    assert lines[nr7_index + 1].strip() == "retired_markets: [nse]"
    # `enabled` is NOT flipped - that would switch it off on every other market too.
    assert "enabled: true" in lines[nr7_index + 2]
    assert "enabled: false" not in text
    # base_breakout, listed first in the fixture, is completely untouched.
    base_index = next(i for i, line in enumerate(lines) if line.strip() == "base_breakout:")
    assert "enabled: true" in lines[base_index + 1]
    assert result.git_commit_sha is not None
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    assert "[self-review] retire nr7_breakout" in log


def test_retiring_on_a_second_market_extends_the_list_and_loads_per_market(
    tmp_path: Path,
) -> None:
    from tradedesk.config.models import SetupsConfig

    setups_yaml = tmp_path / "setups.yaml"
    setups_yaml.write_text(SETUPS_YAML_TEXT, encoding="utf-8")
    ap._add_to_flow_list(setups_yaml, "nr7_breakout", "retired_markets", "crypto")
    ap._add_to_flow_list(setups_yaml, "nr7_breakout", "retired_markets", "bse")
    ap._add_to_flow_list(setups_yaml, "nr7_breakout", "retired_markets", "crypto")  # idempotent

    import yaml

    text = setups_yaml.read_text(encoding="utf-8")
    assert "    retired_markets: [crypto, bse]" in text
    cfg = SetupsConfig.model_validate(yaml.safe_load(text))
    nr7 = cfg.setups["nr7_breakout"]
    assert not nr7.active_on("crypto") and not nr7.active_on("bse")
    assert nr7.active_on("nse")  # the market nobody retired it on keeps it
    assert nr7.runs_on("crypto")  # still scanned there, in shadow
    assert cfg.setups["base_breakout"].active_on("crypto")


def test_apply_retire_setup_never_auto_promotes_a_named_replacement(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    item_id, review_path = _submit_and_approve_retirement(
        tmp_path, root, replacement_source="lab_candidate:exp1:cand1"
    )

    ap.apply(
        item_id, root=root, review_path=review_path,
        applied_dir=tmp_path / "applied", applied_log=tmp_path / "log.md",
    )

    text = (root / "config" / "setups.yaml").read_text(encoding="utf-8")
    # Retirement happens; nothing about "exp1"/"cand1" is ever written into production config -
    # the replacement pointer is informational only and requires its own separate approval.
    assert "exp1" not in text
    assert "cand1" not in text


GENERATED_SOURCE = '''"""A brand-new candidate."""

from tradedesk.engine.signals import SetupKind


class MeanReversionV1:
    kind = SetupKind.MEAN_REVERSION_V1

    def arm(self, df, ctx, params):
        return None
'''


def test_apply_new_detector_writes_all_five_wiring_points(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    lab_output = tmp_path / "lab_output"
    (lab_output / "candidates" / "exp1").mkdir(parents=True)
    (lab_output / "candidates" / "exp1" / "production_setup.py").write_text(
        GENERATED_SOURCE, encoding="utf-8"
    )

    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"
    payload = NewDetectorCode(
        setup_kind="mean_reversion_v1",
        market="nse",
        lab_module_path="tradedesk_lab/candidates/mean_reversion_v1.py",
        lab_experiment_id="exp1",
        lab_candidate_id="cand1",
        target_files=[
            "src/tradedesk/engine/patterns.py",
            "src/tradedesk/setups/mean_reversion_v1.py",
            "src/tradedesk/engine/signals.py",
            "src/tradedesk/setups/__init__.py",
            "config/setups.yaml",
        ],
    )
    proposal = new_proposal(
        ProposalKind.NEW_DETECTOR, payload,
        gauntlet_report={"stopped_at": None, "kill_criteria": {"passed": True}},
        gauntlet_artifact_path="x", gauntlet_artifact_sha256="y",
        evidence_summary="cleared the gauntlet",
    )
    item = rq.add_item(
        "nse", "NEW DETECTOR mean_reversion_v1", "detail", "proposal", path=review_path
    )
    proposal_path = write_proposal(proposal, item_id=item.id, directory=proposals_dir)
    rows = rq.load_queue(review_path)
    rows[item.id].proposal_ref = str(proposal_path)
    rq.save_queue(rows, review_path)
    rq.decide(item.id, "approved", path=review_path)

    result = ap.apply(
        item.id, root=root, lab_output=lab_output, review_path=review_path,
        applied_dir=tmp_path / "applied", applied_log=tmp_path / "log.md",
    )

    signals_text = (root / "src" / "tradedesk" / "engine" / "signals.py").read_text(
        encoding="utf-8"
    )
    assert 'MEAN_REVERSION_V1 = "mean_reversion_v1"' in signals_text
    assert "NR7_BREAKOUT" in signals_text  # existing members untouched

    new_setup_text = (
        root / "src" / "tradedesk" / "setups" / "mean_reversion_v1.py"
    ).read_text(encoding="utf-8")
    assert new_setup_text == GENERATED_SOURCE

    init_text = (root / "src" / "tradedesk" / "setups" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "from tradedesk.setups.mean_reversion_v1 import MeanReversionV1" in init_text
    assert "SetupKind.MEAN_REVERSION_V1: MeanReversionV1()," in init_text
    assert "SetupKind.NR7_BREAKOUT: Nr7Breakout()," in init_text  # untouched

    yaml_text = (root / "config" / "setups.yaml").read_text(encoding="utf-8")
    # Scoped to the market it was validated on - never switched on everywhere.
    assert "mean_reversion_v1:\n    enabled: true\n    markets: [nse]" in yaml_text

    assert result.git_commit_sha is not None
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    assert "[self-review] add new detector mean_reversion_v1" in log


def test_apply_new_detector_refuses_without_a_pre_generated_source(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"
    payload = NewDetectorCode(
        setup_kind="ghost_setup",
        market="nse",
        lab_module_path="tradedesk_lab/candidates/ghost_setup.py",
        lab_experiment_id="exp_missing",
        lab_candidate_id="cand_missing",
    )
    proposal = new_proposal(
        ProposalKind.NEW_DETECTOR, payload,
        gauntlet_report={}, gauntlet_artifact_path="", gauntlet_artifact_sha256="",
        evidence_summary="",
    )
    item = rq.add_item("nse", "NEW DETECTOR ghost_setup", "detail", "proposal", path=review_path)
    proposal_path = write_proposal(proposal, item_id=item.id, directory=proposals_dir)
    rows = rq.load_queue(review_path)
    rows[item.id].proposal_ref = str(proposal_path)
    rq.save_queue(rows, review_path)
    rq.decide(item.id, "approved", path=review_path)

    with pytest.raises(ap.ApplyRefused, match="no pre-generated production source"):
        ap.apply(
            item.id, root=root, lab_output=tmp_path / "empty_lab_output", review_path=review_path,
        )
