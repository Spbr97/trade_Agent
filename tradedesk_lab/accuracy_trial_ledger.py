"""Append-only, hash-chained trial accounting for the accuracy program."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradedesk_lab.accuracy_program_contract import (
    DEFAULT_ACCURACY_PROGRAM,
    AccuracyProgramProtocol,
    EvidenceClass,
)
from tradedesk_lab.artifacts import ROOT, digest

GENESIS_SHA256 = "0" * 64
TERMINAL_OUTCOMES = frozenset({"failed", "passed", "stopped", "unavailable"})


@dataclass(frozen=True)
class TrialRegistration:
    id: str
    family: str
    stage: str
    evidence_class: EvidenceClass
    source_path: str
    outcome: str
    trial_count: int
    attempt_count: int = 1
    controls_count: int = 0
    evaluated: bool = True
    candidate_nominated: bool = False
    baseline_improved: bool = False
    summary: str = ""


def enforce_cycle_trial_budget(
    *,
    signal_families: int,
    mechanism_variants: int,
    model_pipelines_by_family: dict[str, int],
    protocol: AccuracyProgramProtocol = DEFAULT_ACCURACY_PROGRAM,
) -> None:
    """Reject a research cycle that exceeds any preregistered search budget."""

    _positive_integer(signal_families, "signal_families")
    _positive_integer(mechanism_variants, "mechanism_variants")
    if signal_families > protocol.max_signal_families_per_cycle:
        raise ValueError("signal-family trial budget exceeded")
    if mechanism_variants > protocol.max_mechanism_variants_per_cycle:
        raise ValueError("mechanism-variant trial budget exceeded")
    if not model_pipelines_by_family or len(model_pipelines_by_family) > signal_families:
        raise ValueError("model-pipeline family accounting is invalid")
    for family, count in model_pipelines_by_family.items():
        if not family:
            raise ValueError("model-pipeline family requires an identifier")
        _positive_integer(count, "model_pipelines")
        if count > protocol.max_model_pipelines_per_surviving_family:
            raise ValueError("model-pipeline trial budget exceeded")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _record_sha256(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _source(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("trial source_path must be a relative workspace path")
    root_resolved = root.resolve()
    source = (root / relative_path).resolve()
    if not source.is_relative_to(root_resolved):
        raise ValueError("trial source_path escapes the workspace")
    if not source.is_file():
        raise ValueError(f"trial source does not exist: {relative_path}")
    return source


def _positive_integer(value: Any, name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < (0 if allow_zero else 1):
        raise ValueError(f"{name} is outside its permitted range")
    return value


def load_trial_ledger(
    path: Path,
    *,
    root: Path = ROOT,
    protocol: AccuracyProgramProtocol = DEFAULT_ACCURACY_PROGRAM,
) -> list[dict[str, Any]]:
    """Validate and return every chained record; malformed evidence never loads."""

    if not path.is_file():
        raise ValueError("accuracy-program trial ledger is missing")
    records: list[dict[str, Any]] = []
    previous = GENESIS_SHA256
    ids: set[str] = set()
    for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError("trial ledger contains a blank record")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("trial ledger contains invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError("trial ledger records must be objects")
        if record.get("sequence") != sequence:
            raise ValueError("trial ledger sequence is not contiguous")
        if record.get("previous_record_sha256") != previous:
            raise ValueError("trial ledger hash chain is broken")
        if record.get("record_sha256") != _record_sha256(record):
            raise ValueError("trial ledger record hash is invalid")
        if record.get("protocol_sha256") != protocol.sha256:
            raise ValueError("trial ledger protocol fingerprint is invalid")
        trial_id = record.get("id")
        if not isinstance(trial_id, str) or not trial_id or trial_id in ids:
            raise ValueError("trial ledger id is missing or duplicated")
        ids.add(trial_id)
        try:
            EvidenceClass(record.get("evidence_class"))
        except ValueError as exc:
            raise ValueError("trial ledger evidence class is invalid") from exc
        if record.get("outcome") not in TERMINAL_OUTCOMES:
            raise ValueError("trial ledger outcome is not terminal")
        for name in ("evaluated", "candidate_nominated", "baseline_improved"):
            if not isinstance(record.get(name), bool):
                raise ValueError(f"trial ledger {name} must be boolean")
        _positive_integer(record.get("trial_count"), "trial_count")
        _positive_integer(record.get("attempt_count"), "attempt_count")
        _positive_integer(record.get("controls_count"), "controls_count", allow_zero=True)
        source = _source(root, str(record.get("source_path", "")))
        if record.get("source_sha256") != digest(source):
            raise ValueError("trial ledger source fingerprint is invalid")
        previous = record["record_sha256"]
        records.append(record)
    return records


def append_trial(
    path: Path,
    registration: TrialRegistration,
    *,
    root: Path = ROOT,
    protocol: AccuracyProgramProtocol = DEFAULT_ACCURACY_PROGRAM,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Validate the whole prefix before appending exactly one immutable record."""

    records = load_trial_ledger(path, root=root, protocol=protocol) if path.exists() else []
    if any(record["id"] == registration.id for record in records):
        raise ValueError("trial ledger id already exists")
    if registration.outcome not in TERMINAL_OUTCOMES:
        raise ValueError("trial outcome must be terminal")
    _positive_integer(registration.trial_count, "trial_count")
    _positive_integer(registration.attempt_count, "attempt_count")
    _positive_integer(registration.controls_count, "controls_count", allow_zero=True)
    source = _source(root, registration.source_path)
    record: dict[str, Any] = {
        "sequence": len(records) + 1,
        "id": registration.id,
        "recorded_at": recorded_at or datetime.now(UTC).isoformat(),
        "protocol_sha256": protocol.sha256,
        "previous_record_sha256": records[-1]["record_sha256"] if records else GENESIS_SHA256,
        "family": registration.family,
        "stage": registration.stage,
        "evidence_class": registration.evidence_class.value,
        "source_path": registration.source_path,
        "source_sha256": digest(source),
        "evaluated": registration.evaluated,
        "trial_count": registration.trial_count,
        "attempt_count": registration.attempt_count,
        "controls_count": registration.controls_count,
        "outcome": registration.outcome,
        "candidate_nominated": registration.candidate_nominated,
        "baseline_improved": registration.baseline_improved,
        "summary": registration.summary,
    }
    record["record_sha256"] = _record_sha256(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(_canonical(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record


def new_trial_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"
