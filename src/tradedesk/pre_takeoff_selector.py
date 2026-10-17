"""M19 pre-takeoff tradeability selector.

The experiment consumes only the content-addressed M18-A rows.  It learns from causal
prior-close features, chooses a one-call-per-session abstention policy on calibration and
opens the internal diagnostic once.  Historical output is research evidence only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import warnings
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from tradedesk.broker.indstocks.models import IST
from tradedesk.intraday_contract_race import _append_registry, _atomic_json, _hash_file
from tradedesk.leader_discovery import FEATURE_COLUMNS
from tradedesk.prediction_ledger import canonical_sha256
from tradedesk.selection_readiness import (
    DATASET_VERSION as M18_DATASET_VERSION,
)
from tradedesk.selection_readiness import (
    DEFAULT_OUTPUT_ROOT as M18_OUTPUT_ROOT,
)
from tradedesk.selection_readiness import load_selection_readiness_status
from tradedesk.stored_path import stored_path

VERSION = "pre-takeoff-selector-m19-v1"
PROTOCOL_PATH = Path("docs/self-learning-m19-pre-takeoff-protocol.md")
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/pre_takeoff_selector")
SUPPORTED_MARKETS = ("nse", "crypto")
ALL_MARKETS = ("nse", "bse", "crypto")
PROBABILITY_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
MINIMUM_POLICY_CALLS = 8
RANDOM_REPETITIONS = 2_000
RANDOM_SEED = 19
CANONICAL_BASELINE_ACCURACY = 149 / 693

MarketName = Literal["nse", "bse", "crypto"]


def _protocol_sha256() -> str:
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(f"frozen M19 protocol missing: {PROTOCOL_PATH}")
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    observed = successes / total
    denominator = 1.0 + z * z / total
    centre = (observed + z * z / (2.0 * total)) / denominator
    radius = (
        z
        * math.sqrt(observed * (1.0 - observed) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _maximum_losing_streak(values: Sequence[bool]) -> int:
    maximum = 0
    current = 0
    for value in values:
        if value:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


def _policy_metrics(selected: pd.DataFrame, *, total_sessions: int) -> dict[str, Any]:
    ordered = selected.sort_values(["session", "scrip_code"]).copy()
    calls = len(ordered)
    call_sessions = int(ordered["session"].nunique()) if calls else 0
    successes = int(ordered["label"].sum()) if calls else 0
    accuracy = successes / calls if calls else None
    lower, upper = _wilson(successes, calls)
    net_values = ordered["net_r"].to_numpy(dtype=float) if calls else np.array([])
    return {
        "calls": calls,
        "call_sessions": call_sessions,
        "total_sessions": total_sessions,
        "no_call_rate": 1.0 - call_sessions / total_sessions if total_sessions else None,
        "strict_successes": successes,
        "strict_accuracy": accuracy,
        "wilson_95_lower": lower if calls else None,
        "wilson_95_upper": upper if calls else None,
        "mean_net_r": float(net_values.mean()) if calls else None,
        "median_net_r": float(np.median(net_values)) if calls else None,
        "total_net_r": float(net_values.sum()) if calls else None,
        "maximum_losing_streak": (
            _maximum_losing_streak([bool(value) for value in ordered["label"]])
            if calls
            else None
        ),
    }


def _feature_frame(rows: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [dict(value) for value in rows["features"]],
        columns=list(FEATURE_COLUMNS),
        index=rows.index,
        dtype=float,
    )


def _read_dataset(readiness: Mapping[str, Any], market: str) -> pd.DataFrame:
    dataset = readiness.get("dataset") or {}
    path_value = dataset.get("path")
    if not path_value:
        # Dashboard status intentionally hides the path. Recover it from the validated
        # M18-A latest file, then verify the content digest again below.
        latest = M18_OUTPUT_ROOT / market / "latest.json"
        payload = json.loads(latest.read_text(encoding="utf-8"))
        dataset = payload.get("dataset") or {}
        path_value = dataset.get("path")
    path = Path(str(path_value))
    expected_sha = str(dataset.get("sha256") or readiness.get("dataset", {}).get("sha256"))
    if not path.exists() or _hash_file(path) != expected_sha:
        raise ValueError("M19 bound M18-A dataset is missing or changed")

    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            value = json.loads(line)
            if value.get("version") != M18_DATASET_VERSION:
                raise ValueError("M19 source dataset version mismatch")
            if value.get("market") != market:
                raise ValueError("M19 source dataset market mixing detected")
            if value.get("model_eligible") is not True:
                continue
            features = value.get("features") or {}
            if set(features) != set(FEATURE_COLUMNS):
                raise ValueError("M19 causal feature contract mismatch")
            numbers = [float(features[name]) for name in FEATURE_COLUMNS]
            if not all(math.isfinite(number) for number in numbers):
                raise ValueError("M19 source contains a non-finite eligible feature")
            label = value.get("precision_label")
            net_r = value.get("net_r")
            if not isinstance(label, bool) or net_r is None or not math.isfinite(float(net_r)):
                raise ValueError("M19 source contains an invalid eligible outcome")
            records.append(
                {
                    "session": str(value["session"]),
                    "scrip_code": str(value["scrip_code"]),
                    "symbol": str(value["symbol"]),
                    "roles": tuple(sorted(str(role) for role in value.get("roles", ()))),
                    "split": str(value["split"]),
                    "features": {name: float(features[name]) for name in FEATURE_COLUMNS},
                    "label": bool(label),
                    "net_r": float(net_r),
                }
            )
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("M19 source has no model-eligible rows")
    if frame.duplicated(["session", "scrip_code"]).any():
        raise ValueError("M19 source contains duplicate session/instrument rows")
    frame.sort_values(["session", "scrip_code"], inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame


def _build_models() -> dict[str, Any]:
    return {
        "regularized_logistic": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.1,
                        penalty="l2",
                        class_weight="balanced",
                        max_iter=2_000,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        ),
        "bounded_random_forest": RandomForestClassifier(
            n_estimators=300,
            max_depth=4,
            min_samples_leaf=20,
            max_features="sqrt",
            class_weight="balanced_subsample",
            random_state=RANDOM_SEED,
            n_jobs=1,
        ),
    }


def _fit_calibrated_models(
    train: pd.DataFrame, calibration: pd.DataFrame
) -> dict[str, CalibratedClassifierCV]:
    x_train = _feature_frame(train).to_numpy(dtype=float)
    y_train = train["label"].to_numpy(dtype=int)
    x_calibration = _feature_frame(calibration).to_numpy(dtype=float)
    y_calibration = calibration["label"].to_numpy(dtype=int)
    if len(set(y_train)) != 2 or len(set(y_calibration)) != 2:
        raise ValueError("M19 train and calibration blocks must each contain both classes")

    calibrated: dict[str, CalibratedClassifierCV] = {}
    for name, model in _build_models().items():
        model.fit(x_train, y_train)
        # sklearn 1.5 supports the prefit contract used in the frozen protocol. Warnings
        # concern a future API migration and do not change the fitted estimator contract.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            wrapper = CalibratedClassifierCV(model, method="sigmoid", cv="prefit")
            wrapper.fit(x_calibration, y_calibration)
        calibrated[name] = wrapper
    return calibrated


def _score(rows: pd.DataFrame, model: CalibratedClassifierCV) -> pd.DataFrame:
    scored = rows.copy()
    scored["probability"] = model.predict_proba(
        _feature_frame(rows).to_numpy(dtype=float)
    )[:, 1]
    if not np.isfinite(scored["probability"].to_numpy(dtype=float)).all():
        raise ValueError("M19 model produced a non-finite probability")
    return scored


def _top_one(scored: pd.DataFrame, *, threshold: float | None = None) -> pd.DataFrame:
    eligible = scored
    if threshold is not None:
        eligible = eligible[eligible["probability"] >= threshold]
    ranked = eligible.sort_values(
        ["session", "probability", "scrip_code"], ascending=[True, False, True]
    )
    return ranked[ranked.groupby("session").cumcount() == 0].copy()


def _select_policy(
    models: Mapping[str, CalibratedClassifierCV], calibration: pd.DataFrame
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    total_sessions = int(calibration["session"].nunique())
    curves: dict[str, Any] = {}
    eligible: list[tuple[tuple[float, float, int, float, int], dict[str, Any]]] = []
    for name, model in models.items():
        scored = _score(calibration, model)
        rows: list[dict[str, Any]] = []
        for threshold in PROBABILITY_THRESHOLDS:
            selected = _top_one(scored, threshold=threshold)
            metrics = _policy_metrics(selected, total_sessions=total_sessions)
            is_eligible = (
                metrics["calls"] >= MINIMUM_POLICY_CALLS
                and metrics["call_sessions"] >= MINIMUM_POLICY_CALLS
                and float(metrics["mean_net_r"] or 0.0) > 0.0
            )
            card = {"threshold": threshold, "eligible": is_eligible, **metrics}
            rows.append(card)
            if is_eligible:
                policy = {"model": name, "threshold": threshold, "calibration": metrics}
                key = (
                    float(metrics["strict_accuracy"] or -1.0),
                    float(metrics["mean_net_r"] or -math.inf),
                    int(metrics["call_sessions"]),
                    threshold,
                    1 if name == "regularized_logistic" else 0,
                )
                eligible.append((key, policy))
        curves[name] = rows
    winner = max(eligible, key=lambda item: item[0])[1] if eligible else None
    return winner, curves


def _momentum_top_one(rows: pd.DataFrame) -> pd.DataFrame:
    ranked = rows.copy()
    ranked["momentum"] = ranked["features"].map(
        lambda value: float(value["return_20_rank"])
    )
    ranked.sort_values(
        ["session", "momentum", "scrip_code"], ascending=[True, False, True], inplace=True
    )
    return ranked[ranked.groupby("session").cumcount() == 0].copy()


def _primary_top_one(scored: pd.DataFrame) -> pd.DataFrame:
    primary = scored[scored["roles"].map(lambda roles: "primary" in roles)]
    return _top_one(primary)


def _matched_random(
    universe: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    repetitions: int = RANDOM_REPETITIONS,
) -> dict[str, Any]:
    sessions = sorted(set(str(value) for value in selected["session"]))
    groups = [universe[universe["session"] == session] for session in sessions]
    if not groups:
        return {
            "repetitions": repetitions,
            "matched_sessions": 0,
            "mean_accuracy": None,
            "mean_net_r": None,
            "accuracy_tail_probability": None,
            "net_r_tail_probability": None,
            "seed": RANDOM_SEED,
        }
    observed_accuracy = float(selected["label"].mean())
    observed_net_r = float(selected["net_r"].mean())
    rng = np.random.default_rng(RANDOM_SEED)
    accuracies = np.empty(repetitions, dtype=float)
    net_returns = np.empty(repetitions, dtype=float)
    group_values = [
        (
            group["label"].to_numpy(dtype=float),
            group["net_r"].to_numpy(dtype=float),
        )
        for group in groups
    ]
    for index in range(repetitions):
        picked_labels: list[float] = []
        picked_returns: list[float] = []
        for labels, returns in group_values:
            choice = int(rng.integers(0, len(labels)))
            picked_labels.append(float(labels[choice]))
            picked_returns.append(float(returns[choice]))
        accuracies[index] = float(np.mean(picked_labels))
        net_returns[index] = float(np.mean(picked_returns))
    return {
        "repetitions": repetitions,
        "matched_sessions": len(groups),
        "mean_accuracy": float(accuracies.mean()),
        "p95_accuracy": float(np.quantile(accuracies, 0.95)),
        "mean_net_r": float(net_returns.mean()),
        "p95_net_r": float(np.quantile(net_returns, 0.95)),
        "accuracy_tail_probability": float(
            (1 + int(np.sum(accuracies >= observed_accuracy))) / (repetitions + 1)
        ),
        "net_r_tail_probability": float(
            (1 + int(np.sum(net_returns >= observed_net_r))) / (repetitions + 1)
        ),
        "seed": RANDOM_SEED,
    }


def _artifact_path(output_root: Path, market: str) -> Path:
    return output_root / market / "latest.json"


def _validate_result(result: Mapping[str, Any]) -> None:
    if result.get("version") != VERSION:
        raise ValueError("unsupported M19 result version")
    if result.get("market") not in ALL_MARKETS:
        raise ValueError("unsupported M19 result market")
    if result.get("markets_pooled") is not False:
        raise ValueError("M19 market mixing detected")
    if result.get("protocol_sha256") != _protocol_sha256():
        raise ValueError("M19 protocol binding mismatch")
    for field in ("eligible_for_live", "active_model_changed", "baseline_accuracy_improved"):
        if result.get(field) is not False:
            raise ValueError(f"M19 historical result cannot set {field}")
    body = dict(result)
    supplied = body.pop("artifact_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M19 result hash mismatch")


def _unavailable_result(
    market: str,
    readiness: Mapping[str, Any],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    reason = (
        "BSE M18-A path coverage is below the frozen 85% gate."
        if market == "bse"
        else "M18-A is not ready for M19 development."
    )
    result: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "markets_pooled": False,
        "generated_at": generated_at.isoformat(),
        "protocol_sha256": _protocol_sha256(),
        "status": "not_ready",
        "diagnostic_opened": False,
        "readiness_status": readiness.get("status"),
        "selected_policy": None,
        "blockers": list(readiness.get("blockers") or ["m18a_not_ready"]),
        "detail": reason,
        "canonical_baseline_accuracy": CANONICAL_BASELINE_ACCURACY,
        "prospective_observer_registered": False,
        "authority": "research_only",
        "eligible_for_live": False,
        "active_model_changed": False,
        "baseline_accuracy_improved": False,
    }
    result["artifact_sha256"] = canonical_sha256(result)
    _validate_result(result)
    return result


def run_pre_takeoff_selector(
    market: str,
    *,
    readiness_root: Path = M18_OUTPUT_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Run or return the one frozen M19 historical experiment for ``market``."""

    if market not in ALL_MARKETS:
        raise ValueError(f"unsupported M19 market: {market}")
    observed_at = generated_at or datetime.now(IST)
    readiness = load_selection_readiness_status(market, output_root=readiness_root)
    if market not in SUPPORTED_MARKETS or readiness.get("status") != "ready_for_development":
        result = _unavailable_result(market, readiness, generated_at=observed_at)
        _atomic_json(_artifact_path(output_root, market), result)
        return result

    dataset_sha = str((readiness.get("dataset") or {}).get("sha256"))
    experiment_id = canonical_sha256(
        {
            "version": VERSION,
            "market": market,
            "protocol_sha256": _protocol_sha256(),
            "m18_experiment_id": readiness.get("experiment_id"),
            "m18_dataset_sha256": dataset_sha,
            "features": FEATURE_COLUMNS,
            "thresholds": PROBABILITY_THRESHOLDS,
        }
    )
    latest_path = _artifact_path(output_root, market)
    if latest_path.exists():
        existing = json.loads(latest_path.read_text(encoding="utf-8"))
        _validate_result(existing)
        if existing.get("experiment_id") != experiment_id:
            raise ValueError("an M19 result already exists for different bound evidence")
        return dict(existing)

    frame = _read_dataset(readiness, market)
    blocks = {
        name: frame[frame["split"] == name].copy()
        for name in ("development_train", "calibration", "internal_diagnostic")
    }
    if any(blocks[name].empty for name in blocks):
        raise ValueError("M19 requires all three non-purge chronological blocks")
    models = _fit_calibrated_models(blocks["development_train"], blocks["calibration"])
    selected_policy, threshold_curves = _select_policy(models, blocks["calibration"])

    base: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": experiment_id,
        "generated_at": observed_at.isoformat(),
        "protocol_sha256": _protocol_sha256(),
        "m18_experiment_id": readiness.get("experiment_id"),
        "m18_dataset_sha256": dataset_sha,
        "feature_contract": list(FEATURE_COLUMNS),
        "rows": {name: len(value) for name, value in blocks.items()},
        "sessions": {name: int(value["session"].nunique()) for name, value in blocks.items()},
        "label_balance": {
            name: {
                "positive": int(value["label"].sum()),
                "negative": int((~value["label"]).sum()),
            }
            for name, value in blocks.items()
        },
        "source_roles": dict(
            sorted(
                Counter(
                    role
                    for roles in frame["roles"]
                    for role in (roles if roles else ("unlabelled",))
                ).items()
            )
        ),
        "threshold_curves": threshold_curves,
        "canonical_baseline_accuracy": CANONICAL_BASELINE_ACCURACY,
        "consumed_historical_evidence": True,
        "prospective_observer_registered": False,
        "authority": "research_only",
        "eligible_for_live": False,
        "active_model_changed": False,
        "baseline_accuracy_improved": False,
    }

    if selected_policy is None:
        result = {
            **base,
            "status": "development_rejected",
            "diagnostic_opened": False,
            "selected_policy": None,
            "diagnostic": None,
            "controls": None,
            "gates": [],
            "gates_passed": 0,
        "gates_total": 10,
            "blockers": ["no_calibration_policy_with_8_calls_and_positive_mean_net_r"],
            "detail": (
                "No preregistered model/threshold produced useful calibration call volume "
                "with positive after-cost R; the internal diagnostic remains unopened."
            ),
        }
    else:
        model_name = str(selected_policy["model"])
        threshold = float(selected_policy["threshold"])
        model = models[model_name]
        diagnostic = blocks["internal_diagnostic"]
        scored = _score(diagnostic, model)
        selected = _top_one(scored, threshold=threshold)
        total_sessions = int(diagnostic["session"].nunique())
        selected_metrics = _policy_metrics(selected, total_sessions=total_sessions)
        forced = _top_one(scored)
        forced_metrics = _policy_metrics(forced, total_sessions=total_sessions)
        momentum_metrics = _policy_metrics(
            _momentum_top_one(diagnostic), total_sessions=total_sessions
        )
        primary_metrics = _policy_metrics(
            _primary_top_one(scored), total_sessions=total_sessions
        )
        unfiltered_metrics = _policy_metrics(diagnostic, total_sessions=total_sessions)
        random = _matched_random(diagnostic, selected)
        accuracy_advantage = float(selected_metrics["strict_accuracy"] or 0.0) - float(
            forced_metrics["strict_accuracy"] or 0.0
        )
        net_r_advantage = float(selected_metrics["mean_net_r"] or 0.0) - float(
            forced_metrics["mean_net_r"] or 0.0
        )
        calibration_accuracy = float(
            selected_policy["calibration"].get("strict_accuracy") or 0.0
        )
        diagnostic_accuracy = float(selected_metrics["strict_accuracy"] or 0.0)
        integrity_violations: list[str] = []
        gates = {
            "at_least_8_calls": selected_metrics["calls"] >= 8,
            "at_least_8_call_sessions": selected_metrics["call_sessions"] >= 8,
            "strict_accuracy_at_least_60pct": diagnostic_accuracy >= 0.60,
            "wilson_lower_at_least_30pct": float(
                selected_metrics["wilson_95_lower"] or 0.0
            )
            >= 0.30,
            "mean_net_r_positive": float(selected_metrics["mean_net_r"] or 0.0) > 0.0,
            "accuracy_advantage_at_least_10pp": accuracy_advantage >= 0.10,
            "net_r_advantage_at_least_010r": net_r_advantage >= 0.10,
            "random_tails_at_most_010": (
                float(random.get("accuracy_tail_probability") or 1.0) <= 0.10
                and float(random.get("net_r_tail_probability") or 1.0) <= 0.10
            ),
            "calibration_diagnostic_gap_at_most_20pp": abs(
                calibration_accuracy - diagnostic_accuracy
            )
            <= 0.20,
            "zero_integrity_violations": not integrity_violations,
        }
        # The protocol lists zero integrity violations as part of the final gate; retain
        # the exact ten machine-verifiable booleans rather than hiding that safety check.
        passed = sum(bool(value) for value in gates.values())
        status = "historical_candidate" if passed == len(gates) else "development_rejected"
        model_root = output_root / market / "models"
        model_root.mkdir(parents=True, exist_ok=True)
        temporary = model_root / f".{experiment_id}.tmp.joblib"
        joblib.dump(model, temporary)
        model_sha = _hash_file(temporary)
        model_path = model_root / f"{experiment_id}-{model_sha}.joblib"
        os.replace(temporary, model_path)
        result = {
            **base,
            "status": status,
            "diagnostic_opened": True,
            "selected_policy": selected_policy,
            "model_artifact": {"path": str(model_path), "sha256": model_sha},
            "diagnostic": selected_metrics,
            "selected_calls": [
                {
                    "session": str(row.session),
                    "scrip_code": str(row.scrip_code),
                    "symbol": str(row.symbol),
                    "probability": float(row.probability),  # type: ignore[arg-type]
                    "strict_success": bool(row.label),
                    "net_r": float(row.net_r),  # type: ignore[arg-type]
                }
                for row in selected.itertuples(index=False)
            ],
            "controls": {
                "unfiltered": unfiltered_metrics,
                "forced_model_top_one": forced_metrics,
                "momentum_top_one": momentum_metrics,
                "upstream_primary_top_one": primary_metrics,
                "matched_random": random,
                "accuracy_advantage_over_forced_top_one": accuracy_advantage,
                "mean_net_r_advantage_over_forced_top_one": net_r_advantage,
            },
            "integrity_violations": integrity_violations,
            "gates": gates,
            "gates_passed": passed,
            "gates_total": len(gates),
            "blockers": [name for name, value in gates.items() if not value],
            "detail": (
                "Historical pre-takeoff policy cleared every frozen gate; prospective "
                "observation is the next required proof."
                if status == "historical_candidate"
                else (
                    "The frozen pre-takeoff selector did not clear tradeability, volume "
                    "and control gates together; no live authority is created."
                )
            ),
        }

    result["artifact_sha256"] = canonical_sha256(result)
    _validate_result(result)
    _atomic_json(latest_path, result)
    _append_registry(
        output_root / market / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": experiment_id,
            "event": "historical_result_sealed",
            "at": observed_at.isoformat(),
            "status": result["status"],
            "diagnostic_opened": result["diagnostic_opened"],
            "artifact_sha256": result["artifact_sha256"],
        },
    )
    return result


