"""self_review/decision_packet.py: one ReviewItem+Proposal per gauntlet-passed candidate,
and the cooldown that stops a rejected proposal from being immediately re-proposed."""

from __future__ import annotations

from pathlib import Path

from tradedesk import review_queue as rq
from tradedesk.proposals import ConfigPatch, ProposalKind, load_proposal
from tradedesk.self_review import decision_packet as dp

PASSING_REPORT = {"strategy_name": "x", "stopped_at": None, "kill_criteria": {"passed": True}}


def _payload() -> ConfigPatch:
    return ConfigPatch(
        setup="base_breakout", market="nse", path="setups.base_breakout.partial_at_r",
        old_value=2.0, new_value=2.5,
    )


def test_submit_for_review_writes_a_linked_item_and_proposal(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"

    item = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        _payload(),
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="data/x/report.json",
        gauntlet_artifact_sha256="abc",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )

    assert item is not None
    assert item.proposal_ref is not None
    assert "TUNE base_breakout on nse" in item.title
    reloaded = rq.load_queue(review_path)[item.id]
    assert reloaded.proposal_ref == item.proposal_ref
    proposal = load_proposal(Path(item.proposal_ref))
    assert proposal.payload.new_value == 2.5


def test_rejected_proposal_enters_cooldown_and_blocks_resubmission(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"

    item = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        _payload(),
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="x",
        gauntlet_artifact_sha256="y",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )
    assert item is not None

    dp.decide_with_cooldown(item.id, "rejected", review_path=review_path, cooldown_days=30)

    again = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        _payload(),
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="x2",
        gauntlet_artifact_sha256="y2",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )
    assert again is None

    queue = rq.load_queue(review_path)
    assert len(queue) == 1  # nothing new was written


def test_a_different_setup_is_not_blocked_by_another_setups_cooldown(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"

    item = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        _payload(),
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="x",
        gauntlet_artifact_sha256="y",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )
    assert item is not None
    dp.decide_with_cooldown(item.id, "rejected", review_path=review_path)

    other = ConfigPatch(
        setup="nr7_breakout", market="nse", path="setups.nr7_breakout.partial_at_r",
        old_value=2.0, new_value=2.5,
    )
    result = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        other,
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="x",
        gauntlet_artifact_sha256="y",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )
    assert result is not None


def test_approved_proposal_is_not_in_cooldown(tmp_path: Path) -> None:
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"

    item = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        _payload(),
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="x",
        gauntlet_artifact_sha256="y",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )
    assert item is not None
    dp.decide_with_cooldown(item.id, "approved", review_path=review_path)

    # Approving (not rejecting) never sets a cooldown - a second identical proposal for the
    # same setup should still be allowed through (e.g. a follow-up tuning pass).
    again = dp.submit_for_review(
        ProposalKind.CONFIG_PATCH,
        _payload(),
        failure=None,
        gauntlet_report=PASSING_REPORT,
        gauntlet_artifact_path="x2",
        gauntlet_artifact_sha256="y2",
        review_path=review_path,
        proposals_dir=proposals_dir,
    )
    assert again is not None
