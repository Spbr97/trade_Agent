"""Fail-closed Checkpoint-1 data feasibility audit for the accuracy program.

The audit inventories already-frozen, outcome-free evidence. It never downloads data,
opens a locked partition, constructs a signal, or trains a model.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradedesk_lab.accuracy_program_contract import DEFAULT_ACCURACY_PROGRAM
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json


class LayerStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DataLayerSpec:
    id: str
    required_fields: tuple[str, ...]
    causal_availability: str


REQUIRED_LAYERS = (
    DataLayerSpec("stock_prices", ("M1", "M5", "M15", "D1"), "exchange_bar_close"),
    DataLayerSpec(
        "market_context",
        ("nifty", "bank_nifty", "point_in_time_sector_indices"),
        "exchange_bar_close",
    ),
    DataLayerSpec(
        "symbol_master",
        ("listing_status", "sector", "industry", "membership_intervals"),
        "effective_or_publication_time",
    ),
    DataLayerSpec(
        "cross_section",
        ("candidate_excluded_breadth", "rank", "peer_aggregates"),
        "latest_completed_peer_bars",
    ),
    DataLayerSpec(
        "announcements",
        ("exchange_id", "category", "exchange_dissemination_time"),
        "exchange_dissemination_time",
    ),
    DataLayerSpec(
        "corporate_actions",
        ("action", "ex_or_effective_date", "adjustment_provenance"),
        "official_publication_time",
    ),
    DataLayerSpec(
        "execution",
        ("charges", "spread_or_depth_proxy", "participation"),
        "decision_time",
    ),
    DataLayerSpec(
        "provenance",
        ("source", "retrieved_at", "version_or_hash", "freshness"),
        "capture_time",
    ),
)


@dataclass(frozen=True)
class AccuracyDataManifestProtocol:
    version: str = "accuracy-data-manifest-v1"
    accuracy_program_sha256: str = DEFAULT_ACCURACY_PROGRAM.sha256
    cohort_a_target: int = 50
    cohort_b_target: int = 200
    consumed_through_session: str = "2026-09-18"
    request_budget: int = 0
    required_layers: tuple[DataLayerSpec, ...] = REQUIRED_LAYERS

    def __post_init__(self) -> None:
        if asdict(self) != asdict(_frozen_protocol()):
            raise ValueError("accuracy data-manifest protocol is frozen")

    @property
    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _frozen_protocol() -> AccuracyDataManifestProtocol:
    value = object.__new__(AccuracyDataManifestProtocol)
    object.__setattr__(value, "version", "accuracy-data-manifest-v1")
    object.__setattr__(value, "accuracy_program_sha256", DEFAULT_ACCURACY_PROGRAM.sha256)
    object.__setattr__(value, "cohort_a_target", 50)
    object.__setattr__(value, "cohort_b_target", 200)
    object.__setattr__(value, "consumed_through_session", "2026-09-18")
    object.__setattr__(value, "request_budget", 0)
    object.__setattr__(value, "required_layers", REQUIRED_LAYERS)
    return value


DEFAULT_DATA_MANIFEST_PROTOCOL = _frozen_protocol()
FORBIDDEN_OUTCOME_TOKENS = frozenset(
    {
        "outcome",
        "target_hit",
        "stop_hit",
        "mfe",
        "mae",
        "future_return",
        "resolved",
        "strict_success",
        "profit",
        "pnl",
    }
)


def reject_outcome_fields(value: Any, *, path: str = "root") -> None:
    """Reject outcome-like fields recursively from feature/manifest artifacts."""

    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in FORBIDDEN_OUTCOME_TOKENS):
                raise ValueError(f"outcome field is forbidden in data manifest: {path}.{key}")
            reject_outcome_fields(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_outcome_fields(item, path=f"{path}[{index}]")


def candidate_excluded_peer_mean(
    *,
    candidate: str,
    peer_values: dict[str, float],
    decision_epoch: int,
    peer_epochs: dict[str, int],
) -> float:
    """Return a peer mean only when the candidate and future observations are absent."""

    if candidate in peer_values or candidate in peer_epochs:
        raise ValueError("candidate must be excluded from peer statistics")
    if set(peer_values) != set(peer_epochs) or not peer_values:
        raise ValueError("peer values and timestamps must form a nonempty complete set")
    if any(epoch > decision_epoch for epoch in peer_epochs.values()):
        raise ValueError("future peer observation is unavailable at decision time")
    values = list(peer_values.values())
    if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in values):
        raise ValueError("peer statistic contains a nonfinite value")
    return float(sum(values) / len(values))


def validate_coverage_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Validate explicit coordinates; missing provenance never becomes coverage."""

    required = {
        "cohort",
        "symbol",
        "session",
        "layer",
        "status",
        "source_version",
        "source_sha256",
        "available_at",
        "exclusion_reason",
    }
    coordinates: set[tuple[str, str, str, str]] = set()
    accepted = 0
    unavailable = 0
    for row in rows:
        reject_outcome_fields(row)
        if set(row) != required:
            raise ValueError("coverage row schema is incomplete or contains extra fields")
        coordinate = tuple(str(row[key]) for key in ("cohort", "symbol", "session", "layer"))
        if coordinate in coordinates:
            raise ValueError("coverage coordinate is duplicated")
        coordinates.add(coordinate)
        try:
            status = LayerStatus(row["status"])
        except ValueError as exc:
            raise ValueError("coverage status is invalid") from exc
        if status is LayerStatus.AVAILABLE:
            if not all(row[key] for key in ("source_version", "source_sha256", "available_at")):
                raise ValueError("available coverage lacks identity or causal availability")
            if row["exclusion_reason"] is not None:
                raise ValueError("available coverage cannot have an exclusion reason")
            accepted += 1
        else:
            if not row["exclusion_reason"]:
                raise ValueError("partial or unavailable coverage requires an exclusion reason")
            unavailable += 1
    return {"coordinates": len(coordinates), "available": accepted, "not_available": unavailable}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load feasibility evidence: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("feasibility evidence must be a JSON object")
    return value


