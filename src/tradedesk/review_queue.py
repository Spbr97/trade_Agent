"""The approve/reject surface for "self-learning" the user asked for (2026-09-12): flag a
failed-call pattern, explain it, and let a human decide - never apply anything on its own.

This is deliberately a bookkeeping log, not a mechanism that can change config or code:
CLAUDE.md's hard rule is that Claude's output is advisory text only and can never create a
signal or change a price, level or quantity, and a new setup/threshold change ships only
after a human reviews it. Approving an item here just marks it decided so it stops
surfacing as pending and the decision is on record - actually acting on it (editing a
config value, tuning a setup) stays a manual step for the user, same as it already is for
`tradedesk review week`'s output today.

Same JSONL-log pattern as scripts/crypto_signal_tracker.py (small enough not to need a
real table). NSE-only for now, matching `review week`'s current scope - crypto/BSE have no
paper book or journal-based failure analysis to draw from yet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from tradedesk.broker.indstocks.models import IST

QUEUE = Path("data/reviews/queue.jsonl")


@dataclass
class ReviewItem:
    id: str
    created_at: str
    market: str
    title: str
    detail: str
    proposal: str
    status: str = "pending"  # "pending" | "approved" | "rejected"
    decided_at: str | None = None


def load_queue(path: Path = QUEUE) -> dict[str, ReviewItem]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]  # noqa: E501
    return {r["id"]: ReviewItem(**r) for r in rows}


def save_queue(rows: dict[str, ReviewItem], path: Path = QUEUE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows.values():
            fh.write(json.dumps(asdict(r)) + "\n")


def add_item(
    market: str, title: str, detail: str, proposal: str, *, path: Path = QUEUE
) -> ReviewItem:
    rows = load_queue(path)
    item = ReviewItem(
        id=f"{market}:{datetime.now(IST).isoformat()}",
        created_at=datetime.now(IST).isoformat(),
        market=market,
        title=title,
        detail=detail,
        proposal=proposal,
    )
    rows[item.id] = item
    save_queue(rows, path)
    return item


def decide(item_id: str, status: str, *, path: Path = QUEUE) -> ReviewItem | None:
    if status not in ("approved", "rejected"):
        raise ValueError(f"status must be approved or rejected, got {status!r}")
    rows = load_queue(path)
    item = rows.get(item_id)
    if item is None:
        return None
    item.status = status
    item.decided_at = datetime.now(IST).isoformat()
    save_queue(rows, path)
    return item
