"""Frozen, fail-closed accuracy-model race.

The race is deliberately separate from normal model training.  It first proves that the
cohort has the current feature schema, a transparent rule score, and complete economics.
Only then may the transparent score, regularised logistic model and a sklearn tree model
share identical purged chronological folds.  A locked tail is opened only when one
development operating point clears every accuracy-selector gate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.prediction.features import FEATURE_NAMES, FEATURE_VERSION
from tradedesk.prediction.selective import (
    AccuracySelectorPolicy,
    accuracy_coverage_curve,
    select_accuracy_operating_point,
)
from tradedesk.prediction.train import purged_walk_forward


@dataclass(frozen=True)
class AccuracyRaceProtocol:
    version: str = "accuracy-race-v1"
    seed: int = 20260101
    n_splits: int = 4
    embargo_sessions: int = 10
    final_test_frac: float = 0.20
    candidates: tuple[str, ...] = ("rule_score", "logistic", "hist_gradient_boosting")


DEFAULT_RACE_PROTOCOL = AccuracyRaceProtocol()


def _cohort_fingerprint(df: pd.DataFrame, feature_names: Sequence[str]) -> str:
    columns = [
        column
        for column in (
            "signal_id", "armed_on", "label", "realised_r", "plain_score", *feature_names
        )
        if column in df.columns
    ]
    hashed = pd.util.hash_pandas_object(df[columns], index=True).to_numpy(dtype=np.uint64)
    return hashlib.sha256(hashed.tobytes()).hexdigest()


def audit_accuracy_race_dataset(
    df: pd.DataFrame,
    protocol: AccuracyRaceProtocol = DEFAULT_RACE_PROTOCOL,
    *,
    feature_names: Sequence[str] = FEATURE_NAMES,
    feature_version: str = FEATURE_VERSION,
) -> dict[str, Any]:
    """Return a reproducible readiness manifest; missing evidence is always a blocker."""
    required_meta = {"signal_id", "armed_on", "label", "realised_r", "plain_score"}
    missing_meta = sorted(required_meta - set(df.columns))
    feature_names = list(feature_names)
    if not feature_names:
        raise ValueError("accuracy race requires at least one decision-time feature")
    if len(feature_names) != len(set(feature_names)):
        raise ValueError("accuracy race feature names must be unique")
    missing_features = sorted(set(feature_names) - set(df.columns))
    n = len(df)
    economics_complete = (
        int(pd.to_numeric(df["realised_r"], errors="coerce").notna().sum())
        if "realised_r" in df else 0
    )
    rule_score_complete = (
        int(pd.to_numeric(df["plain_score"], errors="coerce").notna().sum())
        if "plain_score" in df else 0
    )
    feature_null_rows = n
    if not missing_features and n:
        feature_null_rows = int(df[feature_names].isna().any(axis=1).sum())
    dates: list[date] = []
    if "armed_on" in df:
        dates = [pd.Timestamp(value).date() for value in df["armed_on"].dropna()]
    classes = int(df["label"].nunique()) if "label" in df else 0

    blockers: list[str] = []
    if not n:
        blockers.append("dataset is empty")
    if missing_meta:
        blockers.append(f"missing metadata columns: {', '.join(missing_meta)}")
    if missing_features:
        blockers.append(
            f"feature schema is not {feature_version}: {len(missing_features)} columns missing"
        )
    if feature_null_rows:
        blockers.append(f"{feature_null_rows} rows have missing decision-time features")
    if economics_complete != n:
        blockers.append(
            f"complete realised economics available for only {economics_complete}/{n} rows"
        )
    if rule_score_complete != n:
        blockers.append(f"transparent rule score available for only {rule_score_complete}/{n} rows")
    if classes < 2:
        blockers.append("labels do not contain both outcome classes")
    if len(set(dates)) < protocol.n_splits + 2:
        blockers.append("not enough distinct sessions for chronological folds and locked tail")

    return {
        "ready": not blockers,
        "protocol_version": protocol.version,
        "feature_version": feature_version,
        "feature_count": len(feature_names),
        "feature_names": feature_names,
        "rows": n,
        "sessions": len(set(dates)),
        "date_start": min(dates).isoformat() if dates else None,
        "date_end": max(dates).isoformat() if dates else None,
        "classes": classes,
        "economics_complete": economics_complete,
        "economics_coverage": economics_complete / n if n else 0.0,
        "rule_score_complete": rule_score_complete,
        "rule_score_coverage": rule_score_complete / n if n else 0.0,
        "missing_features": missing_features,
        "feature_null_rows": feature_null_rows,
        "fingerprint_sha256": _cohort_fingerprint(df, feature_names),
        "blockers": blockers,
    }


def _model(kind: str, seed: int) -> Any:
    if kind == "logistic":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.5, max_iter=1000, random_state=seed),
        )
    if kind == "hist_gradient_boosting":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=150,
            max_leaf_nodes=15,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=seed,
        )
    raise ValueError(f"unknown race candidate: {kind}")


def _best_observed(curve: list[dict[str, Any]]) -> dict[str, Any] | None:
    sampled = [row for row in curve if row["n_selected"] >= 20]
    return max(
        sampled,
        key=lambda row: (
            row["wilson_lower_bound"], row["observed_success"], row["n_selected"]
        ),
        default=None,
    )


def run_accuracy_race(
    df: pd.DataFrame,
    *,
    protocol: AccuracyRaceProtocol = DEFAULT_RACE_PROTOCOL,
    policy: AccuracySelectorPolicy | None = None,
    feature_names: Sequence[str] = FEATURE_NAMES,
    feature_version: str = FEATURE_VERSION,
) -> dict[str, Any]:
    """Run the frozen race or return a blocked artifact without fitting any model."""
    policy = policy or AccuracySelectorPolicy()
    feature_names = list(feature_names)
    audit = audit_accuracy_race_dataset(
        df,
        protocol,
        feature_names=feature_names,
        feature_version=feature_version,
    )
    base: dict[str, Any] = {
        "created_at": datetime.now(IST).isoformat(),
        "status": "blocked" if not audit["ready"] else "running",
        "protocol": asdict(protocol),
        "selector_policy": asdict(policy),
        "cohort": audit,
        "candidates": [],
        "nominee": None,
        "locked_test": {"status": "not_opened"},
    }
    if not audit["ready"]:
        base["detail"] = "race blocked before fitting: cohort readiness failed"
        return base

    work = df.copy()
    work["_armed_date"] = [pd.Timestamp(value).date() for value in work["armed_on"]]
    work = work.sort_values(["_armed_date", "signal_id"]).reset_index(drop=True)
    unique_dates = sorted(work["_armed_date"].unique())
    split_pos = max(
        1,
        min(
            len(unique_dates) - 1,
            int(len(unique_dates) * (1 - protocol.final_test_frac)),
        ),
    )
    split_at = unique_dates[split_pos]
    dev = work[work["_armed_date"] < split_at].reset_index(drop=True)
    final = work[work["_armed_date"] >= split_at].reset_index(drop=True)
    folds = purged_walk_forward(
        list(dev["_armed_date"]),
        n_splits=protocol.n_splits,
        embargo_sessions=protocol.embargo_sessions,
    )
    if not folds:
        base["status"] = "blocked"
        base["detail"] = "race blocked before fitting: no valid purged walk-forward folds"
        return base

    X = dev[feature_names].to_numpy(dtype=float)
    y = dev["label"].to_numpy(dtype=int)
    r = dev["realised_r"].to_numpy(dtype=float)
    sessions = list(dev["_armed_date"])
    candidate_rows: list[dict[str, Any]] = []
    for kind in protocol.candidates:
        oos_p = np.full(len(dev), np.nan)
        if kind == "rule_score":
            scores = np.clip(dev["plain_score"].to_numpy(dtype=float) / 100.0, 0.0, 1.0)
            for fold in folds:
                oos_p[fold.test_idx] = scores[fold.test_idx]
        else:
            for fold in folds:
                model = _model(kind, protocol.seed)
                if len(np.unique(y[fold.train_idx])) < 2:
                    continue
                model.fit(X[fold.train_idx], y[fold.train_idx])
                oos_p[fold.test_idx] = model.predict_proba(X[fold.test_idx])[:, 1]
        mask = np.isfinite(oos_p)
        curve = accuracy_coverage_curve(
            y[mask], oos_p[mask], r[mask], list(np.asarray(sessions, dtype=object)[mask]),
            policy=policy,
        )
        point = select_accuracy_operating_point(curve)
        row = {
            "kind": kind,
            "oos_rows": int(mask.sum()),
            "folds": len(folds),
            "operating_point": point,
            "best_observed": _best_observed(curve),
            "curve": curve,
        }
        candidate_rows.append(row)
    qualified = [row for row in candidate_rows if row["operating_point"] is not None]
    nominee = max(
        qualified,
        key=lambda row: (
            row["operating_point"]["session_coverage"],
            row["operating_point"]["n_selected"],
            row["operating_point"]["wilson_lower_bound"],
        ),
        default=None,
    )
    base["candidates"] = candidate_rows
    if nominee is None:
        base["status"] = "abstain"
        base["detail"] = "no development candidate cleared every frozen selector gate"
        base["locked_test"] = {"status": "not_opened_no_nominee", "rows": len(final)}
        return base

    kind = nominee["kind"]
    point = nominee["operating_point"]
    if kind == "rule_score":
        final_p = np.clip(final["plain_score"].to_numpy(dtype=float) / 100.0, 0.0, 1.0)
    else:
        model = _model(kind, protocol.seed)
        model.fit(X, y)
        final_p = model.predict_proba(final[feature_names].to_numpy(dtype=float))[:, 1]
    locked_curve = accuracy_coverage_curve(
        final["label"].to_numpy(dtype=int),
        final_p,
        final["realised_r"].to_numpy(dtype=float),
        list(final["_armed_date"]),
        policy=policy,
        thresholds=[float(point["threshold"])],
        top_ks=[int(point["top_k"])],
    )
    base["status"] = "locked_pass" if locked_curve[0]["qualified"] else "locked_fail"
    base["nominee"] = {"kind": kind, "development_operating_point": point}
    base["locked_test"] = {
        "status": "opened_once",
        "date_start": final["_armed_date"].min().isoformat(),
        "date_end": final["_armed_date"].max().isoformat(),
        "result": locked_curve[0],
    }
    base["detail"] = "locked tail passed" if locked_curve[0]["qualified"] else "locked tail failed"
    return base


def save_accuracy_race(result: dict[str, Any], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}.json"
    text = json.dumps(result, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    (folder / "latest.json").write_text(text, encoding="utf-8")
    return path