def _source_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "nse_announcements",
            "url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "access": "public_query",
            "attempted": False,
            "reason": "historical bulk completeness and permitted automation not yet verified",
        },
        {
            "id": "nse_index_constituents",
            "url": "https://www.nseindia.com/static/nse-indices/index-data-subscription",
            "access": "license_or_subscription_required",
            "attempted": False,
            "reason": "point-in-time constituent history not licensed locally",
        },
        {
            "id": "nse_historical_order_trade",
            "url": "https://www.nseindia.com/static/market-data/eod-historical-data-subscription",
            "access": "paid_subscription_required",
            "attempted": False,
            "reason": "historical spreads/depth/trades not subscribed locally",
        },
        {
            "id": "nse_cash_charges",
            "url": "https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies",
            "access": "public_reference",
            "attempted": False,
            "reason": (
                "broker contract-note golden source and historical effective intervals "
                "still required"
            ),
        },
    ]


def _write_request_ledger(path: Path, sources: list[dict[str, Any]]) -> str:
    """Write a deterministic zero-request chain without contacting any source."""

    previous = "0" * 64
    lines: list[str] = []
    for sequence, source in enumerate(sources, start=1):
        record = {
            "sequence": sequence,
            "source_id": source["id"],
            "source_url": source["url"],
            "attempted": False,
            "requests_made": 0,
            "status": "not_requested",
            "reason": source["reason"],
            "previous_record_sha256": previous,
        }
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        record["record_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        lines.append(line)
        previous = record["record_sha256"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)
    return digest(path)


def load_acquisition_request_ledger(path: Path, *, maximum_requests: int) -> list[dict[str, Any]]:
    """Validate the complete acquisition ledger and its append-only hash chain."""

    if maximum_requests < 0:
        raise ValueError("maximum requests cannot be negative")
    previous = "0" * 64
    records: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("acquisition ledger contains invalid JSON") from exc
        if record.get("sequence") != sequence:
            raise ValueError("acquisition ledger sequence is not contiguous")
        if record.get("previous_record_sha256") != previous:
            raise ValueError("acquisition ledger hash chain is broken")
        claimed = record.get("record_sha256")
        payload = {key: value for key, value in record.items() if key != "record_sha256"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if claimed != hashlib.sha256(canonical.encode()).hexdigest():
            raise ValueError("acquisition ledger record hash is invalid")
        source_id = record.get("source_id")
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise ValueError("acquisition ledger source id is missing or duplicated")
        source_ids.add(source_id)
        if not str(record.get("source_url", "")).startswith("https://"):
            raise ValueError("acquisition ledger source URL is invalid")
        requests = record.get("requests_made")
        if isinstance(requests, bool) or not isinstance(requests, int) or requests < 0:
            raise ValueError("acquisition ledger request count is invalid")
        if not isinstance(record.get("attempted"), bool):
            raise ValueError("acquisition ledger attempted flag is invalid")
        if not record["attempted"] and requests != 0:
            raise ValueError("unattempted acquisition cannot record requests")
        records.append(record)
        previous = claimed
    if sum(record["requests_made"] for record in records) > maximum_requests:
        raise ValueError("acquisition request budget exceeded")
    return records


def run_data_feasibility_audit(
    *, root: Path = ROOT, output: Path = OUTPUT
) -> dict[str, Any]:
    """Publish the honest Checkpoint-1 feasibility verdict from tracked evidence."""

    protocol = DEFAULT_DATA_MANIFEST_PROTOCOL
    universe_path = root / "docs/evidence/aem-v2-universe-audit.json"
    context_path = root / "docs/evidence/aem-market-sector-context-readiness.json"
    universe = _read_json(universe_path)
    context = _read_json(context_path)
    if universe.get("source_dataset_id") != DEFAULT_ACCURACY_PROGRAM.canonical_dataset_id:
        raise ValueError("universe evidence does not identify the canonical dataset")
    if universe.get("universe_symbols") != protocol.cohort_a_target:
        raise ValueError("cohort A evidence does not contain exactly 50 symbols")
    per_code = universe.get("per_code_summary")
    if not isinstance(per_code, dict) or len(per_code) != protocol.cohort_a_target:
        raise ValueError("cohort A symbol accounting is incomplete")
    codes = sorted(per_code)
    if context.get("dataset_id") != DEFAULT_ACCURACY_PROGRAM.canonical_dataset_id:
        raise ValueError("market-context evidence identifies a different dataset")

    cohort_a = {
        "id": "cohort_a_existing_50",
        "status": "partial_not_point_in_time_membership",
        "selection_freeze": "2026-03-24",
        "evaluation_through": protocol.consumed_through_session,
        "evidence_class": "consumed_historical_development",
        "symbols": codes,
        "symbol_count": len(codes),
        "evaluation_sessions": universe["evaluation_sessions"],
        "selection_rule": "preperiod_median_turnover_descending_with_symbol_code_tie_break",
        "point_in_time_membership_verified": False,
        "sector_intervals_available": False,
        "source_path": str(universe_path.relative_to(root)).replace("\\", "/"),
        "source_sha256": digest(universe_path),
    }
    cohort_b = {
        "id": "cohort_b_required_200",
        "status": "unavailable",
        "target_symbol_count": protocol.cohort_b_target,
        "symbols": [],
        "symbol_count": 0,
        "point_in_time_membership_verified": False,
        "sector_intervals_available": False,
        "exclusion_reason": "no historical point-in-time 200-stock membership source is local",
    }
    layer_inventory = [
        {
            "layer": "stock_prices",
            "status": "partial",
            "coverage": "5999/6000 Cohort-A M1 symbol-sessions; D1 present; M5/M15 not frozen",
            "source_path": cohort_a["source_path"],
            "source_sha256": cohort_a["source_sha256"],
            "gap": "NSE_3063 on 2026-04-30 is incomplete and excluded",
        },
        {
            "layer": "market_context",
            "status": "partial",
            "coverage": "359/360 code-sessions across three broad indices",
            "source_path": str(context_path.relative_to(root)).replace("\\", "/"),
            "source_sha256": digest(context_path),
            "gap": "point-in-time sector indices absent; one Nifty Financial session incomplete",
        },
        {
            "layer": "symbol_master",
            "status": "unavailable",
            "coverage": "current metadata only",
            "gap": "historical listing/status/sector/index membership intervals absent",
        },
        {
            "layer": "cross_section",
            "status": "unavailable",
            "coverage": "not constructible without point-in-time sector/liquidity cohorts",
            "gap": "candidate-excluded peer population cannot yet be frozen",
        },
        {
            "layer": "announcements",
            "status": "unavailable",
            "coverage": "no versioned historical local corpus",
            "gap": "exchange dissemination timestamps and completeness unverified",
        },
        {
            "layer": "corporate_actions",
            "status": "partial",
            "coverage": "price-discontinuity heuristic only",
            "gap": "official publication timestamps and adjustment provenance absent",
        },
        {
            "layer": "execution",
            "status": "unavailable",
            "coverage": "modeled costs only",
            "gap": "broker golden contract notes and historical spread/depth proxy absent",
        },
        {
            "layer": "provenance",
            "status": "partial",
            "coverage": "existing price/context artifacts are hashed",
            "gap": "missing layers have no local source version or retrieval ledger",
        },
    ]
    if {row["layer"] for row in layer_inventory} != {layer.id for layer in REQUIRED_LAYERS}:
        raise ValueError("required data-layer inventory is incomplete")
    sources = _source_catalog()
    gates = {
        "cohort_a_point_in_time": False,
        "cohort_b_frozen": False,
        "complete_coordinate_accounting": False,
        "announcement_timestamp_golden_cases": False,
        "sector_membership_golden_cases": False,
        "execution_golden_cases": False,
        "future_and_candidate_exclusion_dataset_mutations": False,
        "d2_boundary_locked_without_outcomes": False,
        "at_least_one_signal_family_data_feasible": False,
    }
    run_id = uuid4().hex
    run_directory = output / "accuracy_program" / "checkpoint_1" / "runs" / run_id
    test_path = root / "tests_lab/test_accuracy_data_manifest.py"
    if not test_path.is_file():
        test_path = ROOT / "tests_lab/test_accuracy_data_manifest.py"
    schema = {
        "version": protocol.version,
        "coordinate_key": ["cohort", "symbol", "session", "layer"],
        "required_fields": [
            "status",
            "source_version",
            "source_sha256",
            "available_at",
            "exclusion_reason",
        ],
        "forbidden_outcome_tokens": sorted(FORBIDDEN_OUTCOME_TOKENS),
        "missing_policy": "not_available_never_pass",
    }
    coverage = {
        "complete_coordinate_table_available": False,
        "known_coordinates": {
            "cohort_a_stock_symbol_sessions": universe["total_code_sessions"],
            "cohort_a_stock_symbol_sessions_available": universe["included_code_sessions"],
            "broad_index_code_sessions": context["coverage"]["total_code_sessions"],
            "broad_index_code_sessions_available": context["coverage"][
                "complete_code_sessions"
            ],
        },
        "unaccounted_reason": (
            "Cohort B symbols and historical point-in-time memberships are unavailable; "
            "fabricating coordinates is prohibited"
        ),
    }
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": protocol.version,
        "status": "checkpoint_1_stopped_data_infeasible",
        "protocol_sha256": protocol.sha256,
        "accuracy_program_sha256": protocol.accuracy_program_sha256,
        "implementation_sha256": digest(Path(__file__).resolve()),
        "tests_sha256": digest(test_path),
        "source_evidence_sha256": {
            "cohort_a": digest(universe_path),
            "market_context": digest(context_path),
        },
        "cohorts": {"a": cohort_a, "b": cohort_b},
        "layers": layer_inventory,
        "coverage": coverage,
        "source_catalog": sources,
        "request_budget": {"maximum_requests": 0, "requests_made": 0},
        "d2": {
            "declared": False,
            "boundary": None,
            "sha256": None,
            "opened": False,
            "reason": "no qualifying later unopened source has been acquired",
        },
        "gates": gates,
        "checkpoint_passed": False,
        "checkpoint_2_authorized": False,
        "authorized_signal_families": [],
        "new_outcomes_opened": False,
        "model_trained": False,
        "candidate_nominated": False,
        "baseline_improved": False,
        "eligible_for_live": False,
        "canonical_strict_success_rate": (
            DEFAULT_ACCURACY_PROGRAM.canonical_strict_success_rate
        ),
        "required_next_action": (
            "license or otherwise obtain timestamped point-in-time membership, Cohort-B "
            "prices, announcements and execution evidence; then rerun Checkpoint 1"
        ),
    }
    reject_outcome_fields(
        {
            "cohorts": report["cohorts"],
            "layers": layer_inventory,
            "coverage": coverage,
            "source_catalog": sources,
            "d2": report["d2"],
        }
    )
    write_json(run_directory / "manifest_schema.json", schema)
    write_json(run_directory / "cohort_a.json", cohort_a)
    write_json(run_directory / "cohort_b.json", cohort_b)
    write_json(run_directory / "layer_inventory.json", layer_inventory)
    write_json(run_directory / "coverage_summary.json", coverage)
    write_json(run_directory / "source_catalog.json", sources)
    request_ledger_sha256 = _write_request_ledger(
        run_directory / "acquisition_request_ledger.jsonl", sources
    )
    load_acquisition_request_ledger(
        run_directory / "acquisition_request_ledger.jsonl",
        maximum_requests=protocol.request_budget,
    )
    report["request_budget"]["ledger_sha256"] = request_ledger_sha256
    write_json(run_directory / "report.json", report)
    report_path = run_directory / "report.json"
    report["artifact_path"] = str(report_path.relative_to(root)).replace("\\", "/")
    write_json(report_path, report)
    write_json(
        output / "accuracy_program" / "checkpoint_1" / "latest.json",
        {"id": run_id, "report": report["artifact_path"]},
    )
    return report
