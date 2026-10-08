"""Session-level self-learning refresh; never mutates or promotes the active model."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.challenger_workflow import (
    drift_snapshot,
    performance_snapshot,
    record_failed_experiment,
    run_challenger_experiment,
)
from tradedesk.data.candle_store import CandleStore
from tradedesk.exit_contract_race import evaluate_exit_contract_race
from tradedesk.failure_attribution import failure_attribution_summary
from tradedesk.learning_dataset import (
    LearningDataset,
    build_learning_dataset,
    build_timing_opportunities,
)
from tradedesk.prediction_ledger import canonical_sha256
from tradedesk.signal_tracker import TrackedSignal


def _write_status(state_path: Path, report: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(state_path)


def _contract_dataset(dataset: LearningDataset, contract_version: str) -> LearningDataset:
    rows = tuple(
        row for row in dataset.rows if str(row.get("contract_version")) == contract_version
    )
    return LearningDataset(
        dataset_id=canonical_sha256(
            {
                "source_dataset_id": dataset.dataset_id,
                "contract_version": contract_version,
                "signal_ids": [row["signal_id"] for row in rows],
            }
        ),
        version=dataset.version,
        market=dataset.market,
        purpose=dataset.purpose,
        rows=rows,
        exclusions=dict(dataset.exclusions),
        source_records=dataset.source_records,
    )


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
    contract_status: dict[str, Any] = {}
    for contract_version in sorted(
        {str(row["contract_version"]) for row in dataset.rows}
    ):
        contract_ids = [
            str(row["signal_id"])
            for row in dataset.rows
            if row["contract_version"] == contract_version
        ]
        contract_new = sorted(set(contract_ids) - prior_ids)
        contract_status[contract_version] = {
            "eligible_mature": len(contract_ids),
            "new_mature": len(contract_new),
            "remaining": max(0, minimum_new_mature - len(contract_new)),
            "status": (
                "ready_for_weekly_challenger"
                if len(contract_new) >= minimum_new_mature
                else "waiting_for_sealed_evidence"
            ),
        }
    failures = failure_attribution_summary(records)
    performance = performance_snapshot(dataset)
    drift = drift_snapshot(dataset)
    status = (
        "ready_for_weekly_challenger"
        if any(
            item["status"] == "ready_for_weekly_challenger"
            for item in contract_status.values()
        )
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
        "remaining_for_challenger": min(
            (item["remaining"] for item in contract_status.values()),
            default=minimum_new_mature,
        ),
        "contract_status": contract_status,
        "eligible_signal_ids": eligible_ids,
        "challenger_consumed_signal_ids": sorted(prior_ids),
        "last_challenger_dataset_id": previous.get("last_challenger_dataset_id"),
        "last_challenger_experiment_id": previous.get("last_challenger_experiment_id"),
        "last_challenger_status": previous.get("last_challenger_status"),
        "last_challenger_conclusion": previous.get("last_challenger_conclusion"),
        "last_challenger_completed_at": previous.get("last_challenger_completed_at"),
        "last_challenger_completed_at_by_contract": previous.get(
            "last_challenger_completed_at_by_contract", {}
        ),
        "exclusions": dataset.exclusions,
        "evidence_classes": dict(Counter(str(row["evidence_class"]) for row in dataset.rows)),
        "failure_summary": failures,
        "exit_contract_race": evaluate_exit_contract_race(records, market=market),
        "performance": performance,
        "drift": drift,
        "active_model_changed": False,
        "promotion_authorized": False,
        "blockers": (
            []
            if status == "ready_for_weekly_challenger"
            else [
                "no single exit contract has enough new mature sealed calls; "
                + "; ".join(
                    f"{version} needs {item['remaining']}"
                    for version, item in contract_status.items()
                )
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
    candle_store: CandleStore | None = None,
) -> dict[str, Any]:
    """Run once when ready, then no more than weekly; never alter the active model."""

    current = now or datetime.now(IST)
    state = refresh_learning_status(market, rows, state_path)
    records = [asdict(row) for row in rows.values()]
    dataset = build_learning_dataset(records, market=market, purpose="prospective")
    timing_opportunities = build_timing_opportunities(
        records, market=market, purpose="prospective"
    )
    root = output_root or Path(f"data/models/self_learning/{market}")
    completed_at_by_contract = dict(
        state.get("last_challenger_completed_at_by_contract") or {}
    )
    due_contracts = []
    for version, contract_state in state.get("contract_status", {}).items():
        if contract_state.get("status") != "ready_for_weekly_challenger":
            continue
        completed_at = completed_at_by_contract.get(version)
        if completed_at:
            try:
                if current - datetime.fromisoformat(str(completed_at)) < timedelta(days=7):
                    continue
            except ValueError:
                pass
        due_contracts.append(version)
    if not due_contracts:
        return state

    experiments: dict[str, Any] = {}
    consumed = set(state.get("challenger_consumed_signal_ids") or [])
    blockers: list[str] = []
    completed_any = False
    for version in due_contracts:
        contract_dataset = _contract_dataset(dataset, version)
        contract_root = root / "contracts" / version
        try:
            experiment = run_challenger_experiment(
                contract_dataset,
                contract_root,
                bars_loader=(
                    (lambda code: candle_store.load(code, Interval.D1, adjusted=False))
                    if candle_store is not None
                    else None
                ),
                timing_opportunities=timing_opportunities,
            )
        except Exception as exc:
            experiment = record_failed_experiment(contract_root, contract_dataset, exc)
            blockers.append(f"{version}: {type(exc).__name__}: {exc}")
        experiments[version] = experiment
        if str(experiment.get("status", "")).startswith("completed_"):
            completed_any = True
            consumed.update(str(row["signal_id"]) for row in contract_dataset.rows)
            completed_at_by_contract[version] = current.isoformat()
        else:
            blockers.extend(
                f"{version}: {reason}" for reason in experiment.get("blockers") or []
            )
    latest_version = due_contracts[-1]
    latest = experiments[latest_version]
    state.update(
        status=(
            "challenger_completed_no_promotion"
            if completed_any and not blockers
            else "challenger_completed_with_blockers"
            if completed_any
            else "challenger_failed_or_blocked"
        ),
        challenger_consumed_signal_ids=sorted(consumed),
        last_challenger_dataset_id=latest.get("dataset_id"),
        last_challenger_experiment_id=latest.get("experiment_id"),
        last_challenger_status=latest.get("status"),
        last_challenger_conclusion=latest.get("conclusion"),
        last_challenger_completed_at=(current.isoformat() if completed_any else None),
        last_challenger_completed_at_by_contract=completed_at_by_contract,
        current_challengers=experiments,
        current_challenger=latest,
        new_mature_since_last_refresh=(
            0 if completed_any else len(state.get("eligible_signal_ids", []))
        ),
        remaining_for_challenger=state["minimum_new_mature_for_challenger"],
        blockers=blockers or latest.get("blockers") or [],
        active_model_changed=False,
        promotion_authorized=False,
    )
    _write_status(state_path, state)
    return state
