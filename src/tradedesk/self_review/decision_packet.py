"""Assembles one ReviewItem + linked Proposal per gauntlet-passed candidate, and enforces the
cooldown that keeps a rejected proposal from being silently re-proposed.

Only a fully gauntlet-passed candidate ever reaches `submit_for_review` - callers in
`config_tuning.py` (and, later, `retire_replace.py`/`detector_authoring.py`) are responsible
for running the harness and discarding anything that didn't clear it; this module never
re-checks that itself, it only turns an already-validated result into a decision packet.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk import review_queue
from tradedesk.broker.indstocks.models import IST
from tradedesk.proposals import (
    PROPOSALS_DIR,
    ConfigPatch,
    NewDetectorCode,
    ProposalKind,
    RetireSetup,
    load_proposal,
    new_proposal,
    write_proposal,
)
from tradedesk.rolling_failure_monitor import SustainedFailure

DEFAULT_COOLDOWN_DAYS = 30


def _payload_key(payload: ConfigPatch | RetireSetup | NewDetectorCode) -> tuple[str, str]:
    return (payload.market, payload.setup) if hasattr(payload, "setup") else (
        payload.market,
        getattr(payload, "setup_kind", ""),
    )


def _title_for(kind: ProposalKind, payload: ConfigPatch | RetireSetup | NewDetectorCode) -> str:
    if isinstance(payload, ConfigPatch):
        param = payload.path.rsplit(".", 1)[-1]
        return (
            f"TUNE {payload.setup} on {payload.market}: {param} "
            f"{payload.old_value}->{payload.new_value}"
        )
    if isinstance(payload, RetireSetup):
        suffix = (
            f", replace with {payload.replacement_source}"
            if payload.replacement_source
            else ""
        )
        return f"RETIRE {payload.setup} on {payload.market} (shadow-tracked){suffix}"
    return f"NEW DETECTOR {payload.setup_kind} on {payload.market}"


def _in_cooldown(
    kind: ProposalKind,
    market: str,
    setup: str,
    *,
    review_path: Path,
    now: datetime,
) -> bool:
    for item in review_queue.load_queue(review_path).values():
        if item.status != "rejected" or not item.proposal_ref:
            continue
        proposal_path = Path(item.proposal_ref)
        if not proposal_path.exists():
            continue
        proposal = load_proposal(proposal_path)
        if proposal.kind != kind or proposal.cooldown_until is None:
            continue
        if _payload_key(proposal.payload) != (market, setup):
            continue
        if datetime.fromisoformat(proposal.cooldown_until) > now:
            return True
    return False


def _open_duplicate(
    kind: ProposalKind, market: str, setup: str, *, review_path: Path
) -> review_queue.ReviewItem | None:
    """An item for the same (kind, market, setup) that is still pending - or, for a
    retirement or a new detector, already approved: a daily run must not stack a second
    copy of a proposal a human hasn't decided on yet, nor re-ask for one already accepted.
    An approved CONFIG_PATCH doesn't block a follow-up tuning pass."""
    blocking = ("pending",) if kind == ProposalKind.CONFIG_PATCH else ("pending", "approved")
    for item in review_queue.load_queue(review_path).values():
        if item.status not in blocking or not item.proposal_ref:
            continue
        proposal_path = Path(item.proposal_ref)
        if not proposal_path.exists():
            continue
        proposal = load_proposal(proposal_path)
        if proposal.kind == kind and _payload_key(proposal.payload) == (market, setup):
            return item
    return None


def _proposal_text(
    kind: ProposalKind,
    payload: ConfigPatch | RetireSetup | NewDetectorCode,
    gauntlet_report: dict[str, Any],
    gauntlet_artifact_path: str,
) -> str:
    if isinstance(payload, RetireSetup):
        # A retirement makes no new performance claim, so it has no gauntlet of its own -
        # printing "gauntlet FAILED ()" for it (as before 2026-09-29) read as if something
        # had been tested and failed.
        return (
            f"Retire {payload.setup} on {payload.market} only - other markets untouched; it "
            f"stays shadow-tracked on {payload.market} (graded, not a call). "
            + (payload.replacement_plan or "No replacement search has run yet.")
        )
    kill = gauntlet_report.get("kill_criteria") or {}
    passed = gauntlet_report.get("stopped_at") is None and kill.get("passed")
    verdict = "PASSED" if passed else "FAILED"
    return f"{payload!r} - gauntlet {verdict} ({gauntlet_artifact_path})"


