from __future__ import annotations

import json
from pathlib import Path

import pytest
from tradedesk_lab.accuracy_program_checkpoint import EXPECTED_SEED_IDS
from tradedesk_lab.accuracy_program_contract import EvidenceClass
from tradedesk_lab.accuracy_trial_ledger import (
    TrialRegistration,
    append_trial,
    enforce_cycle_trial_budget,
    load_trial_ledger,
)
from tradedesk_lab.artifacts import ROOT


def _registration(source_path: str, trial_id: str = "test-trial") -> TrialRegistration:
    return TrialRegistration(
        id=trial_id,
        family="test_family",
        stage="mechanism",
        evidence_class=EvidenceClass.CONSUMED_DEVELOPMENT,
        source_path=source_path,
        outcome="failed",
        trial_count=1,
        summary="synthetic ledger test",
    )


def _temporary_ledger(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source.md"
    source.write_text("immutable source\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    append_trial(ledger, _registration("source.md"), root=tmp_path)
    return ledger, source


def test_seed_ledger_accounts_for_all_prior_trials_as_consumed() -> None:
    records = load_trial_ledger(ROOT / "docs/evidence/accuracy-trial-ledger.jsonl")

    assert {record["id"] for record in records} == EXPECTED_SEED_IDS
    assert all(
        record["evidence_class"] == EvidenceClass.CONSUMED_DEVELOPMENT.value
        for record in records
    )
    assert not any(record["candidate_nominated"] for record in records)
    assert not any(record["baseline_improved"] for record in records)


def test_append_preserves_prefix_and_rejects_duplicate(tmp_path: Path) -> None:
    ledger, _ = _temporary_ledger(tmp_path)
    prefix = ledger.read_bytes()
    source_two = tmp_path / "source-two.md"
    source_two.write_text("second source\n", encoding="utf-8")

    append_trial(ledger, _registration("source-two.md", "test-trial-two"), root=tmp_path)

    assert ledger.read_bytes().startswith(prefix)
    assert len(load_trial_ledger(ledger, root=tmp_path)) == 2
    with pytest.raises(ValueError, match="already exists"):
        append_trial(ledger, _registration("source-two.md", "test-trial-two"), root=tmp_path)


def test_record_mutation_breaks_its_hash(tmp_path: Path) -> None:
    ledger, _ = _temporary_ledger(tmp_path)
    record = json.loads(ledger.read_text(encoding="utf-8"))
    record["summary"] = "rewritten"
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="record hash"):
        load_trial_ledger(ledger, root=tmp_path)


def test_reordering_breaks_sequence_or_chain(tmp_path: Path) -> None:
    ledger, _ = _temporary_ledger(tmp_path)
    source_two = tmp_path / "source-two.md"
    source_two.write_text("second source\n", encoding="utf-8")
    append_trial(ledger, _registration("source-two.md", "test-trial-two"), root=tmp_path)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sequence|chain"):
        load_trial_ledger(ledger, root=tmp_path)


def test_source_mutation_breaks_source_identity(tmp_path: Path) -> None:
    ledger, source = _temporary_ledger(tmp_path)
    source.write_text("changed source\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source fingerprint"):
        load_trial_ledger(ledger, root=tmp_path)


def test_unsafe_source_path_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"

    with pytest.raises(ValueError, match="relative workspace path|escapes"):
        append_trial(ledger, _registration(str((tmp_path / "outside.md").resolve())), root=tmp_path)


def test_trial_budget_accepts_boundary_and_rejects_each_excess() -> None:
    enforce_cycle_trial_budget(
        signal_families=3,
        mechanism_variants=12,
        model_pipelines_by_family={"family-1": 6, "family-2": 6},
    )
    with pytest.raises(ValueError, match="signal-family"):
        enforce_cycle_trial_budget(
            signal_families=4,
            mechanism_variants=12,
            model_pipelines_by_family={"family-1": 6},
        )
    with pytest.raises(ValueError, match="mechanism-variant"):
        enforce_cycle_trial_budget(
            signal_families=3,
            mechanism_variants=13,
            model_pipelines_by_family={"family-1": 6},
        )
    with pytest.raises(ValueError, match="model-pipeline"):
        enforce_cycle_trial_budget(
            signal_families=3,
            mechanism_variants=12,
            model_pipelines_by_family={"family-1": 7},
        )
