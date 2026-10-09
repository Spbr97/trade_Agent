from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradedesk.learning_dataset import LearningDataset
from tradedesk.performance_comparison import build_performance_comparison


def _row(
    index: int,
    *,
    label: int,
    recommended: bool,
    model_version: str = "model-v1",
) -> dict[str, object]:
    armed_on = date(2026, 10, 1) + timedelta(days=index)
    return {
        "signal_id": f"signal-{index}",
        "prediction_sha256": f"prediction-{index}",
        "market": "nse",
        "symbol": f"S{index}",
        "setup": "trend_pullback",
        "contract_kind": "quick_profit",
        "contract_version": "quick-profit-v1",
        "contract_sha256": "a" * 64,
        "strategy_version": "strategy-v1",
        "feature_version": "features-v1",
        "model_version": model_version,
        "model_kind": "logistic",
        "evidence_class": "qualified_call" if recommended else "rejected_call",
        "evidence_role": "recommended" if recommended else "counterfactual",
        "armed_on": armed_on.isoformat(),
        "label": label,
        "net_r": 0.8 if label else -1.0,
        "after_tax_r": 0.6 if label else -1.0,
        "features": {"decision.probability": 0.8 if label else 0.2},
    }


def _dataset(rows: list[dict[str, object]]) -> LearningDataset:
    return LearningDataset(
        dataset_id="dataset-v1",
        version="self-learning-dataset-v4",
        market="nse",
        purpose="prospective",
        rows=tuple(rows),
        exclusions={"pending": 3, "invalid": 2},
        source_records=len(rows) + 5,
    )


def test_filter_comparison_uses_the_same_frozen_version_cohort() -> None:
    dataset = _dataset(
        [
            _row(0, label=0, recommended=False),
            _row(1, label=1, recommended=False),
            _row(2, label=1, recommended=True),
            _row(3, label=1, recommended=True),
        ]
    )

    report = build_performance_comparison(dataset, {"latest": None})

    assert report["status"] == "available_single_frozen_cohort"
    assert report["contracts_pooled"] is False
    assert report["versions_pooled"] is False
    assert report["invalid_pending_never_triggered_included"] is False
    comparison = report["filter_comparisons"][0]
    assert comparison["before_filter"]["strict_accuracy"] == pytest.approx(0.75)
    assert comparison["after_filter"]["strict_accuracy"] == pytest.approx(1.0)
    assert comparison["retained_fraction"] == pytest.approx(0.5)
    assert comparison["deltas_after_minus_before"]["strict_accuracy"] == pytest.approx(0.25)
    assert comparison["descriptive_only"] is True
    assert report["latest_frozen_challenger"]["improvement_proven"] is False


def test_versions_are_reported_separately_and_never_pooled() -> None:
    dataset = _dataset(
        [
            _row(0, label=1, recommended=True, model_version="model-v1"),
            _row(1, label=0, recommended=True, model_version="model-v2"),
        ]
    )

    report = build_performance_comparison(dataset, {})

    assert report["status"] == "available_by_frozen_cohort"
    assert len(report["filter_comparisons"]) == 2
    assert {row["model_version"] for row in report["version_comparisons"]} == {
        "model-v1",
        "model-v2",
    }
    assert all(row["before_filter"]["n"] == 1 for row in report["version_comparisons"])


def test_after_filter_unavailable_is_not_treated_as_a_zero_or_pass() -> None:
    dataset = _dataset([_row(0, label=1, recommended=False)])

    report = build_performance_comparison(dataset, {})
    comparison = report["filter_comparisons"][0]

    assert report["status"] == "filter_comparison_not_available"
    assert comparison["status"] == "after_filter_not_available"
    assert comparison["after_filter"]["strict_accuracy"] is None
    assert comparison["deltas_after_minus_before"]["strict_accuracy"] is None


def test_locked_challenger_and_primary_selector_are_bound_to_exact_artifact() -> None:
    dataset = _dataset([_row(0, label=1, recommended=True)])
    summary = {
        "latest": {
            "market": "nse",
            "experiment_id": "experiment-v1",
            "dataset_id": "locked-dataset-v1",
            "cohorts": {"contract_versions": ["quick-profit-v1"]},
            "scores": {
                "chronological_test": {
                    "frozen_baseline": {
                        "n": 20,
                        "wins": 8,
                        "accuracy": 0.55,
                        "strict_outcome_accuracy": 0.4,
                        "strict_outcome_wilson_95_low": 0.22,
                        "brier": 0.30,
                        "selected": 10,
                        "selected_mean_net_r": -0.1,
                    },
                    "challenger": {
                        "n": 20,
                        "wins": 8,
                        "accuracy": 0.65,
                        "strict_outcome_accuracy": 0.4,
                        "strict_outcome_wilson_95_low": 0.22,
                        "brier": 0.24,
                        "selected": 5,
                        "selected_mean_net_r": 0.12,
                    },
                }
            },
            "precision_selector": {
                "version": "precision-selector-v2",
                "primary_policy": "top_1_per_session",
                "operating_points": {
                    "top_1_per_session": {
                        "selected": 4,
                        "strict_accuracy": 0.75,
                        "wilson_95_low": 0.30,
                        "mean_net_r": 0.15,
                        "passes_final_accuracy_gate": False,
                    }
                },
            },
            "final_evidence_gates": {"all_passed": False},
        }
    }

    frozen = build_performance_comparison(dataset, summary)["latest_frozen_challenger"]

    assert frozen["status"] == "available"
    assert frozen["experiment_id"] == "experiment-v1"
    assert frozen["frozen_baseline"]["accuracy"] == pytest.approx(0.55)
    assert frozen["challenger"]["brier"] == pytest.approx(0.24)
    assert frozen["locked_model_deltas"]["classification_accuracy"] == pytest.approx(0.10)
    assert frozen["locked_model_deltas"]["brier_improvement"] == pytest.approx(0.06)
    assert frozen["primary_selector"]["strict_accuracy"] == pytest.approx(0.75)
    assert frozen["improvement_proven"] is False
    assert frozen["active_model_changed"] is False
    assert frozen["promotion_authorized"] is False


def test_challenger_market_mismatch_fails_closed() -> None:
    dataset = _dataset([_row(0, label=1, recommended=True)])

    frozen = build_performance_comparison(dataset, {"latest": {"market": "crypto"}})[
        "latest_frozen_challenger"
    ]

    assert frozen["status"] == "invalid_market_binding"
    assert frozen["improvement_proven"] is False
