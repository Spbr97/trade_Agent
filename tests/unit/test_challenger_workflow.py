from __future__ import annotations

import json
from datetime import date, timedelta

from tradedesk.challenger_workflow import (
    drift_snapshot,
    performance_snapshot,
    run_challenger_experiment,
)
from tradedesk.learning_dataset import LearningDataset
from tradedesk.random_timing_control import CONTROL_VERSION


def _dataset(n: int = 24, *, qualified: bool = True) -> LearningDataset:
    rows = []
    for i in range(n):
        label = i % 2
        armed_on = date(2026, 9, 1) + timedelta(days=i // 3)
        rows.append(
            {
                "signal_id": f"signal-{i:03d}",
                "prediction_sha256": f"hash-{i}",
                "market": "nse",
                "symbol": f"S{i}",
                "setup": "base_breakout",
                "sector": "test",
                "regime": "risk_on",
                "contract_kind": "quick_profit",
                "contract_version": "quick-profit-v1",
                "contract_sha256": "a" * 64,
                "strategy_version": "strategy-v1",
                "feature_version": "features-v1",
                "model_version": "active-v1",
                "model_kind": "logistic",
                "evidence_class": "qualified_call" if qualified else "rejected_call",
                "evidence_role": "recommended" if qualified else "counterfactual",
                "cohort": "prospective",
                "armed_on": armed_on.isoformat(),
                "entry_on": (armed_on + timedelta(days=1)).isoformat(),
                "exit_on": (armed_on + timedelta(days=1)).isoformat(),
                "time_to_resolution_sessions": 1,
                "features": {
                    "decision.rule_score": 90.0 if label else 10.0,
                    "decision.probability": 0.5,
                    "context.atr_pct": 0.02 + i / 10_000,
                    "execution.net_rr_t1": 0.7,
                },
                "label": label,
                "outcome": "target" if label else "stop",
                "gross_r": 0.75 if label else -1.0,
                "net_r": 0.65 if label else -1.1,
                "after_tax_r": None,
                "holding_sessions": 1,
                "failure_attributions": [],
            }
        )
    return LearningDataset(
        dataset_id=f"dataset-{n}-{qualified}",
        version="self-learning-dataset-v2",
        market="nse",
        purpose="prospective",
        rows=tuple(rows),
        exclusions={},
        source_records=n,
    )


def test_performance_headline_does_not_pool_counterfactuals_or_contracts() -> None:
    recommended = list(_dataset(2).rows)
    counterfactual = list(_dataset(1, qualified=False).rows)
    dataset = LearningDataset(
        dataset_id="mixed",
        version="v2",
        market="nse",
        purpose="prospective",
        rows=tuple(recommended + counterfactual),
        exclusions={"invalid": 7, "pending": 3, "never_triggered": 2},
        source_records=15,
    )
    report = performance_snapshot(dataset)
    assert report["qualified"]["n"] == 2
    assert report["counterfactual"]["n"] == 1
    assert report["contracts_pooled"] is False
    assert report["invalid_pending_never_triggered_included"] is False


def test_drift_monitor_detects_accuracy_and_confidence_decline() -> None:
    rows = []
    for i, row in enumerate(_dataset(40).rows):
        changed = dict(row)
        changed["label"] = 1 if i < 20 else 0
        changed["features"] = {**row["features"], "decision.probability": 0.8}
        rows.append(changed)
    dataset = LearningDataset(
        dataset_id="drift",
        version="v2",
        market="nse",
        purpose="prospective",
        rows=tuple(rows),
        exclusions={},
        source_records=40,
    )
    report = drift_snapshot(dataset)
    assert report["status"] == "drift_detected"
    assert "strict_accuracy_decline" in report["alerts"]
    assert "confidence_calibration_decline" in report["alerts"]


def test_versioned_challenger_is_chronological_idempotent_and_cannot_promote(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dataset = _dataset()
    report = run_challenger_experiment(dataset, tmp_path)
    assert report["status"].startswith("completed_")
    assert (
        report["split"]["development_rows"]
        + report["split"]["test_rows"]
        + report["purge"]["overlap_rows"]
        + report["purge"]["unverifiable_exit_rows"]
        == 24
    )
    assert report["purge"]["overlap_rows"] == 3
    assert report["purge"]["unverifiable_exit_rows"] == 0
    assert report["evidence_ladder"]["stage"] == "collecting_below_diagnostic_floor"
    assert "month" in report["consistency"]
    assert "liquidity" in report["consistency"]
    assert report["scores"]["chronological_test"]["challenger"]["calibration"]
    assert report["final_evidence_gates"]["all_passed"] is False
    assert report["active_model_changed"] is False
    assert report["promotion_authorized"] is False
    assert (tmp_path / "datasets" / f"{dataset.dataset_id}.json").exists()
    assert (tmp_path / "challengers" / f"{report['experiment_id']}.json").exists()
    assert len((tmp_path / "experiments.jsonl").read_text().splitlines()) == 1

    repeated = run_challenger_experiment(dataset, tmp_path)
    assert repeated["idempotent_replay"] is True
    assert len((tmp_path / "experiments.jsonl").read_text().splitlines()) == 1


def test_challenger_refuses_thin_evidence_without_registering_an_experiment(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = run_challenger_experiment(_dataset(10), tmp_path)
    assert report["status"] == "blocked_insufficient_or_mixed_evidence"
    assert report["conclusion"] == "not_run"
    assert not (tmp_path / "experiments.jsonl").exists()


def test_future_challenger_cannot_reuse_a_prior_locked_outcome(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = run_challenger_experiment(_dataset(24), tmp_path)
    second = run_challenger_experiment(_dataset(30), tmp_path)
    assert first["status"].startswith("completed_")
    assert second["status"].startswith("completed_")
    assert second["prior_locked_rows_excluded"] == first["split"]["test_rows"]
    uses = [
        json.loads(line)
        for line in (tmp_path / "dataset_uses.jsonl").read_text().splitlines()
    ]
    locked = [set(use["signal_ids"]) for use in uses if use["purpose"] == "locked_test"]
    assert len(locked) == 2
    assert locked[0].isdisjoint(locked[1])


def test_timing_control_registers_then_selects_from_future_ex_ante_population(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    first_dataset = _dataset()
    registration_population = [dict(row) for row in first_dataset.rows]
    observed: dict[str, list[object]] = {
        "candidate_ids": [],
        "selected_ids": [],
        "historical_ids": [],
        "selection_contexts": [],
        "selection_manifests": [],
    }

    def select(rows, probabilities, policy):  # type: ignore[no-untyped-def]
        candidate_ids = {str(row["signal_id"]) for row in rows}
        observed["candidate_ids"].append(candidate_ids)
        assert set(probabilities) == candidate_ids
        if not candidate_ids:
            return []
        assert candidate_ids == {"future-invalid", "future-pending"}
        return ["future-pending"]

    def replay(rows, **kwargs):  # type: ignore[no-untyped-def]
        observed["selected_ids"].append([str(row["signal_id"]) for row in rows])
        observed["historical_ids"].append(
            {str(row["signal_id"]) for row in kwargs["historical_opportunities"]}
        )
        observed["selection_contexts"].append(dict(kwargs["selection_context"]))
        observed["selection_manifests"].append(dict(kwargs["selection_manifest"]))
        return {
            "version": CONTROL_VERSION,
            "status": "collecting_insufficient_evidence",
            "market": kwargs["market"],
            "contract_version": kwargs["contract_version"],
            "selector_policy": "top_1_per_session",
            "selection_context_sha256": kwargs["selection_context"]["record_sha256"],
            "records": [],
            "random_timing_gate_passed": False,
        }

    monkeypatch.setattr("tradedesk.challenger_workflow.select_primary_signal_ids", select)
    monkeypatch.setattr("tradedesk.challenger_workflow.evaluate_matched_random_timing", replay)
    first = run_challenger_experiment(
        first_dataset,
        tmp_path,
        timing_opportunities=registration_population,
    )
    contract_path = tmp_path / "timing_selection_contract.json"
    contract_text = contract_path.read_text(encoding="utf-8")
    contract = json.loads(contract_text)

    assert first["status"].startswith("completed_")
    assert first["timing_selection"]["candidate_rows"] == 0
    assert first["timing_selection"]["selected_rows"] == 0
    assert contract["selection_authority"] == "future_sessions_only"
    assert contract["registered_from_dataset_id"] == first_dataset.dataset_id
    assert contract["starts_after"] == max(
        str(row["armed_on"]) for row in registration_population
    )

    future_pending = dict(registration_population[-1])
    future_pending.update(
        signal_id="future-pending",
        prediction_sha256="future-pending-seal",
        armed_on="2026-10-01",
        outcome_state="pending_call",
        outcome=None,
        label=None,
        net_r=None,
        entry_on=None,
        exit_on=None,
    )
    future_invalid = dict(future_pending)
    future_invalid.update(
        signal_id="future-invalid",
        prediction_sha256="future-invalid-seal",
        outcome_state="invalid_call",
        outcome="unavailable",
    )
    later_population = registration_population + [future_pending, future_invalid]
    second = run_challenger_experiment(
        _dataset(30),
        tmp_path,
        timing_opportunities=later_population,
    )

    assert second["status"].startswith("completed_")
    assert second["timing_selection"]["candidate_rows"] == 2
    assert second["timing_selection"]["selected_rows"] == 1
    assert second["timing_selection"]["pending_rows_included_before_ranking"] is True
    assert contract_path.read_text(encoding="utf-8") == contract_text
    assert observed["candidate_ids"] == [set(), {"future-invalid", "future-pending"}]
    assert observed["selected_ids"] == [[], ["future-pending"]]
    assert observed["historical_ids"][-1] == {
        *(str(row["signal_id"]) for row in registration_population),
        "future-invalid",
        "future-pending",
    }
    assert observed["selection_contexts"][-1] == contract
    manifest = observed["selection_manifests"][-1]
    assert manifest["selection_context_sha256"] == contract["record_sha256"]
    assert manifest["selected_signal_ids"] == ["future-pending"]
