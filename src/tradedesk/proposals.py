"""Machine-actionable proposals for the self-review loop (2026-09-27).

`review_queue.py` is deliberately just a bookkeeping log: a `ReviewItem.proposal` is free
text a human reads, and `decide()` only flips a status field. That is exactly right for
every existing producer (`flag_setup_failures`, `flag_research_findings`,
`check_and_flag_drift`, `weekly_review`) - none of them should ever auto-apply.

The self-review loop is different: an approved proposal must actually change
`config/setups.yaml` (or, later, add new detector code), so a human's "approved" click has
to reference something a program can act on. This module is that structured layer, kept
fully separate from `review_queue.py` so its own contract never has to change:

- A `Proposal` is written to its own JSON file under `data/reviews/proposals/<item_id>.json`
  and only *referenced* by a `ReviewItem.proposal_ref` - the queue file itself stays small
  and human-eyeballable, and the (potentially large) `GauntletReport` payload lives out of
  line, the same way `tradedesk_lab.artifacts.write_json` keeps large evidence out of a
  checkpoint's own small JSON.
- `self_review/apply.py` is the ONLY code that ever reads a `Proposal` back and turns it into
  a config/code change, and only when the linked `ReviewItem.status == "approved"` and the
  proposal file's hash still matches what was reviewed (see `apply.py`'s docstring).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST

PROPOSALS_DIR = Path("data/reviews/proposals")


class ProposalKind(StrEnum):
    CONFIG_PATCH = "config_patch"
    RETIRE_SETUP = "retire_setup"
    NEW_DETECTOR = "new_detector"


@dataclass(frozen=True)
class ConfigPatch:
    """A single dotted-path change into config/setups.yaml, e.g. path=
    "setups.base_breakout.rs_percentile_min", proposing new_value in place of old_value."""

    setup: str
    market: str
    path: str
    old_value: Any
    new_value: Any


@dataclass(frozen=True)
class RetireSetup:
    """Retire `setup` on `market` only (config/setups.yaml `retired_markets`; every other
    market is untouched and the setup keeps being shadow-tracked on this one). A
    replacement, if any, is always a SEPARATE Proposal (ConfigPatch or NewDetectorCode) -
    never bundled into the same ReviewItem, so each can be approved or rejected
    independently. `replacement_plan` is the replacement search's plain-English status at
    submission (what it tested, the closest candidate, what runs next);
    `replacement_search_report` is that run's full JSON report."""

    setup: str
    market: str
    replacement_setup_kind: str | None = None
    replacement_source: str | None = None  # e.g. "lab_candidate:<experiment_id>:<candidate_id>"
    replacement_plan: str | None = None
    replacement_search_report: str | None = None


@dataclass(frozen=True)
class NewDetectorCode:
    """A new setup authored and gauntlet-validated in tradedesk_lab/candidates/, not yet
    wired into production. `target_files` lists the exact production paths apply() will
    write on approval - nothing outside this list is ever touched."""

    setup_kind: str
    market: str
    lab_module_path: str
    lab_experiment_id: str
    lab_candidate_id: str
    target_files: list[str] = field(default_factory=list)


PAYLOAD_TYPES: dict[ProposalKind, type] = {
    ProposalKind.CONFIG_PATCH: ConfigPatch,
    ProposalKind.RETIRE_SETUP: RetireSetup,
    ProposalKind.NEW_DETECTOR: NewDetectorCode,
}


@dataclass(frozen=True)
class Proposal:
    kind: ProposalKind
    payload: ConfigPatch | RetireSetup | NewDetectorCode
    gauntlet_report: dict[str, Any]
    gauntlet_artifact_path: str
    gauntlet_artifact_sha256: str
    evidence_summary: str
    created_at: str
    cooldown_until: str | None = None


def write_proposal(proposal: Proposal, *, item_id: str, directory: Path = PROPOSALS_DIR) -> Path:
    """Writes `<directory>/<item_id>.json` and returns its path. `item_id` mirrors the
    ReviewItem id it will be attached to via `proposal_ref`, so the two stay linkable without
    a database - same convention as `data/m14_m18/*/runs/<id>/report.json`."""

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{safe_filename(item_id)}.json"
    payload = asdict(proposal)
    payload["kind"] = proposal.kind.value
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return target


def load_proposal(path: Path) -> Proposal:
    raw = json.loads(path.read_text(encoding="utf-8"))
    kind = ProposalKind(raw["kind"])
    payload_type = PAYLOAD_TYPES[kind]
    payload = payload_type(**raw["payload"])
    return Proposal(
        kind=kind,
        payload=payload,
        gauntlet_report=raw["gauntlet_report"],
        gauntlet_artifact_path=raw["gauntlet_artifact_path"],
        gauntlet_artifact_sha256=raw["gauntlet_artifact_sha256"],
        evidence_summary=raw["evidence_summary"],
        created_at=raw["created_at"],
        cooldown_until=raw.get("cooldown_until"),
    )


def safe_filename(item_id: str) -> str:
    """A ReviewItem id turned into a safe filename stem - shared by every module that
    names a file after an item id (this one, `self_review/apply.py`'s applied-evidence
    file, and the dashboard's applied-status lookup), so all three always agree."""
    return item_id.replace(":", "_").replace("/", "_").replace("\\", "_")


def new_proposal(
    kind: ProposalKind,
    payload: ConfigPatch | RetireSetup | NewDetectorCode,
    *,
    gauntlet_report: dict[str, Any],
    gauntlet_artifact_path: str,
    gauntlet_artifact_sha256: str,
    evidence_summary: str,
) -> Proposal:
    return Proposal(
        kind=kind,
        payload=payload,
        gauntlet_report=gauntlet_report,
        gauntlet_artifact_path=gauntlet_artifact_path,
        gauntlet_artifact_sha256=gauntlet_artifact_sha256,
        evidence_summary=evidence_summary,
        created_at=datetime.now(IST).isoformat(),
    )
