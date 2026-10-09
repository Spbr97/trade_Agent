"""M16 execution-aligned two-stage selector and forward-only evidence collector.

Historical M16 output is development evidence because its 180-session window overlaps
M15's consumed experiment.  A model can only earn a forward observation cohort when all
preregistered development sanity gates pass.  Even then this module cannot create a live
call or change the active model: it writes isolated, hash-bound research evidence only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from tradedesk.broker.indstocks.models import IST
from tradedesk.leader_discovery import (
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    LABEL_VERSION,
    extract_causal_universe,
    extract_decision_universe,
    spec_for,
)
from tradedesk.leader_separability import (
    DB_PATHS,
    SESSION_COUNT,
    _build_replay_universe,
    _load_replay_paths,
    _market_bundle,
    _model_frame,
    _replay_one,
    _split_manifest,
)
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "execution-aligned-m16-v1"
DATASET_VERSION = "execution-aligned-dataset-v1"
MODEL_VERSION = "execution-aligned-model-v1"
REGISTRATION_VERSION = "execution-aligned-registration-v1"
OBSERVATION_VERSION = "execution-aligned-observation-v1"
RESOLUTION_VERSION = "execution-aligned-resolution-v1"
SNAPSHOT_VERSION = "execution-aligned-decision-snapshot-v1"
PROSPECTIVE_REPLAY_VERSION = "execution-aligned-prospective-replay-v1"
PROTOCOL_PATH = Path("docs/self-learning-m16-execution-aligned-protocol.md")
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/execution_aligned_selector")

TRAIN_SESSIONS = 102
VALIDATION_SESSIONS = 36
DIAGNOSTIC_SESSIONS = 36
MIN_POLICY_SELECTIONS = 12
PROBABILITY_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
RANDOM_REPETITIONS = 2_000
RANDOM_SEED = 16
PROSPECTIVE_MIN_CALLS = 60
PROSPECTIVE_MIN_SESSIONS = 40
STAGE_TWO_FEATURES = (*FEATURE_COLUMNS, "opportunity_score", "opportunity_score_rank")


def _protocol_sha256() -> str:
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(f"frozen M16 protocol missing: {PROTOCOL_PATH}")
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_gzip_jsonl(
    directory: Path, prefix: str, rows: Iterable[Mapping[str, Any]]
) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{prefix}.tmp.gz"
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0
            ) as compressed:
                for row in rows:
                    line = json.dumps(
                        row,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    compressed.write((line + "\n").encode("utf-8"))
        digest = _hash_file(temporary)
        destination = directory / f"{prefix}-{digest}.jsonl.gz"
        if destination.exists():
            if _hash_file(destination) != digest:
                raise ValueError(f"existing M16 {prefix} artifact digest mismatch")
        else:
            os.replace(temporary, destination)
        return destination, digest
    finally:
        temporary.unlink(missing_ok=True)


def _read_gzip_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"M16 gzip row {line_number} is not an object")
            rows.append(value)
    return rows


def _json_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _dataset_rows(frame: pd.DataFrame, market: str) -> Iterable[dict[str, Any]]:
    for row in frame.itertuples(index=False):
        yield {
            "version": DATASET_VERSION,
            "market": market,
            "session": row.session,
            "scrip_code": row.scrip_code,
            "symbol": row.symbol,
            "features": {
                name: _json_number(getattr(row, name)) for name in FEATURE_COLUMNS
            },
            "opportunity_label": int(row.label),
            "execution": {
                "status": row.status,
                "event": None if pd.isna(row.event) else str(row.event),
                "strict_success": bool(row.strict_success),
                "net_r": float(row.net_r),
            },
        }


def _execution_frame(
    extracted: pd.DataFrame, db_path: Path, market: str
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frame = _model_frame(extracted)
    paths = _load_replay_paths(db_path, frame)
    replay_frame, replay_records = _build_replay_universe(frame, paths, market)
    execution = replay_frame[
        [
            "session",
            "scrip_code",
            "symbol",
            "label",
            "status",
            "event",
            "strict_success",
            "net_r",
        ]
    ]
    merged = frame.merge(
        execution,
        on=["session", "scrip_code", "symbol", "label"],
        how="left",
        validate="one_to_one",
    )
    # The protocol makes bad/missing historical execution evidence a failure rather than
    # dropping a difficult row and inflating accuracy.
    merged["status"] = merged["status"].fillna("invalid_missing_path")
    merged["event"] = merged["event"].fillna("invalid")
    merged["strict_success"] = merged["strict_success"].fillna(False).astype(bool)
    merged["net_r"] = merged["net_r"].fillna(0.0).astype(float)
    merged.sort_values(["session", "scrip_code"], inplace=True)
    return merged, replay_records


def _stage_one_model() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=200,
                    max_depth=4,
                    min_samples_leaf=25,
                    max_features="sqrt",
                    class_weight="balanced_subsample",
                    random_state=16,
                    n_jobs=1,
                ),
            ),
        ]
    )


def _stage_two_classifier() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=5,
                    min_samples_leaf=25,
                    max_features="sqrt",
                    class_weight="balanced_subsample",
                    random_state=17,
                    n_jobs=1,
                ),
            ),
        ]
    )


def _stage_two_regressor() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestRegressor(
                    n_estimators=300,
                    max_depth=5,
                    min_samples_leaf=25,
                    max_features="sqrt",
                    random_state=18,
                    n_jobs=1,
                ),
            ),
        ]
    )


def _add_opportunity_scores(
    frame: pd.DataFrame, stage_one: Pipeline
) -> pd.DataFrame:
    scored = frame.copy()
    scored["opportunity_score"] = stage_one.predict_proba(
        scored[list(FEATURE_COLUMNS)]
    )[:, 1]
    scored["opportunity_score_rank"] = scored.groupby("session")[
        "opportunity_score"
    ].rank(method="average", pct=True)
    return scored


def _fit_pipeline(frame: pd.DataFrame) -> dict[str, Any]:
    stage_one = _stage_one_model()
    stage_one.fit(frame[list(FEATURE_COLUMNS)], frame["label"].to_numpy(dtype=int))
    scored = _add_opportunity_scores(frame, stage_one)
    classifier = _stage_two_classifier()
    regressor = _stage_two_regressor()
    classifier.fit(
        scored[list(STAGE_TWO_FEATURES)],
        scored["strict_success"].to_numpy(dtype=int),
    )
    regressor.fit(
        scored[list(STAGE_TWO_FEATURES)], scored["net_r"].to_numpy(dtype=float)
    )
    return {
        "stage_one": stage_one,
        "classifier": classifier,
        "regressor": regressor,
    }


def _score_pipeline(frame: pd.DataFrame, models: Mapping[str, Any]) -> pd.DataFrame:
    scored = _add_opportunity_scores(frame, models["stage_one"])
    features = scored[list(STAGE_TWO_FEATURES)]
    scored["trade_probability"] = models["classifier"].predict_proba(features)[:, 1]
    scored["predicted_net_r"] = models["regressor"].predict(features)
    return scored


def _candidate_pool(scored: pd.DataFrame) -> pd.DataFrame:
    ranked = scored.sort_values(
        ["session", "opportunity_score", "scrip_code"],
        ascending=[True, False, True],
    ).copy()
    within = ranked.groupby("session").cumcount()
    sizes = ranked.groupby("session")["scrip_code"].transform("size")
    limits = np.minimum(20, np.maximum(3, np.ceil(sizes * 0.01))).astype(int)
    return ranked[within < limits].copy()


def _select_policy(scored: pd.DataFrame, threshold: float) -> pd.DataFrame:
    candidates = _candidate_pool(scored)
    eligible = candidates[
        (candidates["trade_probability"] >= threshold)
        & (candidates["predicted_net_r"] > 0.0)
    ].copy()
    eligible.sort_values(
        [
            "session",
            "trade_probability",
            "predicted_net_r",
            "opportunity_score",
            "scrip_code",
        ],
        ascending=[True, False, False, False, True],
        inplace=True,
    )
    return eligible[eligible.groupby("session").cumcount() == 0].copy()


def _maximum_losing_streak(successes: Sequence[bool]) -> int:
    maximum = 0
    current = 0
    for success in successes:
        current = 0 if success else current + 1
        maximum = max(maximum, current)
    return maximum


def _wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    probability = successes / total
    denominator = 1.0 + z * z / total
    centre = (probability + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1.0 - probability) / total
            + z * z / (4.0 * total * total)
        )
        / denominator
    )
    return centre - margin, centre + margin


def _selection_metrics(
    selected: pd.DataFrame, *, total_sessions: int
) -> dict[str, Any]:
    ordered = selected.sort_values(["session", "scrip_code"])
    count = len(ordered)
    successes = int(ordered["strict_success"].sum()) if count else 0
    lower, upper = _wilson_interval(successes, count)
    events = Counter(str(value) for value in ordered.get("event", []))
    return {
        "selected": count,
        "selected_sessions": int(ordered["session"].nunique()) if count else 0,
        "total_sessions": total_sessions,
        "no_call_sessions": total_sessions
        - (int(ordered["session"].nunique()) if count else 0),
        "no_call_frequency": (
            1.0 - int(ordered["session"].nunique()) / total_sessions
            if total_sessions
            else None
        ),
        "strict_successes": successes,
        "strict_accuracy": successes / count if count else None,
        "wilson_95_lower": lower,
        "wilson_95_upper": upper,
        "mean_net_r": float(ordered["net_r"].mean()) if count else None,
        "median_net_r": float(ordered["net_r"].median()) if count else None,
        "maximum_losing_streak": _maximum_losing_streak(
            [bool(value) for value in ordered["strict_success"]]
        ),
        "events": dict(sorted(events.items())),
    }


def _top_one(frame: pd.DataFrame, score_column: str, sessions: set[str]) -> pd.DataFrame:
    subset = frame[frame["session"].isin(sessions)].copy()
    subset.sort_values(
        ["session", score_column, "scrip_code"],
        ascending=[True, False, True],
        inplace=True,
    )
    return subset[subset.groupby("session").cumcount() == 0].copy()


def _matched_random_control(
    frame: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    repetitions: int = RANDOM_REPETITIONS,
    seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    selected_metrics = _selection_metrics(
        selected, total_sessions=int(frame["session"].nunique())
    )
    sessions = set(str(value) for value in selected["session"])
    groups = []
    for _, group in frame[frame["session"].isin(sessions)].groupby("session"):
        groups.append(
            (
                group["strict_success"].to_numpy(dtype=bool),
                group["net_r"].to_numpy(dtype=float),
            )
        )
    if not groups:
        return {
            "repetitions": repetitions,
            "status": "not_available",
            "detail": "No selected validation sessions; unavailable is not a pass.",
        }
    rng = np.random.default_rng(seed)
    accuracies = np.empty(repetitions, dtype=float)
    expectancies = np.empty(repetitions, dtype=float)
    for index in range(repetitions):
        chosen_success = []
        chosen_r = []
        for success, net_r in groups:
            choice = int(rng.integers(0, len(success)))
            chosen_success.append(bool(success[choice]))
            chosen_r.append(float(net_r[choice]))
        accuracies[index] = float(np.mean(chosen_success))
        expectancies[index] = float(np.mean(chosen_r))
    observed_accuracy = float(selected_metrics["strict_accuracy"] or 0.0)
    observed_net_r = float(selected_metrics["mean_net_r"] or 0.0)
    return {
        "status": "available",
        "repetitions": repetitions,
        "sessions": len(groups),
        "mean_strict_accuracy": float(accuracies.mean()),
        "mean_net_r": float(expectancies.mean()),
        "p05_net_r": float(np.quantile(expectancies, 0.05)),
        "p95_net_r": float(np.quantile(expectancies, 0.95)),
        "accuracy_tail_probability": float(
            (1 + int(np.sum(accuracies >= observed_accuracy))) / (repetitions + 1)
        ),
        "net_r_tail_probability": float(
            (1 + int(np.sum(expectancies >= observed_net_r))) / (repetitions + 1)
        ),
        "seed": seed,
    }


def _controls(frame: pd.DataFrame, selected: pd.DataFrame, seed: int) -> dict[str, Any]:
    sessions = set(str(value) for value in selected["session"])
    total = len(sessions)
    stage_one = _top_one(frame, "opportunity_score", sessions)
    momentum = _top_one(frame, "return_20_rank", sessions)
    return {
        "forced_stage_one_top_1": _selection_metrics(
            stage_one, total_sessions=total
        ),
        "momentum_top_1": _selection_metrics(momentum, total_sessions=total),
        "matched_random": _matched_random_control(
            frame, selected, seed=seed
        ),
    }


def _select_threshold(validation: pd.DataFrame) -> tuple[float | None, dict[str, Any]]:
    total_sessions = int(validation["session"].nunique())
    cards: dict[str, Any] = {}
    eligible: list[tuple[float, dict[str, Any]]] = []
    for threshold in PROBABILITY_THRESHOLDS:
        selected = _select_policy(validation, threshold)
        metrics = _selection_metrics(selected, total_sessions=total_sessions)
        key = f"{threshold:.2f}"
        cards[key] = metrics
        if (
            metrics["selected"] >= MIN_POLICY_SELECTIONS
            and metrics["selected_sessions"] >= MIN_POLICY_SELECTIONS
        ):
            eligible.append((threshold, metrics))
    if not eligible:
        return None, cards
    winner = max(
        eligible,
        key=lambda item: (
            float(item[1]["strict_accuracy"] or -1.0),
            float(item[1]["mean_net_r"] or -math.inf),
            int(item[1]["selected"]),
            item[0],
        ),
    )
    return winner[0], cards


def _feature_importance(pipeline: Pipeline, names: Sequence[str]) -> dict[str, float]:
    values = pipeline.named_steps["model"].feature_importances_
    return {
        name: float(value)
        for name, value in sorted(
            zip(names, values, strict=True),
            key=lambda pair: abs(float(pair[1])),
            reverse=True,
        )
    }


def _artifact_without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "artifact_sha256"}


def validate_development_report(report: Mapping[str, Any]) -> None:
    if report.get("version") != VERSION:
        raise ValueError("unsupported M16 report version")
    spec_for(str(report.get("market")))
    if report.get("markets_pooled") is not False:
        raise ValueError("M16 markets must never be pooled")
    if report.get("active_model_changed") is not False:
        raise ValueError("M16 cannot change the active model")
    if report.get("eligible_for_live") is not False:
        raise ValueError("M16 cannot authorize live calls")
    if report.get("baseline_accuracy_improved") is not False:
        raise ValueError("M16 development cannot improve the reported baseline")
    expected = canonical_sha256(_artifact_without_hash(report))
    if report.get("artifact_sha256") != expected:
        raise ValueError("M16 report hash mismatch")


def validate_registration(registration: Mapping[str, Any]) -> None:
    if registration.get("version") != REGISTRATION_VERSION:
        raise ValueError("unsupported M16 registration version")
    spec_for(str(registration.get("market")))
    if registration.get("authority") != "research_observation_only":
        raise ValueError("M16 registration authority mismatch")
    if registration.get("eligible_for_live") is not False:
        raise ValueError("M16 registration cannot authorize live use")
    expected = canonical_sha256(_artifact_without_hash(registration))
    if registration.get("artifact_sha256") != expected:
        raise ValueError("M16 registration hash mismatch")


def _save_model_bundle(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(dict(payload), temporary)
    os.replace(temporary, path)
    return _hash_file(path)


def _append_registry(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def run_execution_aligned_development(
    *,
    market: str,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run M16 once per market and optionally seal a prospective observation cohort."""

    spec_for(market)
    market_root = output_root / market
    latest_path = market_root / "latest.json"
    if latest_path.exists():
        existing = load_development_report(market, output_root=output_root)
        if existing.get("status") == "invalid_or_unreadable":
            raise ValueError(str(existing.get("detail")))
        return existing

    source_db = db_path or DB_PATHS[market]
    observed_at = now or datetime.now(IST)
    extracted, source = extract_causal_universe(
        source_db, market, now=observed_at, session_count=SESSION_COUNT
    )
    frame, replay_records = _execution_frame(extracted, source_db, market)
    sessions = sorted(pd.to_datetime(frame["session"]).dt.date.unique())
    split = _split_manifest(sessions)
    if (
        len(split.train) != TRAIN_SESSIONS
        or len(split.validation) != VALIDATION_SESSIONS
        or len(split.locked_test) != DIAGNOSTIC_SESSIONS
    ):
        raise AssertionError("M16 frozen split constants do not match M15 manifest")

    dataset_path, dataset_sha = _write_gzip_jsonl(
        market_root / "datasets", "execution-aligned", _dataset_rows(frame, market)
    )
    replay_path, replay_sha = _write_gzip_jsonl(
        market_root / "replays", "historical-universe", replay_records
    )
    protocol_sha = _protocol_sha256()
    experiment_id = canonical_sha256(
        {
            "version": VERSION,
            "market": market,
            "protocol_sha256": protocol_sha,
            "dataset_sha256": dataset_sha,
            "split": split.to_dict(),
        }
    )

    train = frame[frame["session"].isin(split.train)].copy()
    validation = frame[frame["session"].isin(split.validation)].copy()
    diagnostic = frame[frame["session"].isin(split.locked_test)].copy()
    development_models = _fit_pipeline(train)
    scored_validation = _score_pipeline(validation, development_models)
    scored_diagnostic = _score_pipeline(diagnostic, development_models)
    threshold, threshold_cards = _select_threshold(scored_validation)

    if threshold is None:
        validation_selected = scored_validation.iloc[0:0].copy()
        diagnostic_selected = scored_diagnostic.iloc[0:0].copy()
    else:
        validation_selected = _select_policy(scored_validation, threshold)
        diagnostic_selected = _select_policy(scored_diagnostic, threshold)
    validation_metrics = _selection_metrics(
        validation_selected, total_sessions=VALIDATION_SESSIONS
    )
    diagnostic_metrics = _selection_metrics(
        diagnostic_selected, total_sessions=DIAGNOSTIC_SESSIONS
    )
    validation_controls = _controls(
        scored_validation, validation_selected, RANDOM_SEED + 100
    )
    diagnostic_controls = _controls(
        scored_diagnostic, diagnostic_selected, RANDOM_SEED + 200
    )
    forced_validation = validation_controls["forced_stage_one_top_1"]
    accuracy_advantage = float(validation_metrics["strict_accuracy"] or 0.0) - float(
        forced_validation["strict_accuracy"] or 0.0
    )
    net_r_advantage = float(validation_metrics["mean_net_r"] or 0.0) - float(
        forced_validation["mean_net_r"] or 0.0
    )
    diagnostic_random_tail = float(
        diagnostic_controls["matched_random"].get("net_r_tail_probability", 1.0)
    )
    validation_accuracy = float(validation_metrics["strict_accuracy"] or 0.0)
    diagnostic_accuracy = float(diagnostic_metrics["strict_accuracy"] or 0.0)
    gates = {
        "validation_at_least_12_selections": validation_metrics["selected"]
        >= MIN_POLICY_SELECTIONS,
        "validation_accuracy_at_least_55pct": validation_accuracy >= 0.55,
        "validation_mean_net_r_positive": float(validation_metrics["mean_net_r"] or 0.0)
        > 0.0,
        "validation_accuracy_advantage_at_least_10pp": accuracy_advantage >= 0.10,
        "validation_net_r_advantage_at_least_010r": net_r_advantage >= 0.10,
        "diagnostic_at_least_12_selections": diagnostic_metrics["selected"]
        >= MIN_POLICY_SELECTIONS,
        "diagnostic_accuracy_at_least_50pct": diagnostic_accuracy >= 0.50,
        "diagnostic_mean_net_r_positive": float(diagnostic_metrics["mean_net_r"] or 0.0)
        > 0.0,
        "diagnostic_random_net_r_tail_at_most_010": diagnostic_random_tail <= 0.10,
        "validation_diagnostic_accuracy_gap_at_most_20pp": abs(
            validation_accuracy - diagnostic_accuracy
        )
        <= 0.20,
    }
    development_passed = threshold is not None and all(gates.values())

    development_model_path = market_root / "models" / f"{experiment_id}-development.joblib"
    development_model_sha = _save_model_bundle(
        development_model_path,
        {
            "version": MODEL_VERSION,
            "purpose": "consumed_historical_development",
            "market": market,
            "experiment_id": experiment_id,
            "protocol_sha256": protocol_sha,
            "dataset_sha256": dataset_sha,
            "features": FEATURE_COLUMNS,
            "stage_two_features": STAGE_TWO_FEATURES,
            "threshold": threshold,
            "models": development_models,
        },
    )

    final_bundle: dict[str, Any] | None = None
    registration: dict[str, Any] | None = None
    if development_passed and threshold is not None:
        final_models = _fit_pipeline(frame)
        final_model_path = market_root / "models" / f"{experiment_id}-prospective.joblib"
        final_model_sha = _save_model_bundle(
            final_model_path,
            {
                "version": MODEL_VERSION,
                "purpose": "forward_observation",
                "market": market,
                "experiment_id": experiment_id,
                "protocol_sha256": protocol_sha,
                "dataset_sha256": dataset_sha,
                "features": FEATURE_COLUMNS,
                "stage_two_features": STAGE_TWO_FEATURES,
                "threshold": threshold,
                "models": final_models,
            },
        )
        final_bundle = {
            "path": str(final_model_path),
            "sha256": final_model_sha,
            "threshold": threshold,
        }
        registration = {
            "version": REGISTRATION_VERSION,
            "market": market,
            "experiment_id": experiment_id,
            "registered_at": observed_at.isoformat(),
            "starts_strictly_after": source["latest_closed_session"],
            "protocol_sha256": protocol_sha,
            "dataset_sha256": dataset_sha,
            "model_path": str(final_model_path),
            "model_sha256": final_model_sha,
            "threshold": threshold,
            "minimum_calls": PROSPECTIVE_MIN_CALLS,
            "minimum_sessions": PROSPECTIVE_MIN_SESSIONS,
            "authority": "research_observation_only",
            "eligible_for_live": False,
        }
        registration["cohort_id"] = canonical_sha256(registration)
        registration["artifact_sha256"] = canonical_sha256(registration)
        validate_registration(registration)
        registration_path = market_root / "registration.json"
        if registration_path.exists():
            existing_registration = json.loads(
                registration_path.read_text(encoding="utf-8")
            )
            validate_registration(existing_registration)
            if existing_registration.get("cohort_id") != registration["cohort_id"]:
                raise ValueError("a different M16 cohort is already registered")
        else:
            _atomic_json(registration_path, registration)

    report: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": experiment_id,
        "generated_at": observed_at.isoformat(),
        "status": "prospective_registered" if development_passed else "development_rejected",
        "evidence_class": "consumed_historical_development",
        "source": {
            **source,
            "db_path": str(source_db),
            "dataset_path": str(dataset_path),
            "dataset_sha256": dataset_sha,
            "dataset_rows": len(frame),
            "historical_replay_path": str(replay_path),
            "historical_replay_sha256": replay_sha,
            "feature_version": FEATURE_VERSION,
            "opportunity_label_version": LABEL_VERSION,
        },
        "protocol": {
            "path": str(PROTOCOL_PATH),
            "sha256": protocol_sha,
            "threshold_grid": list(PROBABILITY_THRESHOLDS),
        },
        "split": {
            **split.to_dict(),
            "consumed_diagnostic": list(split.locked_test),
            "locked_test": None,
        },
        "selected_threshold": threshold,
        "threshold_cards": threshold_cards,
        "validation": {
            "metrics": validation_metrics,
            "controls": validation_controls,
            "accuracy_advantage_over_forced_stage_one": accuracy_advantage,
            "mean_net_r_advantage_over_forced_stage_one": net_r_advantage,
        },
        "consumed_diagnostic": {
            "metrics": diagnostic_metrics,
            "controls": diagnostic_controls,
        },
        "development_models": {
            "path": str(development_model_path),
            "sha256": development_model_sha,
            "stage_one_importance": _feature_importance(
                development_models["stage_one"], FEATURE_COLUMNS
            ),
            "tradeability_importance": _feature_importance(
                development_models["classifier"], STAGE_TWO_FEATURES
            ),
            "net_r_importance": _feature_importance(
                development_models["regressor"], STAGE_TWO_FEATURES
            ),
        },
        "development_gates": gates,
        "all_development_registration_gates_passed": development_passed,
        "prospective_model": final_bundle,
        "registration": registration,
        "active_model_changed": False,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "research_only",
        "detail": (
            "Historical evidence is consumed development only. A registered cohort must "
            "still accumulate fresh forward outcomes before improvement can be reviewed."
            if development_passed
            else "The frozen development registration gates failed; no prospective M16 "
            "selector was activated and unavailable evidence is not a pass."
        ),
    }
    report["artifact_sha256"] = canonical_sha256(report)
    validate_development_report(report)
    experiment_path = market_root / "experiments" / f"{experiment_id}.json"
    _atomic_json(experiment_path, report)
    _atomic_json(latest_path, report)
    _append_registry(
        market_root / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": experiment_id,
            "dataset_sha256": dataset_sha,
            "protocol_sha256": protocol_sha,
            "status": report["status"],
            "report_path": str(experiment_path),
            "report_sha256": report["artifact_sha256"],
            "registered_at": observed_at.isoformat(),
        },
    )
    return report


