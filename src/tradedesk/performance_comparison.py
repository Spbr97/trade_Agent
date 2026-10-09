"""Read-only, fail-closed performance comparisons for the self-learning dashboard.

The report deliberately separates two questions:

* what happened to the same sealed mature cohort before and after the existing
  prediction-time qualification filter; and
* how the latest frozen challenger scored against its frozen baseline on its locked
  chronological test.

Neither comparison changes a model or grants trading authority.  Markets, outcome
contracts and version identities are never pooled.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from tradedesk.learning_dataset import LearningDataset
from tradedesk.prediction_ledger import canonical_sha256

PERFORMANCE_COMPARISON_VERSION = "self-learning-performance-comparison-v1"

_COHORT_FIELDS = (
    "contract_version",
    "contract_sha256",
    "strategy_version",
    "feature_version",
    "model_version",
    "model_kind",
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _wilson_lower(wins: int, n: int, z: float = 1.959963984540054) -> float | None:
    if n <= 0:
        return None
    p = wins / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, centre - margin)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _score(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    labels = [int(row["label"]) for row in rows if row.get("label") in {0, 1}]
    wins = sum(labels)
    net_r = [value for row in rows if (value := _finite(row.get("net_r"))) is not None]
    after_tax_r = [value for row in rows if (value := _finite(row.get("after_tax_r"))) is not None]
    probabilities = []
    for row in rows:
        probability = _finite((row.get("features") or {}).get("decision.probability"))
        if probability is not None and 0 <= probability <= 1 and row.get("label") in {0, 1}:
            probabilities.append((probability, int(row["label"])))
    return {
        "status": "available" if labels else "not_available",
        "n": len(labels),
        "wins": wins,
        "strict_accuracy": wins / len(labels) if labels else None,
        "wilson_95_low": _wilson_lower(wins, len(labels)),
        "mean_net_r": _mean(net_r),
        "net_r_n": len(net_r),
        "mean_after_tax_r": _mean(after_tax_r),
        "after_tax_r_n": len(after_tax_r),
        "brier": (
            _mean([(probability - label) ** 2 for probability, label in probabilities])
            if probabilities
            else None
        ),
        "calibration_n": len(probabilities),
        "sessions": len({str(row.get("armed_on")) for row in rows}),
    }


def _delta(after: Any, before: Any) -> float | None:
    after_number, before_number = _finite(after), _finite(before)
    if after_number is None or before_number is None:
        return None
    return after_number - before_number


def _cohort_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "unavailable") for field in _COHORT_FIELDS)


def _filter_comparisons(dataset: LearningDataset) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for row in dataset.rows:
        grouped.setdefault(_cohort_identity(row), []).append(row)
    comparisons = []
    for identity, rows in sorted(grouped.items()):
        version_identity = dict(zip(_COHORT_FIELDS, identity, strict=True))
        after_rows = [row for row in rows if row.get("evidence_role") == "recommended"]
        counterfactual_rows = [row for row in rows if row.get("evidence_role") == "counterfactual"]
        before, after = _score(rows), _score(after_rows)
        cohort_scope = {"market": dataset.market, **version_identity}
        comparisons.append(
            {
                "cohort_id": canonical_sha256(cohort_scope),
                **version_identity,
                "status": (
                    "available"
                    if before["status"] == "available" and after["status"] == "available"
                    else "after_filter_not_available"
                    if before["status"] == "available"
                    else "not_available"
                ),
                "before_filter": before,
                "after_filter": after,
                "counterfactual": _score(counterfactual_rows),
                "retained_fraction": len(after_rows) / len(rows) if rows else None,
                "deltas_after_minus_before": {
                    "strict_accuracy": _delta(
                        after.get("strict_accuracy"), before.get("strict_accuracy")
                    ),
                    "mean_net_r": _delta(after.get("mean_net_r"), before.get("mean_net_r")),
                    "mean_after_tax_r": _delta(
                        after.get("mean_after_tax_r"), before.get("mean_after_tax_r")
                    ),
                },
                "evidence_classes": dict(
                    sorted(Counter(str(row.get("evidence_class")) for row in rows).items())
                ),
                "same_market_contract_and_versions": True,
                "descriptive_only": True,
            }
        )
    return comparisons


def _model_view(score: Any) -> dict[str, Any] | None:
    if not isinstance(score, Mapping):
        return None
    return {
        key: score.get(key)
        for key in (
            "n",
            "wins",
            "accuracy",
            "strict_outcome_accuracy",
            "strict_outcome_wilson_95_low",
            "brier",
            "selected",
            "selected_mean_net_r",
        )
    }


def _latest_challenger(dataset: LearningDataset, summary: Mapping[str, Any]) -> dict[str, Any]:
    latest = summary.get("latest")
    if not isinstance(latest, Mapping):
        return {
            "status": "not_available",
            "reason": "no frozen challenger has completed for this market",
            "improvement_proven": False,
            "active_model_changed": False,
            "promotion_authorized": False,
        }
    if latest.get("market") != dataset.market:
        return {
            "status": "invalid_market_binding",
            "reason": "latest challenger market does not match this dashboard market",
            "improvement_proven": False,
            "active_model_changed": False,
            "promotion_authorized": False,
        }
    scores = latest.get("scores") or {}
    locked = scores.get("chronological_test") if isinstance(scores, Mapping) else None
    baseline = _model_view(locked.get("frozen_baseline") if isinstance(locked, Mapping) else None)
    challenger = _model_view(locked.get("challenger") if isinstance(locked, Mapping) else None)
    selector = latest.get("precision_selector") or {}
    primary_policy = selector.get("primary_policy") if isinstance(selector, Mapping) else None
    operating_points = selector.get("operating_points") if isinstance(selector, Mapping) else None
    primary = (
        operating_points.get(primary_policy)
        if isinstance(operating_points, Mapping) and isinstance(primary_policy, str)
        else None
    )
    final_gates = latest.get("final_evidence_gates") or {}
    cohorts = latest.get("cohorts") or {}
    complete = baseline is not None and challenger is not None and isinstance(primary, Mapping)
    required_gates = (
        "sample_threshold_reached",
        "primary_selector_policy_clears_accuracy_and_net_gate",
        "matched_random_timing_margin_passed",
        "prospective_cohort_passed",
        "all_passed",
    )
    gates_passed = bool(
        isinstance(final_gates, Mapping)
        and all(final_gates.get(name) is True for name in required_gates)
        and isinstance(primary, Mapping)
        and primary.get("passes_final_accuracy_gate") is True
    )
    return {
        "status": "available" if complete else "incomplete_artifact_not_a_pass",
        "experiment_id": latest.get("experiment_id"),
        "dataset_id": latest.get("dataset_id"),
        "contract_versions": (
            cohorts.get("contract_versions") if isinstance(cohorts, Mapping) else None
        ),
        "selector_version": selector.get("version") if isinstance(selector, Mapping) else None,
        "primary_policy": primary_policy,
        "frozen_baseline": baseline,
        "challenger": challenger,
        "locked_model_deltas": {
            "classification_accuracy": _delta(
                challenger.get("accuracy") if challenger else None,
                baseline.get("accuracy") if baseline else None,
            ),
            "brier_improvement": _delta(
                baseline.get("brier") if baseline else None,
                challenger.get("brier") if challenger else None,
            ),
        },
        "primary_selector": dict(primary) if isinstance(primary, Mapping) else None,
        "final_evidence_gates": dict(final_gates) if isinstance(final_gates, Mapping) else {},
        "improvement_proven": bool(complete and gates_passed),
        "exact_locked_chronological_test": True,
        "active_model_changed": False,
        "promotion_authorized": False,
    }


def build_performance_comparison(
    dataset: LearningDataset, challenger_summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Build one market-specific report without treating missing evidence as success."""

    comparisons = _filter_comparisons(dataset)
    available = [row for row in comparisons if row["status"] == "available"]
    return {
        "version": PERFORMANCE_COMPARISON_VERSION,
        "market": dataset.market,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "source_records": dataset.source_records,
        "eligible_mature": len(dataset.rows),
        "status": (
            "not_available"
            if not comparisons
            else "available_single_frozen_cohort"
            if len(comparisons) == 1 and len(available) == 1
            else "available_by_frozen_cohort"
            if available
            else "filter_comparison_not_available"
        ),
        "filter_definition": (
            "before = every sealed mature prospective row in the exact frozen cohort; "
            "after = rows qualified at prediction time in that same cohort"
        ),
        "filter_comparisons": comparisons,
        "version_comparisons": [
            {
                key: row[key]
                for key in (
                    "cohort_id",
                    *_COHORT_FIELDS,
                    "before_filter",
                    "after_filter",
                    "retained_fraction",
                    "evidence_classes",
                    "descriptive_only",
                )
            }
            for row in comparisons
        ],
        "latest_frozen_challenger": _latest_challenger(dataset, challenger_summary),
        "markets_pooled": False,
        "contracts_pooled": False,
        "versions_pooled": False,
        "invalid_pending_never_triggered_included": False,
        "descriptive_filter_gain_is_not_promotion_evidence": True,
        "active_model_changed": False,
        "promotion_authorized": False,
    }
