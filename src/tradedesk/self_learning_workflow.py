"""Session-level self-learning refresh; never mutates or promotes the active model."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk.challenger_workflow import (
    challenger_is_due,
    drift_snapshot,
    performance_snapshot,
    record_failed_experiment,
    run_challenger_experiment,
)
from tradedesk.failure_attribution import failure_attribution_summary
from tradedesk.learning_dataset import build_learning_dataset
from tradedesk.signal_tracker import TrackedSignal


def _write_status(state_path: Path, report: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(state_path)


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
    performance = performance_snapshot(dataset)
    drift = drift_snapshot(dataset)
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
        "last_challenger_experiment_id": previous.get("last_challenger_experiment_id"),
        "last_challenger_status": previous.get("last_challenger_status"),
        "last_challenger_conclusion": previous.get("last_challenger_conclusion"),
        "last_challenger_completed_at": previous.get("last_challenger_completed_at"),
        "exclusions": dataset.exclusions,
        "evidence_classes": dict(Counter(str(row["evidence_class"]) for row in dataset.rows)),
        "failure_summary": failures,
        "performance": performance,
        "drift": drift,
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
    _write_status(state_path, report)
    return report


def run_scheduled_challenger(
    market: str,
    rows: dict[str, TrackedSignal],
    state_path: Path,
    *,
    output_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run once when ready, then no more than weekly; never alter the active model."""

    current = now or datetime.now(IST)
    state = refresh_learning_status(market, rows, state_path)
    if not challenger_is_due(state, current):
        return state
    records = [asdict(row) for row in rows.values()]
    dataset = build_learning_dataset(records, market=market, purpose="prospective")
    root = output_root or Path(f"data/models/self_learning/{market}")
    try:
        experiment = run_challenger_experiment(dataset, root)
    except Exception as exc:
        # Challenger diagnostics cannot take down the established market collector. The
        # error remains explicit and cannot look like a completed or passing experiment.
        failed = record_failed_experiment(root, dataset, exc)
        state.update(
            status="challenger_failed",
            blockers=[f"challenger failed: {type(exc).__name__}: {exc}"],
            last_challenger_experiment_id=failed["experiment_id"],
            last_challenger_status=failed["status"],
            last_challenger_conclusion=failed["conclusion"],
            active_model_changed=False,
            promotion_authorized=False,
        )
        _write_status(state_path, state)
        return state
    state.update(
        last_challenger_experiment_id=experiment.get("experiment_id"),
        last_challenger_status=experiment.get("status"),
        last_challenger_conclusion=experiment.get("conclusion"),
        current_challenger=experiment,
        active_model_changed=False,
        promotion_authorized=False,
    )
    if str(experiment.get("status", "")).startswith("completed_"):
        eligible_ids = sorted(str(row["signal_id"]) for row in dataset.rows)
        state.update(
            status="challenger_completed_no_promotion",
            challenger_consumed_signal_ids=eligible_ids,
            last_challenger_dataset_id=dataset.dataset_id,
            last_challenger_completed_at=current.isoformat(),
            new_mature_since_last_refresh=0,
            remaining_for_challenger=state["minimum_new_mature_for_challenger"],
            blockers=experiment.get("blockers") or [],
        )
    else:
        state.update(
            status="challenger_blocked",
            blockers=experiment.get("blockers") or ["challenger evidence is not trainable"],
        )
    _write_status(state_path, state)
    return state
