"""Ledger-only performance monitoring and versioned challenger experiments.

This module never changes an active model, configuration, eligibility gate, or trading
authority.  It consumes only deterministic ``LearningDataset`` rows created from sealed
predictions with mature valid outcomes.  NSE, BSE and crypto are always run separately.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.learning_dataset import LearningDataset, register_dataset_use
from tradedesk.precision_selector import (
    PRIMARY_SELECTOR_POLICY,
    SELECTOR_VERSION,
    SelectorPolicy,
    evaluate_precision_selector,
    select_primary_signal_ids,
)
from tradedesk.prediction_ledger import canonical_sha256
from tradedesk.random_timing_control import (
    SELECTION_CONTRACT_VERSION,
    SELECTION_MANIFEST_VERSION,
    accumulate_random_timing_evidence,
    candle_source_identity,
    evaluate_matched_random_timing,
)
from tradedesk.random_timing_control import (
    control_configuration as random_timing_configuration,
)

WORKFLOW_VERSION = "sealed-challenger-v6"
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
    if len(contracts) <= 1:
        qualified_score = _score_rows(qualified)
    else:
        qualified_score = {
            **_score_rows([]),
            "n": len(qualified),
            "reason": "multiple exit contracts; use contract scorecards",
        }
    return {
        "version": "sealed-performance-v1",
        "status": "available" if qualified else "not_available",
        "market": dataset.market,
        "dataset_id": dataset.dataset_id,
        "qualified": qualified_score,
        "counterfactual": _score_rows(counterfactual),
        "contracts": contracts,
        "contracts_pooled": False,
        "invalid_pending_never_triggered_included": False,
    }


def drift_snapshot(dataset: LearningDataset, *, window: int = DRIFT_WINDOW) -> dict[str, Any]:
    """Compare windows within each exit contract; contracts are never pooled."""

    qualified = [row for row in dataset.rows if row["evidence_role"] == "recommended"]
    contracts = sorted({str(row["contract_version"]) for row in qualified})
    if len(contracts) <= 1:
        return _drift_rows_snapshot(qualified, window=window)
    reports = {
        contract: _drift_rows_snapshot(
            [row for row in qualified if row["contract_version"] == contract],
            window=window,
        )
        for contract in contracts
    }
    return {
        "version": "sealed-drift-v1",
        "status": (
            "drift_detected"
            if any(report["status"] == "drift_detected" for report in reports.values())
            else "contract_specific"
        ),
        "contracts": reports,
        "contracts_pooled": False,
    }


def _drift_rows_snapshot(
    source_rows: list[Mapping[str, Any]], *, window: int
) -> dict[str, Any]:
    rows = sorted(
        source_rows,
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
    wins = int(np.sum(labels))
    calibration = []
    for lo, hi in ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.000001)):
        selected = (probabilities >= lo) & (probabilities < hi)
        if np.any(selected):
            calibration.append(
                {
                    "lo": lo,
                    "hi": min(1.0, hi),
                    "n": int(np.sum(selected)),
                    "mean_probability": float(np.mean(probabilities[selected])),
                    "realised_accuracy": float(np.mean(labels[selected])),
                }
            )
    return {
        "n": len(rows),
        "wins": wins,
        "accuracy": float(np.mean(predicted == labels)),
        "strict_outcome_accuracy": wins / len(rows),
        "strict_outcome_wilson_95_low": _wilson(wins, len(rows))[0],
        "brier": float(np.mean((probabilities - labels) ** 2)),
        "calibration": calibration,
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
    for field in ("regime", "sector", "armed_on", "month", "liquidity"):
        grouped: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            if field == "month":
                name = str(row.get("armed_on") or "unavailable")[:7]
            elif field == "liquidity":
                turnover = _finite(
                    (row.get("features") or {}).get("context.average_turnover")
                )
                name = (
                    "unavailable"
                    if turnover is None
                    else "lt_1cr"
                    if turnover < 10_000_000
                    else "1cr_to_10cr"
                    if turnover < 100_000_000
                    else "10cr_plus"
                )
            else:
                name = str(row.get(field) or "unavailable")
            grouped.setdefault(name, []).append(index)
        out[field] = {
            name: _model_score(probabilities[indexes], [rows[index] for index in indexes])
            for name, indexes in sorted(grouped.items())
        }
    return out


def _evidence_ladder(
    rows: list[Mapping[str, Any]], test_score: Mapping[str, Any]
) -> dict[str, Any]:
    sessions = len({str(row.get("armed_on")) for row in rows})
    n, oos = len(rows), int(test_score["n"])
    if n < 50:
        stage = "collecting_below_diagnostic_floor"
    elif n < 100 or sessions < 30:
        stage = "diagnostic_only"
    elif n < 250:
        stage = "first_review"
    elif n < 500 or oos < 100:
        stage = "stability_review"
    else:
        stage = "production_sample_threshold_reached"
    return {
        "stage": stage,
        "resolved_calls": n,
        "independent_sessions": sessions,
        "locked_oos_calls": oos,
        "thresholds": {
            "diagnostic": {"calls": 50},
            "first_review": {"calls": 100, "sessions": 30},
            "stability_review": {"calls": 250},
            "production_evidence": {"calls": 500, "locked_oos_calls": 100},
        },
    }


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


def _seal_control(payload: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(payload)
    record["record_sha256"] = canonical_sha256(record)
    return record


def _load_timing_selection_contract(
    path: Path, *, market: str, contract_version: str | None
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("timing selection contract is malformed")
    digest = record.get("record_sha256")
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    source_cohort = record.get("source_cohort")
    if (
        record.get("version") != SELECTION_CONTRACT_VERSION
        or not digest
        or digest != canonical_sha256(payload)
        or record.get("market") != market
        or (contract_version is not None and record.get("contract_version") != contract_version)
        or record.get("selector_policy") != PRIMARY_SELECTOR_POLICY
        or record.get("selector_version") != SELECTOR_VERSION
        or record.get("control_configuration_sha256")
        != canonical_sha256(random_timing_configuration())
        or not isinstance(source_cohort, dict)
        or source_cohort.get("contract_version") != record.get("contract_version")
        or not isinstance(source_cohort.get("contract_sha256"), str)
        or len(source_cohort["contract_sha256"]) != 64
        or not all(
            character in "0123456789abcdef"
            for character in source_cohort["contract_sha256"].lower()
        )
        or not source_cohort.get("strategy_version")
        or not source_cohort.get("feature_version")
    ):
        raise ValueError("timing selection contract integrity/scope mismatch")
    return record


def _frozen_probabilities(
    rows: Sequence[Mapping[str, Any]],
    *,
    features: Sequence[str],
    model: Mapping[str, Any],
) -> np.ndarray:
    medians = np.asarray([float(model["imputation_medians"][name]) for name in features])
    means = np.asarray([float(model["scaler_means"][name]) for name in features])
    scales = np.asarray([float(model["scaler_scales"][name]) for name in features])
    coefficients = np.asarray([float(model["coefficients"][name]) for name in features])
    if np.any(scales <= 0):
        raise ValueError("frozen timing selector has invalid scaler values")
    matrix, _ = _matrix(list(rows), list(features), medians)
    logits = float(model["intercept"]) + ((matrix - means) / scales) @ coefficients
    logits = np.clip(logits, -700.0, 700.0)
    return 1.0 / (1.0 + np.exp(-logits))


def run_challenger_experiment(
    dataset: LearningDataset,
    output_root: Path,
    *,
    minimum_rows: int = MINIMUM_ROWS,
    minimum_sessions: int = MINIMUM_SESSIONS,
    bars_loader: Callable[[str], pd.DataFrame] | None = None,
    timing_opportunities: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fit one deterministic transparent challenger and persist every result."""

    if dataset.market not in {"nse", "bse", "crypto"}:
        raise ValueError("challenger market must be nse, bse, or crypto")
    timing_population = sorted(
        (dict(row) for row in (timing_opportunities or ())),
        key=lambda row: (str(row.get("armed_on")), str(row.get("signal_id"))),
    )
    timing_population_sha256 = canonical_sha256(timing_population)
    dataset_contracts = sorted(
        {str(row.get("contract_version")) for row in dataset.rows}
    )
    contract_hint = dataset_contracts[0] if len(dataset_contracts) == 1 else None
    selection_contract_path = output_root / "timing_selection_contract.json"
    selection_contract = _load_timing_selection_contract(
        selection_contract_path,
        market=dataset.market,
        contract_version=contract_hint,
    )
    candle_identity = candle_source_identity(timing_population, bars_loader)
    configuration = {
        "workflow_version": WORKFLOW_VERSION,
        "features": PREREGISTERED_FEATURES,
        "minimum_rows": minimum_rows,
        "minimum_sessions": minimum_sessions,
        "test_session_fraction": TEST_SESSION_FRACTION,
        "minimum_brier_gain": MINIMUM_BRIER_GAIN,
        "learner": "standardised-logistic-l2-c1",
        "random_timing_control": random_timing_configuration(),
        "timing_population_sha256": timing_population_sha256,
        "timing_candle_source_identity": candle_identity,
        "timing_selection_context_sha256": (
            selection_contract.get("record_sha256")
            if selection_contract is not None
            else f"register_from_dataset:{dataset.dataset_id}"
        ),
    }
    experiment_id = canonical_sha256(
        {"dataset_id": dataset.dataset_id, "market": dataset.market, **configuration}
    )
    registry = output_root / "experiments.jsonl"
    if selection_contract is not None and (
        previous := _existing_experiment(registry, experiment_id)
    ):
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
        "contract_sha256s": sorted({str(row.get("contract_sha256")) for row in rows}),
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
    first_test_day = date.fromisoformat(min(split_sessions))
    unpurged_development = development
    development = []
    purged_overlap = 0
    purged_unverifiable = 0
    for row in unpurged_development:
        exit_on = row.get("exit_on")
        if not exit_on:
            purged_unverifiable += 1
            continue
        try:
            exit_day = date.fromisoformat(str(exit_on)[:10])
        except ValueError:
            purged_unverifiable += 1
            continue
        if exit_day >= first_test_day:
            purged_overlap += 1
            continue
        development.append(row)
    if len(development) < 5:
        report.update(
            status="blocked_purged_development_too_small",
            conclusion="not_run",
            blockers=[
                "fewer than five development rows remain after outcome-overlap purging"
            ],
            purge={
                "overlap_rows": purged_overlap,
                "unverifiable_exit_rows": purged_unverifiable,
            },
        )
        return report
    if len({int(row["label"]) for row in development}) < 2:
        report.update(
            status="blocked_single_class_development",
            conclusion="not_run",
            blockers=["development fold has only one outcome class"],
        )
        return report

    labels_dev = np.asarray([int(row["label"]) for row in development], dtype=int)
    contract_version = str(test[0]["contract_version"])
    if selection_contract is not None:
        features = [str(name) for name in selection_contract.get("features") or []]
        model_report = dict(selection_contract.get("model") or {})
        selector_policy = SelectorPolicy(**dict(selection_contract["selector_policy_state"]))
        if not features or model_report.get("kind") != "standardised_logistic_regression":
            raise ValueError("frozen timing selection model is incomplete")
        challenger_dev = _frozen_probabilities(
            development, features=features, model=model_report
        )
        challenger_test = _frozen_probabilities(
            test, features=features, model=model_report
        )
    else:
        features = []
        for name in PREREGISTERED_FEATURES:
            values = [_finite((row.get("features") or {}).get(name)) for row in development]
            observed = np.asarray(
                [value for value in values if value is not None], dtype=float
            )
            if (
                len(observed) >= max(5, len(development) // 2)
                and float(np.std(observed)) > 1e-12
            ):
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
        model = LogisticRegression(C=1.0, max_iter=2000, random_state=17).fit(
            x_dev_scaled, labels_dev
        )
        challenger_dev = model.predict_proba(x_dev_scaled)[:, 1]
        challenger_test = model.predict_proba(x_test_scaled)[:, 1]
        model_report = {
            "kind": "standardised_logistic_regression",
            "intercept": float(model.intercept_[0]),
            "coefficients": dict(zip(features, model.coef_[0].tolist(), strict=True)),
            "imputation_medians": dict(zip(features, medians.tolist(), strict=True)),
            "scaler_means": dict(zip(features, scaler.mean_.tolist(), strict=True)),
            "scaler_scales": dict(zip(features, scaler.scale_.tolist(), strict=True)),
        }
        selector_policy = SelectorPolicy(market=dataset.market, failed_patterns=())

    development_probabilities = {
        str(row["signal_id"]): float(probability)
        for row, probability in zip(development, challenger_dev, strict=True)
    }
    test_probabilities = {
        str(row["signal_id"]): float(probability)
        for row, probability in zip(test, challenger_test, strict=True)
    }
    precision_selector = evaluate_precision_selector(
        development,
        test,
        development_probabilities,
        test_probabilities,
        market=dataset.market,
        frozen_policy=selector_policy if selection_contract is not None else None,
    )
    selector_policy = SelectorPolicy(**precision_selector["policy"])

    if selection_contract is None:
        starts_after = max(
            [str(row.get("armed_on") or "") for row in timing_population]
            or [str(row.get("armed_on") or "") for row in source_rows]
        )
        selection_contract = _seal_control(
            {
                "version": SELECTION_CONTRACT_VERSION,
                "market": dataset.market,
                "contract_version": contract_version,
                "selector_policy": PRIMARY_SELECTOR_POLICY,
                "selector_version": SELECTOR_VERSION,
                "control_configuration_sha256": canonical_sha256(
                    random_timing_configuration()
                ),
                "registered_from_dataset_id": dataset.dataset_id,
                "starts_after": starts_after,
                "features": features,
                "model": model_report,
                "selector_policy_state": precision_selector["policy"],
                "source_cohort": {
                    "contract_version": contract_version,
                    "contract_sha256": test[0].get("contract_sha256"),
                    "strategy_version": test[0].get("strategy_version"),
                    "feature_version": test[0].get("feature_version"),
                    "model_version": test[0].get("model_version"),
                },
                "selection_authority": "future_sessions_only",
            }
        )
        configuration["timing_selection_context_sha256"] = selection_contract[
            "record_sha256"
        ]
        experiment_id = canonical_sha256(
            {"dataset_id": dataset.dataset_id, "market": dataset.market, **configuration}
        )
        report.update(experiment_id=experiment_id, configuration=configuration)
        if previous := _existing_experiment(registry, experiment_id):
            return {**previous, "idempotent_replay": True}
        _write_json(selection_contract_path, selection_contract)
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
    primary_score = precision_selector["operating_points"][PRIMARY_SELECTOR_POLICY]
    assert selection_contract is not None
    starts_after = str(selection_contract["starts_after"])
    source_cohort = dict(selection_contract["source_cohort"])
    timing_test = [
        row
        for row in timing_population
        if str(row.get("armed_on") or "") > starts_after
        and row.get("contract_version") == contract_version
        and all(row.get(name) == expected for name, expected in source_cohort.items())
    ]
    timing_probabilities: dict[str, float] = {}
    if timing_test:
        timing_values = _frozen_probabilities(
            timing_test, features=features, model=model_report
        )
        timing_probabilities = {
            str(row["signal_id"]): float(probability)
            for row, probability in zip(timing_test, timing_values, strict=True)
        }
    timing_primary_ids = set(
        select_primary_signal_ids(timing_test, timing_probabilities, selector_policy)
    )
    timing_primary_rows = [
        row for row in timing_test if str(row["signal_id"]) in timing_primary_ids
    ]
    selection_manifest = _seal_control(
        {
            "version": SELECTION_MANIFEST_VERSION,
            "market": dataset.market,
            "contract_version": contract_version,
            "selection_context_sha256": selection_contract["record_sha256"],
            "candidate_population_sha256": canonical_sha256(timing_test),
            "probabilities_sha256": canonical_sha256(timing_probabilities),
            "selected_signal_ids": sorted(timing_primary_ids),
            "selected_sessions": sorted(
                str(row.get("armed_on")) for row in timing_primary_rows
            ),
        }
    )
    random_timing_batch = evaluate_matched_random_timing(
        timing_primary_rows,
        historical_opportunities=timing_population,
        market=dataset.market,
        contract_version=contract_version,
        selection_context=selection_contract,
        selection_manifest=selection_manifest,
        bars_loader=bars_loader,
    )
    random_timing = accumulate_random_timing_evidence(
        random_timing_batch, output_root / "random_timing_evidence.json"
    )
    evidence_ladder = _evidence_ladder(
        rows, scores["chronological_test"]["challenger"]
    )
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
    timing_replay_invalid = random_timing.get("status") in {
        "invalid_source",
        "not_available",
    }
    if timing_replay_invalid:
        status, conclusion = "blocked_random_timing_replay", "not_run"
    report.update(
        status=status,
        conclusion=conclusion,
        features=features,
        split={"development_rows": len(development), "test_rows": len(test)},
        purge={
            "overlap_rows": purged_overlap,
            "unverifiable_exit_rows": purged_unverifiable,
            "first_locked_test_session": first_test_day.isoformat(),
        },
        scores=scores,
        evidence_ladder=evidence_ladder,
        consistency=consistency,
        controls={
            "random_selection": random_control,
            "random_timing_batch": random_timing_batch,
            "random_timing": random_timing,
        },
        precision_selector=precision_selector,
        timing_selection={
            "context_sha256": selection_contract["record_sha256"],
            "starts_after": selection_contract["starts_after"],
            "manifest_sha256": selection_manifest["record_sha256"],
            "candidate_rows": len(timing_test),
            "selected_rows": len(timing_primary_rows),
            "pending_rows_included_before_ranking": True,
        },
        final_evidence_gates={
            "sample_threshold_reached": (
                evidence_ladder["stage"] == "production_sample_threshold_reached"
            ),
            "primary_selector_policy_clears_accuracy_and_net_gate": bool(
                primary_score["passes_final_accuracy_gate"]
            ),
            # Compatibility alias for already-written promotion-control records. It now
            # means the single frozen primary policy, never post-hoc choice among six.
            "any_preregistered_selector_policy_clears_accuracy_and_net_gate": bool(
                primary_score["passes_final_accuracy_gate"]
            ),
            "matched_random_timing_margin_passed": bool(
                random_timing.get("random_timing_gate_passed")
            ),
            "prospective_cohort_passed": False,
            "all_passed": False,
        },
        deltas={
            "development_brier_gain": dev_gain,
            "chronological_test_brier_gain": test_gain,
            "chronological_test_accuracy_gain": test_accuracy_gain,
        },
        model=model_report,
        blockers=(
            []
            if primary_score["passes_final_accuracy_gate"]
            else ["frozen primary selector accuracy/net gate not passed"]
        )
        + (
            []
            if random_timing.get("random_timing_gate_passed")
            else [
                "random-timing control not completed: "
                + str(random_timing.get("reason") or random_timing.get("status"))
            ]
        )
        + [
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
        output_root / "datasets" / f"timing-opportunities-{timing_population_sha256}.json",
        {
            "market": dataset.market,
            "sha256": timing_population_sha256,
            "rows": timing_population,
        },
    )
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
    if not timing_replay_invalid:
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
    roots = [output_root]
    contracts_root = output_root / "contracts"
    if contracts_root.exists():
        roots.extend(path for path in contracts_root.iterdir() if path.is_dir())
    experiments = []
    latest_by_contract: dict[str, Any] = {}
    latest_candidates = []
    for root in roots:
        registry = root / "experiments.jsonl"
        if registry.exists():
            experiments.extend(
                json.loads(line)
                for line in registry.read_text(encoding="utf-8").splitlines()
                if line
            )
        latest_path = root / "latest.json"
        if latest_path.exists():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            latest_candidates.append(latest)
            if root.parent == contracts_root:
                latest_by_contract[root.name] = latest
    latest = max(
        latest_candidates, key=lambda item: str(item.get("created_at") or ""), default=None
    )
    return {
        "latest": latest,
        "latest_by_contract": latest_by_contract,
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
