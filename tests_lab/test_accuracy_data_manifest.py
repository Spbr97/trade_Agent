from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from tradedesk_lab.accuracy_data_manifest import (
    DEFAULT_DATA_MANIFEST_PROTOCOL,
    candidate_excluded_peer_mean,
    load_acquisition_request_ledger,
    reject_outcome_fields,
    run_data_feasibility_audit,
    validate_coverage_rows,
)
from tradedesk_lab.accuracy_program_contract import DEFAULT_ACCURACY_PROGRAM


def _coverage_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cohort": "a",
        "symbol": "NSE_1",
        "session": "2026-01-01",
        "layer": "stock_prices",
        "status": "available",
        "source_version": "v1",
        "source_sha256": "a" * 64,
        "available_at": "2026-01-01T09:16:00+05:30",
        "exclusion_reason": None,
    }
    row.update(updates)
    return row


def _evidence(root: Path, *, symbols: int = 50) -> None:
    folder = root / "docs/evidence"
    folder.mkdir(parents=True)
    universe = {
        "source_dataset_id": DEFAULT_ACCURACY_PROGRAM.canonical_dataset_id,
        "universe_symbols": symbols,
        "evaluation_sessions": 120,
        "total_code_sessions": symbols * 120,
        "included_code_sessions": symbols * 120 - 1,
        "per_code_summary": {
            f"NSE_{index}": {"included_sessions": 120} for index in range(symbols)
        },
    }
    context = {
        "dataset_id": DEFAULT_ACCURACY_PROGRAM.canonical_dataset_id,
        "coverage": {"total_code_sessions": 360, "complete_code_sessions": 359},
    }
    (folder / "aem-v2-universe-audit.json").write_text(
        json.dumps(universe), encoding="utf-8"
    )
    (folder / "aem-market-sector-context-readiness.json").write_text(
        json.dumps(context), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("cohort_a_target", 49), ("cohort_b_target", 199), ("request_budget", 1)],
)
def test_manifest_protocol_cannot_be_weakened(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="frozen"):
        replace(DEFAULT_DATA_MANIFEST_PROTOCOL, **{field: value})


@pytest.mark.parametrize(
    "field",
    ["outcome", "target_hit_at", "stop_hit", "mfe_30m", "future_return", "net_pnl"],
)
def test_outcome_fields_never_enter_manifest(field: str) -> None:
    with pytest.raises(ValueError, match="outcome field"):
        reject_outcome_fields({"nested": [{field: 1}]})


def test_coverage_requires_identity_and_explicit_missing_reason() -> None:
    assert validate_coverage_rows([_coverage_row()]) == {
        "coordinates": 1,
        "available": 1,
        "not_available": 0,
    }
    with pytest.raises(ValueError, match="identity"):
        validate_coverage_rows([_coverage_row(source_sha256=None)])
    with pytest.raises(ValueError, match="exclusion reason"):
        validate_coverage_rows([_coverage_row(status="unavailable")])


def test_duplicate_coverage_coordinate_is_rejected() -> None:
    row = _coverage_row()
    with pytest.raises(ValueError, match="duplicated"):
        validate_coverage_rows([row, dict(row)])


def test_candidate_and_future_peer_values_are_rejected() -> None:
    assert candidate_excluded_peer_mean(
        candidate="A",
        peer_values={"B": 1.0, "C": 3.0},
        decision_epoch=100,
        peer_epochs={"B": 99, "C": 100},
    ) == 2.0
    with pytest.raises(ValueError, match="candidate"):
        candidate_excluded_peer_mean(
            candidate="A",
            peer_values={"A": 1.0},
            decision_epoch=100,
            peer_epochs={"A": 99},
        )
    with pytest.raises(ValueError, match="future"):
        candidate_excluded_peer_mean(
            candidate="A",
            peer_values={"B": 1.0},
            decision_epoch=100,
            peer_epochs={"B": 101},
        )


def test_real_inventory_shape_stops_fail_closed_without_opening_outcomes(tmp_path: Path) -> None:
    _evidence(tmp_path)

    report = run_data_feasibility_audit(root=tmp_path, output=tmp_path / "output")

    assert report["status"] == "checkpoint_1_stopped_data_infeasible"
    assert report["cohorts"]["a"]["symbol_count"] == 50
    assert report["cohorts"]["b"]["symbol_count"] == 0
    assert report["checkpoint_passed"] is False
    assert report["checkpoint_2_authorized"] is False
    assert report["new_outcomes_opened"] is False
    assert report["model_trained"] is False
    assert report["baseline_improved"] is False
    assert not any(report["gates"].values())
    run = tmp_path / report["artifact_path"]
    assert run.is_file()
    ledger = run.parent / "acquisition_request_ledger.jsonl"
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 4
    assert not any(record["attempted"] for record in records)
    assert sum(record["requests_made"] for record in records) == 0


def test_incomplete_cohort_a_evidence_is_rejected(tmp_path: Path) -> None:
    _evidence(tmp_path, symbols=49)

    with pytest.raises(ValueError, match="exactly 50"):
        run_data_feasibility_audit(root=tmp_path, output=tmp_path / "output")


def test_tracked_zero_request_ledger_is_valid_and_mutation_fails(tmp_path: Path) -> None:
    tracked = (
        Path(__file__).resolve().parents[1]
        / "docs/evidence/accuracy-data-acquisition-ledger.jsonl"
    )
    records = load_acquisition_request_ledger(tracked, maximum_requests=0)
    assert len(records) == 4
    assert sum(record["requests_made"] for record in records) == 0
    mutated = tmp_path / "ledger.jsonl"
    mutated.write_text(tracked.read_text(encoding="utf-8").replace("not_requested", "requested", 1))
    with pytest.raises(ValueError, match="record hash"):
        load_acquisition_request_ledger(mutated, maximum_requests=0)
