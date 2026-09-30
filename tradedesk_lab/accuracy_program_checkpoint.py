"""Checkpoint-0 audit for the frozen accuracy-improvement program."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradedesk_lab.accuracy_program_contract import DEFAULT_ACCURACY_PROGRAM
from tradedesk_lab.accuracy_trial_ledger import load_trial_ledger
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

LEDGER_PATH = "docs/evidence/accuracy-trial-ledger.jsonl"
REQUIRED_PATHS = (
    "docs/plan-absolute-baseline-accuracy.md",
    "docs/accuracy-data-feasibility-spec.md",
    LEDGER_PATH,
    "tradedesk_lab/accuracy_program_contract.py",
    "tradedesk_lab/accuracy_trial_ledger.py",
    "tests_lab/test_accuracy_program_contract.py",
    "tests_lab/test_accuracy_trial_ledger.py",
)
EXPECTED_SEED_IDS = frozenset(
    {
        "aem-staged-baseline",
        "aem-baseline-gates",
        "aem-information-quality",
        "aem-v2-pipeline-race",
        "daily-mean-reversion-exit-search",
        "osr-selector-race",
        "ssm-mechanism-check",
        "aem-index-context-mechanism",
        "mcb-initial-trigger-audit",
        "m14-m18-model-preview",
    }
)


def run_accuracy_program_checkpoint(
    *, root: Path = ROOT, output: Path = OUTPUT
) -> dict[str, Any]:
    """Audit only governance artifacts; this function never reads market outcomes."""

    missing = [relative for relative in REQUIRED_PATHS if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"accuracy-program checkpoint files are missing: {missing}")
    ledger_path = root / LEDGER_PATH
    records = load_trial_ledger(ledger_path, root=root)
    actual_ids = {record["id"] for record in records}
    if actual_ids != EXPECTED_SEED_IDS:
        raise ValueError("accuracy-program seed ledger is incomplete or unexpected")
    if any(
        record["evidence_class"] != "consumed_historical_development"
        or record["candidate_nominated"]
        or record["baseline_improved"]
        for record in records
    ):
        raise ValueError("seed trials must remain consumed, non-promoting evidence")

    run_id = uuid4().hex
    run_directory = output / "accuracy_program" / "checkpoint_0" / "runs" / run_id
    report_path = run_directory / "report.json"
    report: dict[str, Any] = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "checkpoint_0_passed",
        "milestone": "accuracy_program_v1_checkpoint_0",
        "protocol": DEFAULT_ACCURACY_PROGRAM.to_dict(),
        "protocol_sha256": DEFAULT_ACCURACY_PROGRAM.sha256,
        "required_artifacts": {
            relative: digest(root / relative) for relative in REQUIRED_PATHS
        },
        "ledger": {
            "path": LEDGER_PATH,
            "sha256": digest(ledger_path),
            "record_count": len(records),
            "registered_trial_count": sum(record["trial_count"] for record in records),
            "registered_control_count": sum(record["controls_count"] for record in records),
            "all_consumed_development": True,
        },
        "new_outcomes_opened": False,
        "model_trained": False,
        "dashboard_changed": False,
        "production_changed": False,
        "candidate_nominated": False,
        "baseline_improved": False,
        "eligible_for_live": False,
        "canonical_strict_success_rate": (
            DEFAULT_ACCURACY_PROGRAM.canonical_strict_success_rate
        ),
        "next_checkpoint": "data_feasibility_and_point_in_time_manifest",
    }
    report["artifact_path"] = str(report_path.relative_to(root)).replace("\\", "/")
    write_json(report_path, report)
    pointer = output / "accuracy_program" / "checkpoint_0" / "latest.json"
    write_json(pointer, {"id": run_id, "report": report["artifact_path"]})
    return json.loads(report_path.read_text(encoding="utf-8"))
