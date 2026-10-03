"""Preregistered setup-specific stability and selective-accuracy validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.prediction.selective import (
    AccuracySelectorPolicy,
    accuracy_coverage_curve,
    select_accuracy_operating_point,
)
from tradedesk_lab.accuracy_geometry import (
    chronological_partition,
    prepare_calls,
    quick_geometry_records,
    wilson_lower_bound,
)
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.clean_dataset import FEATURES
from tradedesk_lab.dataset import Dataset


@dataclass(frozen=True)
class SetupHypothesis:
    name: str
    setup: str
    entry_mode: str
    stop_atr: float
    target_r: float
    max_hold: int


@dataclass(frozen=True)
class SetupStabilityProtocol:
    version: str = "accuracy-setup-stability-v1"
    hypotheses: tuple[SetupHypothesis, ...] = (
        SetupHypothesis(
            "primary_trend_pullback",
            "trend_pullback",
            "next_session_open",
            1.0,
            0.5,
            3,
        ),
        SetupHypothesis(
            "secondary_base_breakout",
            "base_breakout",
            "next_session_open",
            1.25,
            0.5,
            3,
        ),
    )
    final_test_frac: float = 0.20
    n_folds: int = 4
    embargo_sessions: int = 10
    stability_min_calls: int = 500
    stability_min_accuracy: float = 0.75
    stability_min_wilson: float = 0.70
    stability_min_expectancy_r: float = 0.0
    stability_min_fold_accuracy: float = 0.70
    stability_min_positive_folds: int = 3
    selector_thresholds: tuple[float, ...] = (
        0.50,
        0.55,
        0.60,
        0.65,
        0.70,
        0.75,
        0.80,
        0.85,
        0.90,
    )
    selector_top_ks: tuple[int, ...] = (1, 2, 3)

    @property
    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_SETUP_STABILITY_PROTOCOL = SetupStabilityProtocol()


def global_walk_forward_masks(
    armed_dates: pd.Series,
    global_sessions: list[date],
    *,
    n_folds: int,
    embargo_sessions: int,
) -> list[tuple[np.ndarray, np.ndarray, date, date]]:
    sessions = np.asarray(sorted(set(global_sessions)), dtype=object)
    if len(sessions) < n_folds + 1:
        return []
    blocks = np.array_split(sessions[len(sessions) // (n_folds + 1) :], n_folds)
    row_dates = np.asarray([pd.Timestamp(value).date() for value in armed_dates], dtype=object)
    folds = []
    for block in blocks:
        if not len(block):
            continue
        start, end = block[0], block[-1]
        start_position = int(np.searchsorted(sessions, start))
        cutoff_position = start_position - embargo_sessions
        if cutoff_position <= 0:
            continue
        cutoff = sessions[cutoff_position - 1]
        train = np.flatnonzero(row_dates <= cutoff)
        test = np.flatnonzero((row_dates >= start) & (row_dates <= end))
        if len(train) and len(test):
            folds.append((train, test, start, end))
    return folds


def outcome_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "n": 0,
            "wins": 0,
            "accuracy": 0.0,
            "wilson_lower_bound": 0.0,
            "expectancy_r": 0.0,
            "active_sessions": 0,
            "sessions_meeting_target": 0,
            "session_target_rate": 0.0,
        }
    session_rates = frame.groupby("armed_on")["label"].mean()
    wins = int(frame["label"].sum())
    return {
        "n": len(frame),
        "wins": wins,
        "accuracy": float(frame["label"].mean()),
        "wilson_lower_bound": wilson_lower_bound(wins, len(frame)),
        "expectancy_r": float(frame["net_r"].mean()),
        "active_sessions": len(session_rates),
        "sessions_meeting_target": int((session_rates >= 0.80).sum()),
        "session_target_rate": float((session_rates >= 0.80).mean()),
    }


def stability_failures(
    aggregate: dict[str, Any],
    folds: list[dict[str, Any]],
    protocol: SetupStabilityProtocol,
) -> list[str]:
    failures = []
    if aggregate["n"] < protocol.stability_min_calls:
        failures.append("sample_size")
    if aggregate["accuracy"] < protocol.stability_min_accuracy:
        failures.append("aggregate_accuracy")
    if aggregate["wilson_lower_bound"] < protocol.stability_min_wilson:
        failures.append("aggregate_wilson")
    if aggregate["expectancy_r"] < protocol.stability_min_expectancy_r:
        failures.append("aggregate_expectancy")
    if len(folds) != protocol.n_folds:
        failures.append("fold_count")
    elif any(
        fold["accuracy"] < protocol.stability_min_fold_accuracy for fold in folds
    ):
        failures.append("fold_accuracy")
    if sum(fold["expectancy_r"] >= 0 for fold in folds) < protocol.stability_min_positive_folds:
        failures.append("positive_fold_count")
    return failures


def _model(seed: int = 20260101) -> Any:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.1, max_iter=2000, random_state=seed),
    )


def _best_adequately_sampled(
    curve: list[dict[str, Any]], policy: AccuracySelectorPolicy
) -> dict[str, Any] | None:
    rows = [
        row
        for row in curve
        if row["n_selected"] >= policy.min_calls
        and row["active_sessions"] >= policy.min_active_sessions
        and row["session_coverage"] >= policy.min_session_coverage
    ]
    return max(
        rows,
        key=lambda row: (
            row["wilson_lower_bound"],
            row["observed_success"],
            row["expectancy_r"] if row["expectancy_r"] is not None else -999.0,
        ),
        default=None,
    )


def _hypothesis_frame(
    dataset: Dataset,
    source: pd.DataFrame,
    hypothesis: SetupHypothesis,
    *,
    slippage_pct: float,
) -> pd.DataFrame:
    subset = source[source["setup"] == hypothesis.setup].copy()
    calls = prepare_calls(
        dataset,
        subset,
        slippage_pct=slippage_pct,
        max_hold=hypothesis.max_hold,
    )
    outcomes = quick_geometry_records(
        calls,
        entry_mode=hypothesis.entry_mode,
        stop_atr=hypothesis.stop_atr,
        target_r=hypothesis.target_r,
        max_hold=hypothesis.max_hold,
    )
    feature_frame = subset[["signal_id", *FEATURES]].copy()
    merged: pd.DataFrame = outcomes.merge(
        feature_frame, on="signal_id", validate="one_to_one"
    )
    return merged


def evaluate_hypothesis(
    dataset: Dataset,
    development: pd.DataFrame,
    global_sessions: list[date],
    hypothesis: SetupHypothesis,
    protocol: SetupStabilityProtocol,
    policy: AccuracySelectorPolicy,
    *,
    slippage_pct: float,
) -> tuple[dict[str, Any], pd.DataFrame]:
    frame = _hypothesis_frame(
        dataset, development, hypothesis, slippage_pct=slippage_pct
    )
    folds = global_walk_forward_masks(
        frame["armed_on"],
        global_sessions,
        n_folds=protocol.n_folds,
        embargo_sessions=protocol.embargo_sessions,
    )
    fold_rows = []
    for _, test, start, end in folds:
        summary = outcome_summary(frame.iloc[test])
        fold_rows.append(
            {"test_start": start.isoformat(), "test_end": end.isoformat(), **summary}
        )
    aggregate = outcome_summary(frame)
    failures = stability_failures(aggregate, fold_rows, protocol)
    stable = not failures
    result: dict[str, Any] = {
        "hypothesis": asdict(hypothesis),
        "aggregate": aggregate,
        "folds": fold_rows,
        "stability_pass": stable,
        "stability_failures": failures,
        "selector": {"status": "not_run_unstable_mechanism"},
    }
    if not stable:
        return result, frame

    probabilities: np.ndarray = np.full(len(frame), np.nan, dtype=float)
    for train, test, _, _ in folds:
        labels = frame.iloc[train]["label"].to_numpy(dtype=int)
        if len(np.unique(labels)) < 2:
            continue
        model = _model()
        model.fit(frame.iloc[train][FEATURES].to_numpy(dtype=float), labels)
        probabilities[test] = model.predict_proba(
            frame.iloc[test][FEATURES].to_numpy(dtype=float)
        )[:, 1]
    mask = np.isfinite(probabilities)
    curve = accuracy_coverage_curve(
        frame.loc[mask, "label"].to_numpy(dtype=int),
        probabilities[mask],
        frame.loc[mask, "net_r"].to_numpy(dtype=float),
        list(frame.loc[mask, "armed_on"]),
        policy=policy,
        thresholds=protocol.selector_thresholds,
        top_ks=protocol.selector_top_ks,
    )
    operating_point = select_accuracy_operating_point(curve)
    result["selector"] = {
        "status": "qualified_development" if operating_point else "abstain",
        "model": "standardized_l2_logistic_c0.1",
        "oos_rows": int(mask.sum()),
        "operating_point": operating_point,
        "best_adequately_sampled": _best_adequately_sampled(curve, policy),
        "curve": curve,
    }
    return result, frame


def run_setup_stability(
    dataset: Dataset,
    protocol: SetupStabilityProtocol = DEFAULT_SETUP_STABILITY_PROTOCOL,
    *,
    policy: AccuracySelectorPolicy | None = None,
) -> dict[str, Any]:
    from tradedesk.config import load_config
    from tradedesk.markets.market import nse_market

    policy = policy or AccuracySelectorPolicy()
    development, locked, split_at = chronological_partition(
        dataset.frame, protocol.final_test_frac
    )
    global_sessions = sorted(set(development["_armed_date"]))
    slippage = float(nse_market(load_config(ROOT)).costs.slippage_pct)
    results = []
    frames: dict[str, pd.DataFrame] = {}
    for hypothesis in protocol.hypotheses:
        result, frame = evaluate_hypothesis(
            dataset,
            development,
            global_sessions,
            hypothesis,
            protocol,
            policy,
            slippage_pct=slippage,
        )
        results.append(result)
        frames[hypothesis.name] = frame

    # Primary has frozen precedence. Secondary is eligible only when primary abstains.
    nominee = next(
        (
            row
            for row in results
            if row["selector"].get("operating_point") is not None
        ),
        None,
    )
    artifact: dict[str, Any] = {
        "created_at": datetime.now(IST).isoformat(),
        "status": "abstain" if nominee is None else "development_pass",
        "protocol": asdict(protocol) | {"sha256": protocol.sha256},
        "selector_policy": asdict(policy),
        "development": {
            "rows": len(development),
            "sessions": len(global_sessions),
            "split_before": split_at.isoformat(),
            "historical_research": True,
        },
        "hypotheses": results,
        "nominee": None,
        "locked_test": {"status": "not_opened_no_nominee", "rows": len(locked)},
        "detail": "no setup-specific selector cleared every development gate",
    }
    if nominee is None:
        return artifact

    hypothesis = SetupHypothesis(**nominee["hypothesis"])
    operating = nominee["selector"]["operating_point"]
    dev_frame = frames[hypothesis.name]
    locked_frame = _hypothesis_frame(
        dataset, locked, hypothesis, slippage_pct=slippage
    )
    model = _model()
    model.fit(
        dev_frame[FEATURES].to_numpy(dtype=float),
        dev_frame["label"].to_numpy(dtype=int),
    )
    locked_p = model.predict_proba(locked_frame[FEATURES].to_numpy(dtype=float))[:, 1]
    curve = accuracy_coverage_curve(
        locked_frame["label"].to_numpy(dtype=int),
        locked_p,
        locked_frame["net_r"].to_numpy(dtype=float),
        list(locked_frame["armed_on"]),
        policy=policy,
        thresholds=(float(operating["threshold"]),),
        top_ks=(int(operating["top_k"]),),
    )
    artifact["nominee"] = {
        "hypothesis": asdict(hypothesis),
        "development_operating_point": operating,
    }
    locked_status = "locked_pass" if curve[0]["qualified"] else "locked_fail"
    artifact["locked_test"] = {"status": locked_status, **curve[0]}
    artifact["status"] = locked_status
    artifact["detail"] = "primary-precedence nominee evaluated once on locked tail"
    return artifact


def save_setup_stability(result: dict[str, Any], output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
    path = output / f"{timestamp}.json"
    payload = json.dumps(result, indent=2, allow_nan=False, default=str)
    path.write_text(payload, encoding="utf-8")
    (output / "latest.json").write_text(payload, encoding="utf-8")
    return path