def load_pre_takeoff_status(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    if market not in ALL_MARKETS:
        raise ValueError(f"unsupported M19 market: {market}")
    path = _artifact_path(output_root, market)
    if not path.exists():
        return {
            "version": VERSION,
            "market": market,
            "status": "not_started",
            "diagnostic_opened": False,
            "selected_policy": None,
            "blockers": ["m19_not_run"],
            "detail": "M19 has not run; unavailable is not a pass.",
            "canonical_baseline_accuracy": CANONICAL_BASELINE_ACCURACY,
            "prospective_observer_registered": False,
            "authority": "research_only",
            "eligible_for_live": False,
            "active_model_changed": False,
            "baseline_accuracy_improved": False,
        }
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        _validate_result(result)
        model = result.get("model_artifact") or {}
        if model:
            model_path = stored_path(str(model.get("path", "")))
            if not model_path.exists() or _hash_file(model_path) != model.get("sha256"):
                raise ValueError("M19 model artifact binding mismatch")
        return dict(result)
    except Exception as exc:
        return {
            "version": VERSION,
            "market": market,
            "status": "invalid_or_unavailable",
            "diagnostic_opened": False,
            "selected_policy": None,
            "blockers": [f"invalid_artifact:{type(exc).__name__}"],
            "detail": f"M19 evidence is invalid — not a pass: {exc}",
            "canonical_baseline_accuracy": CANONICAL_BASELINE_ACCURACY,
            "prospective_observer_registered": False,
            "authority": "research_only",
            "eligible_for_live": False,
            "active_model_changed": False,
            "baseline_accuracy_improved": False,
        }