def refresh_retirement_plan(
    market: str,
    setup: str,
    plan: str,
    *,
    review_path: Path = review_queue.QUEUE,
) -> bool:
    """Updates a still-pending retirement item's text with the latest replacement-search
    status, so the item a human is looking at always says what the search found most
    recently rather than what it found the day the item was first raised."""
    item = _open_duplicate(ProposalKind.RETIRE_SETUP, market, setup, review_path=review_path)
    if item is None or item.status != "pending":
        return False
    proposal_path = Path(item.proposal_ref or "")
    proposal = load_proposal(proposal_path)
    assert isinstance(proposal.payload, RetireSetup)
    payload = replace(proposal.payload, replacement_plan=plan)
    write_proposal(
        replace(proposal, payload=payload), item_id=item.id, directory=proposal_path.parent
    )
    rows = review_queue.load_queue(review_path)
    rows[item.id].proposal = _proposal_text(ProposalKind.RETIRE_SETUP, payload, {}, "")
    review_queue.save_queue(rows, review_path)
    return True


def submit_for_review(
    kind: ProposalKind,
    payload: ConfigPatch | RetireSetup | NewDetectorCode,
    *,
    failure: SustainedFailure | None,
    gauntlet_report: dict[str, Any],
    gauntlet_artifact_path: str,
    gauntlet_artifact_sha256: str,
    review_path: Path = review_queue.QUEUE,
    proposals_dir: Path = PROPOSALS_DIR,
) -> review_queue.ReviewItem | None:
    """Writes the Proposal JSON and one linked ReviewItem, unless an identical
    (kind, market, setup) proposal is still in its post-rejection cooldown - in which case
    this returns None and creates nothing, silently (the caller already knows why: a human
    rejected this exact kind of fix for this exact setup recently)."""

    setup = getattr(payload, "setup", None) or payload.setup_kind
    market = payload.market
    now = datetime.now(IST)
    if _in_cooldown(kind, market, setup, review_path=review_path, now=now):
        return None
    if _open_duplicate(kind, market, setup, review_path=review_path) is not None:
        return None

    evidence_summary = failure.detail if failure is not None else "No sustained-failure record."
    proposal = new_proposal(
        kind,
        payload,
        gauntlet_report=gauntlet_report,
        gauntlet_artifact_path=gauntlet_artifact_path,
        gauntlet_artifact_sha256=gauntlet_artifact_sha256,
        evidence_summary=evidence_summary,
    )
    title = _title_for(kind, payload)
    detail = evidence_summary
    proposal_text = _proposal_text(kind, payload, gauntlet_report, gauntlet_artifact_path)

    item = review_queue.add_item(
        market=market,
        title=title,
        detail=detail,
        proposal=proposal_text,
        path=review_path,
        proposal_ref="",  # filled in below once we know the item id
    )
    proposal_path = write_proposal(proposal, item_id=item.id, directory=proposals_dir)
    rows = review_queue.load_queue(review_path)
    rows[item.id].proposal_ref = str(proposal_path)
    review_queue.save_queue(rows, review_path)
    return rows[item.id]


def decide_with_cooldown(
    item_id: str,
    status: str,
    *,
    review_path: Path = review_queue.QUEUE,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
) -> review_queue.ReviewItem | None:
    """The self-review-aware decide path: identical to `review_queue.decide()` for every
    other caller, but if `status == "rejected"` and the item has a `proposal_ref`, also
    stamps that proposal's `cooldown_until` so `submit_for_review` won't immediately
    re-propose the same (kind, market, setup) fix."""

    item = review_queue.decide(item_id, status, path=review_path)
    if item is None or status != "rejected" or not item.proposal_ref:
        return item
    proposal_path = Path(item.proposal_ref)
    if not proposal_path.exists():
        return item
    proposal = load_proposal(proposal_path)
    cooldown_until = (datetime.now(IST) + timedelta(days=cooldown_days)).isoformat()
    updated = replace(proposal, cooldown_until=cooldown_until)
    write_proposal(updated, item_id=item.id, directory=proposal_path.parent)
    return item
