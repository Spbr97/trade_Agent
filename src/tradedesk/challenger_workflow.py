"""Ledger-only performance monitoring and versioned challenger experiments.

This module never changes an active model, configuration, eligibility gate, or trading
authority.  It consumes only deterministic ``LearningDataset`` rows created from sealed
predictions with mature valid outcomes.  NSE, BSE and crypto are always run separately.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from tradedesk.broker.indstocks.models import IST
from tradedesk.learning_dataset import LearningDataset, register_dataset_use
from tradedesk.prediction_ledger import canonical_sha256

WORKFLOW_VERSION = "sealed-challenger-v1"
MINIMUM_ROWS = 20
MINIMUM_SESSIONS = 4
TEST_SESSION_FRACTION = 0.25
MINIMUM_BRIER_GAIN = 0.005
DRIFT_WINDOW = 20

# Fixed before any ledger evidence is inspected. Dynamic score-component and geometry keys
# are deliberately excluded: allowing the available sample to choose arbitrary columns is
# feature search, not a preregistered challenger.
PREREGISTERED_FEATURES = (
    "decision.rule_score",
    "decision.probability",
    "context.relative_strength_percentile",
    "context.sector_percentile",
    "context.atr_pct",
    "context.average_turnover",
    "execution.risk_pct",
    "execution.net_rr_t1",
    "execution.net_rr_t2",
    "source_snapshot.source_feature_row.atr",
    "source_snapshot.source_feature_row.close",
    "source_snapshot.source_feature_row.volume",
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _wilson(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = wins / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [number for value in values if (number := _finite(value)) is not None]
    return float(np.mean(numbers)) if numbers else None


def _score_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    wins = sum(int(row["label"]) for row in rows)
    probabilities: list[tuple[float, int]] = []
    for row in rows:
        probability = _finite((row.get("features") or {}).get("decision.probability"))
        if probability is not None and 0 <= probability <= 1:
            probabilities.append((probability, int(row["label"])))
    calibration = []
    for lo, hi in ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.000001)):
        bucket = [(p, label) for p, label in probabilities if lo <= p < hi]
        if bucket:
            calibration.append(
                {
                    "lo": lo,
                    "hi": min(1.0, hi),
                    "n": len(bucket),
                    "mean_confidence": float(np.mean([p for p, _ in bucket])),
                    "realised_accuracy": float(np.mean([label for _, label in bucket])),
                }
            )
    interval = _wilson(wins, n)
    return {
        "n": n,
        "wins": wins,
        "strict_accuracy": wins / n if n else None,
        "wilson_95_low": interval[0] if interval else None,
        "wilson_95_high": interval[1] if interval else None,
        "brier": (
            float(np.mean([(p - label) ** 2 for p, label in probabilities]))
            if probabilities
            else None
        ),
        "calibration_n": len(probabilities),
        "calibration": calibration,
        "mean_gross_r": _mean(row.get("gross_r") for row in rows),
        "mean_net_r": _mean(row.get("net_r") for row in rows),
        "mean_after_tax_r": _mean(row.get("after_tax_r") for row in rows),
        "sessions": len({str(row.get("armed_on")) for row in rows}),
    }


def performance_snapshot(dataset: LearningDataset) -> dict[str, Any]:
    """Headline performance is qualified calls only; counterfactuals stay visible."""

    qualified = [row for row in dataset.rows if row["evidence_role"] == "recommended"]
    counterfactual = [row for row in dataset.rows if row["evidence_role"] == "counterfactual"]
    contracts = {
        str(contract): _score_rows([r for r in qualified if r["contract_kind"] == contract])
        for contract in sorted({str(r["contract_kind"]) for r in qualified})
    }
    return {
        "version": "sealed-performance-v1",
        "status": "available" if qualified else "not_available",
        "market": dataset.market,
        "dataset_id": dataset.dataset_id,
        "qualified": _score_rows(qualified),
        "counterfactual": _score_rows(counterfactual),
        "contracts": contracts,
        "contracts_pooled": False,
        "invalid_pending_never_triggered_included": False,
    }


def drift_snapshot(dataset: LearningDataset, *, window: int = DRIFT_WINDOW) -> dict[str, Any]:
    """Compare adjacent chronological qualified-call windows without declaring causality."""

    rows = sorted(
        (row for row in dataset.rows if row["evidence_role"] == "recommended"),
        key=lambda row: (str(row.get("armed_on")), str(row.get("signal_id"))),
    )
    required = window * 2
    if len(rows) < required:
        return {
            "version": "sealed-drift-v1",
            "status": "insufficient_evidence",
            "required": required,
            "observed": len(rows),
            "remaining": required - len(rows),
            "performance_drift": None,
            "confidence_drift": None,
            "data_drift": None,
        }
    previous, recent = rows[-required:-window], rows[-window:]
    old_score, new_score = _score_rows(previous), _score_rows(recent)
    accuracy_delta = float(new_score["strict_accuracy"] - old_score["strict_accuracy"])
    brier_delta = (
        float(new_score["brier"] - old_score["brier"])
        if old_score["brier"] is not None and new_score["brier"] is not None
        else None
    )
    expectancy_delta = (
        float(new_score["mean_net_r"] - old_score["mean_net_r"])
        if old_score["mean_net_r"] is not None and new_score["mean_net_r"] is not None
        else None
    )
    feature_shifts: dict[str, float] = {}
    for name in PREREGISTERED_FEATURES:
        old = [_finite((row.get("features") or {}).get(name)) for row in previous]
        new = [_finite((row.get("features") or {}).get(name)) for row in recent]
        old_values = np.asarray([value for value in old if value is not None], dtype=float)
        new_values = np.asarray([value for value in new if value is not None], dtype=float)
        if len(old_values) < 5 or len(new_values) < 5:
            continue
        scale = float(np.std(old_values))
        if scale > 1e-12:
            feature_shifts[name] = float(abs(np.mean(new_values) - np.mean(old_values)) / scale)
    max_shift = max(feature_shifts.values(), default=None)
    alerts = []
    if accuracy_delta <= -0.10:
        alerts.append("strict_accuracy_decline")
    if brier_delta is not None and brier_delta >= 0.05:
        alerts.append("confidence_calibration_decline")
    if expectancy_delta is not None and expectancy_delta <= -0.20:
        alerts.append("net_expectancy_decline")
    if max_shift is not None and max_shift >= 1.0:
        alerts.append("prediction_feature_shift")
    return {
        "version": "sealed-drift-v1",
        "status": "drift_detected" if alerts else "within_monitoring_limits",
        "required": required,
        "observed": len(rows),
        "remaining": 0,
        "previous": old_score,
        "recent": new_score,
        "performance_drift": {
            "strict_accuracy_delta": accuracy_delta,
            "mean_net_r_delta": expectancy_delta,
        },
        "confidence_drift": {"brier_delta": brier_delta},
        "data_drift": {"max_standardised_mean_shift": max_shift, "features": feature_shifts},
        "alerts": alerts,
    }


def _matrix(
    rows: list[Mapping[str, Any]], features: list[str], medians: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.asarray(
        [
            [_finite((row.get("features") or {}).get(name)) for name in features]
            for row in rows
        ],
        dtype=float,
    )
    matrix[~np.isfinite(matrix)] = np.nan
    if medians is None:
        medians = np.nanmedian(matrix, axis=0)
        medians[~np.isfinite(medians)] = 0.0
    missing = np.where(np.isnan(matrix))
    matrix[missing] = medians[missing[1]]
    return matrix, medians


def _model_score(probabilities: np.ndarray, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    labels = np.asarray([int(row["label"]) for row in rows], dtype=int)
    predicted = probabilities >= 0.5
    selected_net = [
        _finite(row.get("net_r")) for row, selected in zip(rows, predicted, strict=True) if selected
    ]
    return {
        "n": len(rows),
        "accuracy": float(np.mean(predicted == labels)),
        "brier": float(np.mean((probabilities - labels) ** 2)),
        "selected": int(np.sum(predicted)),
        "selected_mean_net_r": _mean(selected_net),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _existing_experiment(registry: Path, experiment_id: str) -> dict[str, Any] | None:
    if not registry.exists():
        return None
    for line in registry.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("experiment_id") == experiment_id:
                return record
    return None


def _subset_dataset(
    dataset: LearningDataset, rows: list[Mapping[str, Any]], purpose: str
) -> LearningDataset:
    identity = {
        "version": dataset.version,
        "market": dataset.market,
        "purpose": purpose,
        "source_dataset_id": dataset.dataset_id,
        "signal_ids": [str(row["signal_id"]) for row in rows],
    }
    return LearningDataset(
        dataset_id=canonical_sha256(identity),
        version=dataset.version,
        market=dataset.market,
        purpose=purpose,  # type: ignore[arg-type]
        rows=tuple(dict(row) for row in rows),
        exclusions=dict(dataset.exclusions),
        source_records=dataset.source_records,
    )


def _group_scores(rows: list[Mapping[str, Any]], probabilities: np.ndarray) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in ("regime", "sector", "armed_on"):
        grouped: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            grouped.setdefault(str(row.get(field) or "unavailable"), []).append(index)
        out[field] = {
            name: _model_score(probabilities[indexes], [rows[index] for index in indexes])
            for name, indexes in sorted(grouped.items())
        }
    return out


def _random_selection_control(
    rows: list[Mapping[str, Any]], probabilities: np.ndarray, *, repeats: int = 1000
) -> dict[str, Any]:
    labels = np.asarray([int(row["label"]) for row in rows], dtype=int)
    challenger_brier = float(np.mean((probabilities - labels) ** 2))
    selected = int(np.sum(probabilities >= 0.5))
    net = np.asarray(
        [value if (value := _finite(row.get("net_r"))) is not None else np.nan for row in rows]
    )
    rng = np.random.default_rng(1701)
    random_brier = []
    random_selected_net = []
    for _ in range(repeats):
        permuted = rng.permutation(probabilities)
        random_brier.append(float(np.mean((permuted - labels) ** 2)))
        if selected and selected <= len(rows):
            indexes = rng.choice(len(rows), size=selected, replace=False)
            values = net[indexes]
            values = values[np.isfinite(values)]
            if len(values):
                random_selected_net.append(float(np.mean(values)))
    return {
        "kind": "matched_random_selection",
        "repeats": repeats,
        "challenger_brier": challenger_brier,
        "random_mean_brier": float(np.mean(random_brier)),
        "fraction_random_brier_at_least_as_good": float(
            np.mean(np.asarray(random_brier) <= challenger_brier)
        ),
        "selected_calls": selected,
        "random_selected_mean_net_r": (
            float(np.mean(random_selected_net)) if random_selected_net else None
        ),
        "random_timing_control": "unavailable_from_call_ledger",
    }


def run_challenger_experiment(
    dataset: LearningDataset,
    output_root: Path,
    *,
    minimum_rows: int = MINIMUM_ROWS,
    minimum_sessions: int = MINIMUM_SESSIONS,
) -> dict[str, Any]:
    """Fit one deterministic transparent challenger and persist every result."""

    if dataset.market not in {"nse", "bse", "crypto"}:
        raise ValueError("challenger market must be nse, bse, or crypto")
    configuration = {
        "workflow_version": WORKFLOW_VERSION,
        "features": PREREGISTERED_FEATURES,
        "minimum_rows": minimum_rows,
        "minimum_sessions": minimum_sessions,
        "test_session_fraction": TEST_SESSION_FRACTION,
        "minimum_brier_gain": MINIMUM_BRIER_GAIN,
        "learner": "standardised-logistic-l2-c1",
    }
    experiment_id = canonical_sha256(
        {"dataset_id": dataset.dataset_id, "market": dataset.market, **configuration}
    )
    registry = output_root / "experiments.jsonl"
    if previous := _existing_experiment(registry, experiment_id):
        return {**previous, "idempotent_replay": True}

    usage_registry = output_root / "dataset_uses.jsonl"
    locked_ids: set[str] = set()
    if usage_registry.exists():
        for line in usage_registry.read_text(encoding="utf-8").splitlines():
            if line.strip():
                use = json.loads(line)
                if use.get("purpose") == "locked_test":
                    locked_ids.update(str(item) for item in use.get("signal_ids") or [])
    source_rows = sorted(
        dataset.rows,
        key=lambda row: (str(row.get("armed_on")), str(row.get("signal_id"))),
    )
    rows = [row for row in source_rows if str(row.get("signal_id")) not in locked_ids]
    sessions = sorted({str(row.get("armed_on")) for row in rows})
    blockers = []
    if len(rows) < minimum_rows:
        blockers.append(f"need {minimum_rows - len(rows)} more sealed mature rows")
    if len(sessions) < minimum_sessions:
        blockers.append(f"need {minimum_sessions - len(sessions)} more independent sessions")
    cohorts = {
        "model_versions": sorted({str(row.get("model_version")) for row in rows}),
        "strategy_versions": sorted({str(row.get("strategy_version")) for row in rows}),
        "feature_versions": sorted({str(row.get("feature_version")) for row in rows}),
        "contract_versions": sorted({str(row.get("contract_version")) for row in rows}),
    }
    if any(len(versions) != 1 for versions in cohorts.values()):
        blockers.append("mixed frozen model, strategy, feature, or outcome contracts")
    report: dict[str, Any] = {
        "version": WORKFLOW_VERSION,
        "experiment_id": experiment_id,
        "created_at": datetime.now(IST).isoformat(),
        "market": dataset.market,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "source_rows": len(source_rows),
        "rows": len(rows),
        "prior_locked_rows_excluded": len(source_rows) - len(rows),
        "sessions": len(sessions),
        "cohorts": cohorts,
        "configuration": configuration,
        "active_model_changed": False,
        "promotion_authorized": False,
        "blockers": blockers,
    }
    if blockers:
        report.update(status="blocked_insufficient_or_mixed_evidence", conclusion="not_run")
        return report

    test_sessions = max(1, math.ceil(len(sessions) * TEST_SESSION_FRACTION))
    split_sessions = set(sessions[-test_sessions:])
    development = [row for row in rows if str(row.get("armed_on")) not in split_sessions]
    test = [row for row in rows if str(row.get("armed_on")) in split_sessions]
    if len({int(row["label"]) for row in development}) < 2:
        report.update(
            status="blocked_single_class_development",
            conclusion="not_run",
            blockers=["development fold has only one outcome class"],
        )
        return report

    features = []
    for name in PREREGISTERED_FEATURES:
        values = [_finite((row.get("features") or {}).get(name)) for row in development]
        observed = np.asarray([value for value in values if value is not None], dtype=float)
        if len(observed) >= max(5, len(development) // 2) and float(np.std(observed)) > 1e-12:
            features.append(name)
    if not features:
        report.update(
            status="blocked_no_variable_preregistered_features",
            conclusion="not_run",
            blockers=["no preregistered prediction-time feature has usable variation"],
        )
        return report

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    x_dev, medians = _matrix(development, features)
    x_test, _ = _matrix(test, features, medians)
    scaler = StandardScaler().fit(x_dev)
    x_dev_scaled, x_test_scaled = scaler.transform(x_dev), scaler.transform(x_test)
    labels_dev = np.asarray([int(row["label"]) for row in development], dtype=int)
    model = LogisticRegression(C=1.0, max_iter=2000, random_state=17).fit(
        x_dev_scaled, labels_dev
    )
    challenger_dev = model.predict_proba(x_dev_scaled)[:, 1]
    challenger_test = model.predict_proba(x_test_scaled)[:, 1]
    prevalence = float(np.mean(labels_dev))

    def baseline(rows_: list[Mapping[str, Any]]) -> np.ndarray:
        return np.asarray(
            [
                probability
                if (
                    (
                        probability := _finite(
                            (row.get("features") or {}).get("decision.probability")
                        )
                    )
                    is not None
                    and 0 <= probability <= 1
                )
                else prevalence
                for row in rows_
            ],
            dtype=float,
        )

    baseline_dev, baseline_test = baseline(development), baseline(test)
    scores = {
        "development": {
            "frozen_baseline": _model_score(baseline_dev, development),
            "challenger": _model_score(challenger_dev, development),
        },
        "chronological_test": {
            "sessions": sorted(split_sessions),
            "frozen_baseline": _model_score(baseline_test, test),
            "challenger": _model_score(challenger_test, test),
        },
    }
    consistency = _group_scores(test, challenger_test)
    random_control = _random_selection_control(test, challenger_test)
    dev_gain = (
        scores["development"]["frozen_baseline"]["brier"]
        - scores["development"]["challenger"]["brier"]
    )
    test_gain = (
        scores["chronological_test"]["frozen_baseline"]["brier"]
        - scores["chronological_test"]["challenger"]["brier"]
    )
    test_accuracy_gain = (
        scores["chronological_test"]["challenger"]["accuracy"]
        - scores["chronological_test"]["frozen_baseline"]["accuracy"]
    )
    if dev_gain >= MINIMUM_BRIER_GAIN and test_gain < MINIMUM_BRIER_GAIN:
        status, conclusion = "completed_development_only_rejected", "negative"
    elif test_gain >= MINIMUM_BRIER_GAIN and test_accuracy_gain >= 0:
        status, conclusion = "completed_development_candidate", "needs_locked_evaluation"
    elif test_gain <= -MINIMUM_BRIER_GAIN:
        status, conclusion = "completed_negative", "negative"
    else:
        status, conclusion = "completed_inconclusive", "inconclusive"
    report.update(
        status=status,
        conclusion=conclusion,
        features=features,
        split={"development_rows": len(development), "test_rows": len(test)},
        scores=scores,
        consistency=consistency,
        controls={
            "random_selection": random_control,
            "random_timing": {
                "status": "not_available",
                "reason": "requires a separately frozen candle/universe cohort",
            },
        },
        deltas={
            "development_brier_gain": dev_gain,
            "chronological_test_brier_gain": test_gain,
            "chronological_test_accuracy_gain": test_accuracy_gain,
        },
        model={
            "kind": "standardised_logistic_regression",
            "intercept": float(model.intercept_[0]),
            "coefficients": dict(zip(features, model.coef_[0].tolist(), strict=True)),
            "imputation_medians": dict(zip(features, medians.tolist(), strict=True)),
            "scaler_means": dict(zip(features, scaler.mean_.tolist(), strict=True)),
            "scaler_scales": dict(zip(features, scaler.scale_.tolist(), strict=True)),
        },
        blockers=[
            "random-timing control not completed",
            "prospective challenger cohort not registered",
            "explicit human promotion approval not granted",
        ],
    )
    development_dataset = _subset_dataset(dataset, development, "development")
    locked_dataset = _subset_dataset(dataset, test, "locked_test")
    report["split"].update(
        development_dataset_id=development_dataset.dataset_id,
        locked_test_dataset_id=locked_dataset.dataset_id,
        prior_locked_rows_excluded=len(source_rows) - len(rows),
    )
    frozen = output_root / "datasets" / f"{dataset.dataset_id}.json"
    _write_json(frozen, dataset.to_dict())
    _write_json(
        output_root / "datasets" / f"{development_dataset.dataset_id}.json",
        development_dataset.to_dict(),
    )
    _write_json(
        output_root / "datasets" / f"{locked_dataset.dataset_id}.json",
        locked_dataset.to_dict(),
    )
    artifact = output_root / "challengers" / f"{experiment_id}.json"
    _write_json(artifact, report)
    _write_json(output_root / "latest.json", report)
    register_dataset_use(development_dataset, usage_registry)
    register_dataset_use(locked_dataset, usage_registry)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, sort_keys=True, default=str) + "\n")
    return report


def record_failed_experiment(
    output_root: Path, dataset: LearningDataset, error: Exception
) -> dict[str, Any]:
    """Persist an execution failure so a broken experiment cannot silently disappear."""

    created_at = datetime.now(IST).isoformat()
    experiment_id = canonical_sha256(
        {
            "workflow_version": WORKFLOW_VERSION,
            "dataset_id": dataset.dataset_id,
            "market": dataset.market,
            "failure_type": type(error).__name__,
            "created_at": created_at,
        }
    )
    record = {
        "version": WORKFLOW_VERSION,
        "experiment_id": experiment_id,
        "created_at": created_at,
        "market": dataset.market,
        "dataset_id": dataset.dataset_id,
        "status": "failed_execution",
        "conclusion": "failed",
        "error_type": type(error).__name__,
        "error": str(error),
        "active_model_changed": False,
        "promotion_authorized": False,
    }
    registry = output_root / "experiments.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def challenger_summary(output_root: Path) -> dict[str, Any]:
    registry = output_root / "experiments.jsonl"
    experiments = []
    if registry.exists():
        experiments = [
            json.loads(line) for line in registry.read_text(encoding="utf-8").splitlines() if line
        ]
    latest_path = output_root / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else None
    return {
        "latest": latest,
        "experiments": len(experiments),
        "negative": sum(e.get("conclusion") == "negative" for e in experiments),
        "inconclusive": sum(e.get("conclusion") == "inconclusive" for e in experiments),
        "failed": sum(e.get("conclusion") == "failed" for e in experiments),
        "development_candidates": sum(
            e.get("conclusion") == "needs_locked_evaluation" for e in experiments
        ),
        "active_model_changed": False,
        "promotion_authorized": False,
    }


def challenger_is_due(state: Mapping[str, Any], now: datetime) -> bool:
    if state.get("status") != "ready_for_weekly_challenger":
        return False
    completed_at = state.get("last_challenger_completed_at")
    if not completed_at:
        return True
    try:
        prior = datetime.fromisoformat(str(completed_at))
    except ValueError:
        return True
    return now - prior >= timedelta(days=7)