def load_development_report(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    spec_for(market)
    unavailable = {
        "version": VERSION,
        "market": market,
        "status": "not_available",
        "markets_pooled": False,
        "active_model_changed": False,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "research_only",
        "detail": "No M16 development result exists — unavailable is not a pass.",
    }
    path = output_root / market / "latest.json"
    if not path.exists():
        return unavailable
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("M16 latest report is not an object")
        validate_development_report(report)
        bindings = [
            (report["source"]["dataset_path"], report["source"]["dataset_sha256"]),
            (
                report["source"]["historical_replay_path"],
                report["source"]["historical_replay_sha256"],
            ),
            (
                report["development_models"]["path"],
                report["development_models"]["sha256"],
            ),
        ]
        if report.get("prospective_model"):
            bindings.append(
                (
                    report["prospective_model"]["path"],
                    report["prospective_model"]["sha256"],
                )
            )
        for artifact, expected in bindings:
            artifact_path = Path(str(artifact))
            if not artifact_path.exists() or _hash_file(artifact_path) != expected:
                raise ValueError(f"bound M16 artifact mismatch: {artifact_path}")
        return report
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            **unavailable,
            "status": "invalid_or_unreadable",
            "detail": f"M16 development result is invalid — not a pass: {exc}",
        }


def _load_registration(
    market: str, output_root: Path
) -> tuple[dict[str, Any], Path]:
    path = output_root / market / "registration.json"
    if not path.exists():
        raise FileNotFoundError(f"M16 registration missing for {market}")
    registration = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(registration, dict):
        raise ValueError("M16 registration is not an object")
    validate_registration(registration)
    model_path = Path(str(registration["model_path"]))
    if not model_path.exists() or _hash_file(model_path) != registration["model_sha256"]:
        raise ValueError("M16 registered model binding mismatch")
    if _protocol_sha256() != registration["protocol_sha256"]:
        raise ValueError("M16 registered protocol binding mismatch")
    return registration, path


