"""Session-level self-learning refresh; never mutates or promotes the active model."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk.failure_attribution import failure_attribution_summary
from tradedesk.learning_dataset import build_learning_dataset
from tradedesk.signal_tracker import TrackedSignal


def refresh_learning_status(
    market: str,
    rows: dict[str, TrackedSignal],
    state_path: Path,
    *,
    minimum_new_mature: int = 20,
) -> dict[str, Any]:
    records = [asdict(row) for row in rows.values()]
    dataset = build_learning_dataset(records, market=market, purpose="prospective")
    previous: dict[str, Any] = {}
    if state_path.exists():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    # Only a completed challenger-training run may advance this boundary. Merely refreshing
    # status must not make newly mature evidence disappear on the next session.
    prior_ids = set(previous.get("challenger_consumed_signal_ids") or [])
    eligible_ids = [str(row["signal_id"]) for row in dataset.rows]
    new_ids = sorted(set(eligible_ids) - prior_ids)
    failures = failure_attribution_summary(records)
    status = (
        "ready_for_weekly_challenger"
        if len(new_ids) >= minimum_new_mature
        else "waiting_for_sealed_evidence"
    )
    report = {
        "updated_at": datetime.now(IST).isoformat(),
        "market": market,
        "status": status,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "eligible_mature": len(dataset.rows),
        "new_mature_since_last_refresh": len(new_ids),
        "minimum_new_mature_for_challenger": minimum_new_mature,
        "remaining_for_challenger": max(0, minimum_new_mature - len(new_ids)),
        "eligible_signal_ids": eligible_ids,
        "challenger_consumed_signal_ids": sorted(prior_ids),
        "last_challenger_dataset_id": previous.get("last_challenger_dataset_id"),
        "exclusions": dataset.exclusions,
        "evidence_classes": dict(Counter(str(row["evidence_class"]) for row in dataset.rows)),
        "failure_summary": failures,
        "active_model_changed": False,
        "promotion_authorized": False,
        "blockers": (
            []
            if status == "ready_for_weekly_challenger"
            else [
                f"need {max(0, minimum_new_mature - len(new_ids))} more newly mature "
                "sealed calls before a challenger dataset may be frozen"
            ]
        ),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(state_path)
    return report
