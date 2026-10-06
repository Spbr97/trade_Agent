from __future__ import annotations

import json

from tradedesk.challenger_workflow import (
    drift_snapshot,
    performance_snapshot,
    run_challenger_experiment,
)
from tradedesk.learning_dataset import LearningDataset


def _dataset(n: int = 24, *, qualified: bool = True) -> LearningDataset:
    rows = []
    for i in range(n):
        label = i % 2
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
                "strategy_version": "strategy-v1",
                "feature_version": "features-v1",
                "model_version": "active-v1",
                "model_kind": "logistic",
                "evidence_class": "qualified_call" if qualified else "rejected_call",
                "evidence_role": "recommended" if qualified else "counterfactual",
                "cohort": "prospective",
                "armed_on": f"2026-09-{1 + i // 3:02d}",
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
    assert report["split"]["development_rows"] + report["split"]["test_rows"] == 24
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