def _load_chain(
    path: Path,
    *,
    market: str,
    cohort_id: str,
    version: str,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    previous: str | None = None
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"M16 chain line {line_number} is not an object")
        digest = value.get("record_sha256")
        payload = {key: item for key, item in value.items() if key != "record_sha256"}
        if (
            value.get("version") != version
            or value.get("market") != market
            or value.get("cohort_id") != cohort_id
            or value.get("previous_sha256") != previous
            or not isinstance(digest, str)
            or canonical_sha256(payload) != digest
        ):
            raise ValueError(f"M16 chain integrity failure at line {line_number}")
        records.append(value)
        previous = digest
    return records


def _append_chain(
    path: Path,
    payload: Mapping[str, Any],
    existing: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    record = {
        **payload,
        "previous_sha256": (
            str(existing[-1]["record_sha256"]) if existing else None
        ),
    }
    record["record_sha256"] = canonical_sha256(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _load_prospective_model(registration: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(registration["model_path"]))
    if _hash_file(path) != registration["model_sha256"]:
        raise ValueError("M16 prospective model digest mismatch")
    bundle = joblib.load(path)
    if (
        not isinstance(bundle, dict)
        or bundle.get("version") != MODEL_VERSION
        or bundle.get("purpose") != "forward_observation"
        or bundle.get("experiment_id") != registration["experiment_id"]
        or bundle.get("threshold") != registration["threshold"]
    ):
        raise ValueError("M16 prospective model metadata mismatch")
    return bundle


def _decision_frame(extracted: pd.DataFrame) -> pd.DataFrame:
    required = {"session_date", "scrip_code", "symbol", "close", *FEATURE_COLUMNS}
    missing = required - set(extracted.columns)
    if missing:
        raise ValueError("M16 decision source missing columns: " + ", ".join(sorted(missing)))
    frame = extracted[
        ["session_date", "scrip_code", "symbol", "close", *FEATURE_COLUMNS]
    ].copy()
    frame["session"] = frame["session_date"].map(
        lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]
    )
    frame["label"] = 0
    frame["status"] = "prospective_unresolved"
    frame["event"] = "pending"
    frame["strict_success"] = False
    frame["net_r"] = 0.0
    frame.sort_values(["session", "scrip_code"], inplace=True)
    if frame["session"].nunique() != 1:
        raise ValueError("M16 prospective decision frame must contain exactly one session")
    if frame.duplicated(["session", "scrip_code"]).any():
        raise ValueError("duplicate M16 prospective decision row")
    return frame[
        [
            "session",
            "scrip_code",
            "symbol",
            "close",
            *FEATURE_COLUMNS,
            "label",
            "status",
            "event",
            "strict_success",
            "net_r",
        ]
    ]


def _snapshot_payloads(
    scored: pd.DataFrame, market: str
) -> Iterable[dict[str, Any]]:
    candidate_keys = {
        (str(row.session), str(row.scrip_code))
        for row in _candidate_pool(scored).itertuples(index=False)
    }
    for row in scored.sort_values(["session", "scrip_code"]).itertuples(index=False):
        features = {
            name: _json_number(getattr(row, name)) for name in FEATURE_COLUMNS
        }
        yield {
            "version": SNAPSHOT_VERSION,
            "market": market,
            "session": row.session,
            "scrip_code": row.scrip_code,
            "symbol": row.symbol,
            "decision_close": _json_number(row.close),
            "features": features,
            "causal_features_sha256": canonical_sha256(features),
            "scores": {
                "opportunity": float(row.opportunity_score),
                "opportunity_rank": float(row.opportunity_score_rank),
                "trade_probability": float(row.trade_probability),
                "predicted_net_r": float(row.predicted_net_r),
            },
            "in_candidate_pool": (str(row.session), str(row.scrip_code))
            in candidate_keys,
        }


def _snapshot_to_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    flattened = []
    for row in rows:
        features = row.get("features")
        scores = row.get("scores")
        if not isinstance(features, Mapping) or not isinstance(scores, Mapping):
            raise ValueError("M16 decision snapshot has invalid feature/score payload")
        flattened.append(
            {
                "session": row["session"],
                "scrip_code": row["scrip_code"],
                "symbol": row["symbol"],
                "close": row["decision_close"],
                **{name: features.get(name) for name in FEATURE_COLUMNS},
                "opportunity_score": scores["opportunity"],
                "opportunity_score_rank": scores["opportunity_rank"],
                "trade_probability": scores["trade_probability"],
                "predicted_net_r": scores["predicted_net_r"],
                "label": 0,
                "strict_success": False,
                "net_r": 0.0,
                "status": "prospective_unresolved",
                "event": "pending",
            }
        )
    frame = pd.DataFrame(flattened)
    if frame.empty or frame["session"].nunique() != 1:
        raise ValueError("M16 decision snapshot must contain one non-empty session")
    return frame


def _validate_snapshot(path: Path, expected_sha: str, market: str) -> list[dict[str, Any]]:
    if not path.exists() or _hash_file(path) != expected_sha:
        raise ValueError(f"M16 prospective snapshot binding mismatch: {path}")
    rows = _read_gzip_jsonl(path)
    if not rows or any(
        row.get("version") != SNAPSHOT_VERSION or row.get("market") != market
        for row in rows
    ):
        raise ValueError("M16 prospective snapshot content mismatch")
    return rows


def _prospective_paths(
    market_root: Path,
) -> tuple[Path, Path]:
    return market_root / "observations.jsonl", market_root / "resolutions.jsonl"


def _replay_snapshot(
    snapshot: pd.DataFrame,
    paths: pd.DataFrame,
    market: str,
) -> list[dict[str, Any]]:
    joined = snapshot.merge(
        paths.drop(columns=["session_date"]),
        on=["session", "scrip_code"],
        how="left",
        validate="one_to_one",
        suffixes=("_decision", ""),
    )
    bundle = _market_bundle(market)
    records: list[dict[str, Any]] = []
    for row in joined.to_dict(orient="records"):
        replay = _replay_one(row, market, bundle)
        records.append(
            {
                "version": PROSPECTIVE_REPLAY_VERSION,
                "market": market,
                "session": row["session"],
                "scrip_code": row["scrip_code"],
                "symbol": row["symbol"],
                **replay,
            }
        )
    return records


def _resolve_pending(
    *,
    market: str,
    db_path: Path,
    market_root: Path,
    registration: Mapping[str, Any],
    observed_at: datetime,
) -> list[dict[str, Any]]:
    observation_path, resolution_path = _prospective_paths(market_root)
    observations = _load_chain(
        observation_path,
        market=market,
        cohort_id=str(registration["cohort_id"]),
        version=OBSERVATION_VERSION,
    )
    resolutions = _load_chain(
        resolution_path,
        market=market,
        cohort_id=str(registration["cohort_id"]),
        version=RESOLUTION_VERSION,
    )
    resolved_sessions = {str(row["session"]) for row in resolutions}
    for observation in observations:
        selected = observation.get("selected")
        session = str(observation["session"])
        if not isinstance(selected, Mapping) or session in resolved_sessions:
            continue
        snapshot_rows = _validate_snapshot(
            Path(str(observation["snapshot_path"])),
            str(observation["snapshot_sha256"]),
            market,
        )
        snapshot = _snapshot_to_frame(snapshot_rows)
        paths = _load_replay_paths(db_path, snapshot)
        selected_path = paths[
            (paths["session"] == session)
            & (paths["scrip_code"] == selected["scrip_code"])
        ]
        if selected_path.empty or pd.isna(selected_path.iloc[0].get("close_3")):
            continue
        replay_rows = _replay_snapshot(snapshot, paths, market)
        replay_path, replay_sha = _write_gzip_jsonl(
            market_root / "prospective_replays",
            f"session-{session}",
            replay_rows,
        )
        selected_replays = [
            row
            for row in replay_rows
            if row["scrip_code"] == selected["scrip_code"]
        ]
        if len(selected_replays) != 1:
            raise ValueError("M16 selected prospective replay is not unique")
        outcome = selected_replays[0]
        payload = {
            "version": RESOLUTION_VERSION,
            "market": market,
            "cohort_id": registration["cohort_id"],
            "experiment_id": registration["experiment_id"],
            "resolved_at": observed_at.isoformat(),
            "session": session,
            "scrip_code": selected["scrip_code"],
            "symbol": selected["symbol"],
            "observation_sha256": observation["record_sha256"],
            "snapshot_sha256": observation["snapshot_sha256"],
            "replay_path": str(replay_path),
            "replay_sha256": replay_sha,
            "status": outcome["status"],
            "event": outcome.get("event", "invalid"),
            "strict_success": bool(outcome.get("strict_success", False)),
            "net_r": float(outcome.get("net_r", 0.0)),
        }
        record = _append_chain(resolution_path, payload, resolutions)
        resolutions.append(record)
        resolved_sessions.add(session)
    return resolutions


def _prospective_random_control(
    resolutions: Sequence[Mapping[str, Any]],
    *,
    market: str,
    observed_accuracy: float,
    observed_net_r: float,
) -> dict[str, Any]:
    groups: list[tuple[np.ndarray, np.ndarray]] = []
    for resolution in resolutions:
        replay_path = Path(str(resolution["replay_path"]))
        if not replay_path.exists() or _hash_file(replay_path) != resolution["replay_sha256"]:
            raise ValueError(f"M16 prospective replay binding mismatch: {replay_path}")
        rows = _read_gzip_jsonl(replay_path)
        if not rows or any(
            row.get("version") != PROSPECTIVE_REPLAY_VERSION
            or row.get("market") != market
            for row in rows
        ):
            raise ValueError("M16 prospective replay content mismatch")
        groups.append(
            (
                np.array(
                    [bool(row.get("strict_success", False)) for row in rows],
                    dtype=bool,
                ),
                np.array([float(row.get("net_r", 0.0)) for row in rows], dtype=float),
            )
        )
    if not groups:
        return {
            "status": "not_available",
            "repetitions": RANDOM_REPETITIONS,
            "detail": "No resolved prospective selections; unavailable is not a pass.",
        }
    rng = np.random.default_rng(RANDOM_SEED + 300)
    accuracies = np.empty(RANDOM_REPETITIONS, dtype=float)
    expectancies = np.empty(RANDOM_REPETITIONS, dtype=float)
    for index in range(RANDOM_REPETITIONS):
        successes = []
        net_values = []
        for success, net_r in groups:
            choice = int(rng.integers(0, len(success)))
            successes.append(bool(success[choice]))
            net_values.append(float(net_r[choice]))
        accuracies[index] = float(np.mean(successes))
        expectancies[index] = float(np.mean(net_values))
    return {
        "status": "available",
        "repetitions": RANDOM_REPETITIONS,
        "sessions": len(groups),
        "mean_strict_accuracy": float(accuracies.mean()),
        "mean_net_r": float(expectancies.mean()),
        "accuracy_tail_probability": float(
            (1 + int(np.sum(accuracies >= observed_accuracy)))
            / (RANDOM_REPETITIONS + 1)
        ),
        "net_r_tail_probability": float(
            (1 + int(np.sum(expectancies >= observed_net_r)))
            / (RANDOM_REPETITIONS + 1)
        ),
        "seed": RANDOM_SEED + 300,
    }


def _prospective_summary(
    *,
    market: str,
    market_root: Path,
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    observation_path, resolution_path = _prospective_paths(market_root)
    observations = _load_chain(
        observation_path,
        market=market,
        cohort_id=str(registration["cohort_id"]),
        version=OBSERVATION_VERSION,
    )
    resolutions = _load_chain(
        resolution_path,
        market=market,
        cohort_id=str(registration["cohort_id"]),
        version=RESOLUTION_VERSION,
    )
    for observation in observations:
        _validate_snapshot(
            Path(str(observation["snapshot_path"])),
            str(observation["snapshot_sha256"]),
            market,
        )
    selected = [row for row in observations if isinstance(row.get("selected"), Mapping)]
    no_calls = len(observations) - len(selected)
    resolved = len(resolutions)
    pending = len(selected) - resolved
    successes = sum(bool(row["strict_success"]) for row in resolutions)
    accuracy = successes / resolved if resolved else None
    mean_net_r = (
        float(np.mean([float(row["net_r"]) for row in resolutions]))
        if resolved
        else None
    )
    lower, upper = _wilson_interval(successes, resolved)
    random = _prospective_random_control(
        resolutions,
        market=market,
        observed_accuracy=float(accuracy or 0.0),
        observed_net_r=float(mean_net_r or 0.0),
    )
    ordered_success = [
        bool(row["strict_success"])
        for row in sorted(resolutions, key=lambda item: str(item["session"]))
    ]
    sessions = {str(row["session"]) for row in resolutions}
    qualification_ready = (
        resolved >= PROSPECTIVE_MIN_CALLS
        and len(sessions) >= PROSPECTIVE_MIN_SESSIONS
    )
    gates = {
        "at_least_60_resolved": resolved >= PROSPECTIVE_MIN_CALLS,
        "at_least_40_sessions": len(sessions) >= PROSPECTIVE_MIN_SESSIONS,
        "strict_accuracy_at_least_70pct": float(accuracy or 0.0) >= 0.70,
        "wilson_lower_at_least_55pct": float(lower or 0.0) >= 0.55,
        "mean_net_r_positive": float(mean_net_r or 0.0) > 0.0,
        "random_accuracy_tail_at_most_005": float(
            random.get("accuracy_tail_probability", 1.0)
        )
        <= 0.05,
        "random_net_r_tail_at_most_005": float(
            random.get("net_r_tail_probability", 1.0)
        )
        <= 0.05,
        "maximum_losing_streak_at_most_6": _maximum_losing_streak(ordered_success)
        <= 6,
        "integrity_valid": True,
    }
    passed = qualification_ready and all(gates.values())
    return {
        "status": (
            "qualification_passed_for_review"
            if passed
            else "qualification_failed"
            if qualification_ready
            else "collecting"
        ),
        "cohort_id": registration["cohort_id"],
        "starts_strictly_after": registration["starts_strictly_after"],
        "observed_sessions": len(observations),
        "selected_calls": len(selected),
        "no_call_sessions": no_calls,
        "no_call_frequency": no_calls / len(observations) if observations else None,
        "resolved_calls": resolved,
        "pending_calls": pending,
        "resolved_sessions": len(sessions),
        "strict_successes": successes,
        "strict_accuracy": accuracy,
        "wilson_95_lower": lower,
        "wilson_95_upper": upper,
        "mean_net_r": mean_net_r,
        "maximum_losing_streak": _maximum_losing_streak(ordered_success),
        "progress_to_70pct": accuracy,
        "stretch_80pct_reached": bool(accuracy is not None and accuracy >= 0.80),
        "minimum_calls": PROSPECTIVE_MIN_CALLS,
        "minimum_sessions": PROSPECTIVE_MIN_SESSIONS,
        "matched_random": random,
        "qualification_gates": gates,
        "qualification_passed": passed,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "research_observation_only",
    }


def collect_execution_aligned_prospective(
    *,
    market: str,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve mature M16 selections and append one truly forward decision if available."""

    report = load_development_report(market, output_root=output_root)
    if report.get("status") != "prospective_registered":
        return {
            "market": market,
            "status": "not_registered",
            "development_status": report.get("status"),
            "detail": report.get("detail"),
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
        }
    registration, _ = _load_registration(market, output_root)
    source_db = db_path or DB_PATHS[market]
    observed_at = now or datetime.now(IST)
    market_root = output_root / market
    _resolve_pending(
        market=market,
        db_path=source_db,
        market_root=market_root,
        registration=registration,
        observed_at=observed_at,
    )

    extracted, source = extract_decision_universe(source_db, market, now=observed_at)
    session = str(source["decision_session"])
    observation_path, _ = _prospective_paths(market_root)
    observations = _load_chain(
        observation_path,
        market=market,
        cohort_id=str(registration["cohort_id"]),
        version=OBSERVATION_VERSION,
    )
    existing_sessions = {str(row["session"]) for row in observations}
    if session > str(registration["starts_strictly_after"]) and session not in existing_sessions:
        bundle = _load_prospective_model(registration)
        frame = _decision_frame(extracted)
        scored = _score_pipeline(frame, bundle["models"])
        snapshot_path, snapshot_sha = _write_gzip_jsonl(
            market_root / "decision_snapshots",
            f"session-{session}",
            _snapshot_payloads(scored, market),
        )
        selected_frame = _select_policy(scored, float(registration["threshold"]))
        selected: dict[str, Any] | None = None
        if len(selected_frame) == 1:
            row = selected_frame.iloc[0]
            features = {
                name: _json_number(row[name]) for name in FEATURE_COLUMNS
            }
            selected = {
                "scrip_code": str(row["scrip_code"]),
                "symbol": str(row["symbol"]),
                "decision_close": float(row["close"]),
                "causal_features_sha256": canonical_sha256(features),
                "opportunity_score": float(row["opportunity_score"]),
                "opportunity_score_rank": float(row["opportunity_score_rank"]),
                "trade_probability": float(row["trade_probability"]),
                "predicted_net_r": float(row["predicted_net_r"]),
            }
        payload = {
            "version": OBSERVATION_VERSION,
            "market": market,
            "cohort_id": registration["cohort_id"],
            "experiment_id": registration["experiment_id"],
            "observed_at": observed_at.isoformat(),
            "session": session,
            "source_latest_closed_session": source["latest_closed_session"],
            "universe_rows": int(len(scored)),
            "threshold": registration["threshold"],
            "model_sha256": registration["model_sha256"],
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": snapshot_sha,
            "selected": selected,
            "decision": "selected" if selected is not None else "no_call",
        }
        _append_chain(observation_path, payload, observations)

    return _prospective_summary(
        market=market, market_root=market_root, registration=registration
    )


def load_execution_aligned_status(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    """Return compact, fail-closed M16 development and prospective dashboard evidence."""

    development = load_development_report(market, output_root=output_root)
    threshold_cards = development.get("threshold_cards") or {}
    maximum_threshold_selections = max(
        (
            int(card.get("selected", 0))
            for card in threshold_cards.values()
            if isinstance(card, Mapping)
        ),
        default=0,
    )
    compact_development = {
        "status": development.get("status"),
        "experiment_id": development.get("experiment_id"),
        "selected_threshold": development.get("selected_threshold"),
        "validation": (development.get("validation") or {}).get("metrics"),
        "consumed_diagnostic": (development.get("consumed_diagnostic") or {}).get(
            "metrics"
        ),
        "gates": development.get("development_gates") or {},
        "all_gates_passed": development.get(
            "all_development_registration_gates_passed", False
        ),
        "maximum_threshold_selections": maximum_threshold_selections,
        "minimum_threshold_selections": MIN_POLICY_SELECTIONS,
        "detail": development.get("detail"),
    }
    base = {
        "version": VERSION,
        "market": market,
        "development": compact_development,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "active_model_changed": False,
        "authority": "research_only",
    }
    if development.get("status") != "prospective_registered":
        return {
            **base,
            "status": development.get("status", "not_available"),
            "prospective": {
                "status": "not_registered",
                "resolved_calls": 0,
                "minimum_calls": PROSPECTIVE_MIN_CALLS,
                "minimum_sessions": PROSPECTIVE_MIN_SESSIONS,
                "qualification_passed": False,
            },
        }
    try:
        registration, _ = _load_registration(market, output_root)
        prospective = _prospective_summary(
            market=market,
            market_root=output_root / market,
            registration=registration,
        )
        return {**base, "status": prospective["status"], "prospective": prospective}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            **base,
            "status": "invalid_or_unreadable",
            "prospective": {
                "status": "invalid_or_unreadable",
                "qualification_passed": False,
                "detail": f"M16 prospective evidence is invalid — not a pass: {exc}",
            },
        }
