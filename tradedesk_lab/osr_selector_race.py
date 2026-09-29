"""OSR Milestone 3: the frozen 12-specification causal selector race.

The race is development evidence only.  It evaluates two preregistered selector
families for each of two OSR modes and three frozen geometries.  Every prediction is
outer-walk-forward; probability calibration is itself produced by an inner causal
walk-forward with purge and embargo.  The full call population includes later
unfilled and unresolved outcomes so selection cannot condition on future execution.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT, DEFAULT_OSR_PROTOCOL
from tradedesk_lab.osr_selection_population import FEATURE_NAMES, IDENTITY_COLUMNS, OUTCOME_STATUSES
from tradedesk_lab.osr_selectors import (
    QUALIFICATION_PROBABILITY,
    SELECTOR_KINDS,
    calibrated_probabilities,
    fit_platt_calibrator,
    fit_selector,
    model_summary,
    score_selector,
    selector_registry,
)
TOP_K_POLICIES = DEFAULT_OSR_CONTRACT.top_k_policies
BASELINE_STRICT_SUCCESS_RATE = 149 / 693
BASELINE_WILSON95_LOWER = 0.18603500195342146


@dataclass(frozen=True)
class RaceSettings:
    outer_splits: int = 3
    outer_embargo_sessions: int = 10
    inner_splits: int = 3
    inner_embargo_sessions: int = 2
    matched_random_cohorts: int = 200
    seed: int = 20260929
    minimum_scored_outer_folds: int = 2
    minimum_positive_net_r_fold_share: float = 2 / 3

    def __post_init__(self) -> None:
        if self.outer_splits < 2 or self.inner_splits < 2:
            raise ValueError("OSR race requires at least two outer and inner splits")
        if self.outer_embargo_sessions < 1 or self.inner_embargo_sessions < 1:
            raise ValueError("OSR race embargoes must be positive")
        if self.matched_random_cohorts < 1:
            raise ValueError("OSR race requires at least one matched-random cohort")
        if not 0 < self.minimum_positive_net_r_fold_share <= 1:
            raise ValueError("OSR fold-stability threshold must be a proportion")


DEFAULT_RACE_SETTINGS = RaceSettings()


@dataclass(frozen=True)
class _Fold:
    train: np.ndarray
    test: np.ndarray


def _purge_and_embargo(
    frame: pd.DataFrame,
    train: np.ndarray,
    test: np.ndarray,
    calendar: pd.DatetimeIndex,
    *,
    embargo: int,
) -> np.ndarray:
    """Remove label overlap and post-test sessions from a candidate training set."""

    if not len(test):
        return train
    starts = pd.to_datetime(frame["armed_on"]).to_numpy()
    ends = pd.to_datetime(frame["label_end_date"]).to_numpy()
    intervals = sorted(zip(starts[test], ends[test], strict=True))
    merged: list[tuple[np.datetime64, np.datetime64]] = []
    for lower, upper in intervals:
        if merged and lower <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    keep = np.ones(len(train), dtype=bool)
    for lower, upper in merged:
        index = int(calendar.searchsorted(pd.Timestamp(upper), side="right")) + embargo - 1
        embargo_end = calendar[min(max(index, 0), len(calendar) - 1)].to_datetime64()
        keep &= ~((starts[train] <= upper) & (ends[train] >= lower))
        keep &= ~((starts[train] > upper) & (starts[train] <= embargo_end))
    return train[keep]


def _walk_forward(
    frame: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    splits: int,
    embargo: int,
) -> list[_Fold]:
    """Expanding chronological folds with the same-session labels purged."""

    dates = pd.to_datetime(frame["armed_on"])
    unique = np.sort(dates.unique())
    if len(unique) < splits + 2:
        return []
    blocks = np.array_split(unique, splits + 1)
    result = []
    for block in blocks[1:]:
        test = np.flatnonzero(dates.isin(block))
        cutoff = calendar.searchsorted(pd.Timestamp(block[0])) - embargo
        if cutoff <= 0:
            continue
        train = np.flatnonzero(dates < calendar[cutoff])
        train = _purge_and_embargo(frame, train, test, calendar, embargo=embargo)
        if len(train) >= 30 and len(test):
            result.append(_Fold(train=train, test=test))
    return result


@dataclass(frozen=True)
class PipelineSpec:
    id: str
    mode: str
    geometry_id: str
    selector_kind: str


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def race_registry(settings: RaceSettings = DEFAULT_RACE_SETTINGS) -> dict[str, Any]:
    result = {
        "modes": list(DEFAULT_OSR_CONTRACT.entry_modes),
        "geometry_ids": [item.id for item in DEFAULT_OSR_CONTRACT.geometries],
        "selector_registry": selector_registry(),
        "top_k_policies": list(TOP_K_POLICIES),
        "top_k_counts_as_trials": False,
        "settings": asdict(settings),
        "development_gates": {
            "minimum_observed_strict_success_rate": (
                DEFAULT_OSR_PROTOCOL.minimum_observed_strict_success_rate
            ),
            "minimum_wilson95_lower": DEFAULT_OSR_PROTOCOL.minimum_wilson95_lower,
            "minimum_resolved_selected_fills": (
                DEFAULT_OSR_PROTOCOL.minimum_resolved_selected_fills
            ),
            "minimum_active_sessions": DEFAULT_OSR_PROTOCOL.minimum_active_sessions,
            "minimum_active_session_coverage": (
                DEFAULT_OSR_PROTOCOL.minimum_active_session_coverage
            ),
            "maximum_unresolved_selected_calls": (
                DEFAULT_OSR_PROTOCOL.maximum_unresolved_selected_calls
            ),
            "minimum_mean_net_r": DEFAULT_OSR_PROTOCOL.minimum_mean_net_r,
            "minimum_random_advantage_r": DEFAULT_OSR_PROTOCOL.minimum_random_advantage_r,
            "maximum_single_symbol_win_share": (
                DEFAULT_OSR_PROTOCOL.maximum_single_symbol_win_share
            ),
            "minimum_scored_outer_folds": settings.minimum_scored_outer_folds,
            "minimum_positive_net_r_fold_share": (
                settings.minimum_positive_net_r_fold_share
            ),
        },
    }
    result["registry_sha256"] = _sha(result)
    return result


def registered_pipeline_specs() -> tuple[PipelineSpec, ...]:
    specs = tuple(
        PipelineSpec(
            id=f"{mode}__{geometry.id}__{selector_kind}",
            mode=mode,
            geometry_id=geometry.id,
            selector_kind=selector_kind,
        )
        for mode in DEFAULT_OSR_CONTRACT.entry_modes
        for geometry in DEFAULT_OSR_CONTRACT.geometries
        for selector_kind in SELECTOR_KINDS
    )
    if len(specs) != DEFAULT_OSR_PROTOCOL.initial_pipeline_trial_budget:
        raise ValueError("OSR registered pipeline specs must exactly consume the 12-trial budget")
    if len({item.id for item in specs}) != len(specs):
        raise ValueError("OSR registered pipeline identifiers must be unique")
    return specs


def _wilson95_lower(successes: int, observations: int) -> float:
    if observations == 0:
        return 0.0
    z = 1.959963985
    rate = successes / observations
    denominator = 1 + z**2 / observations
    center = rate + z**2 / (2 * observations)
    margin = z * math.sqrt(
        rate * (1 - rate) / observations + z**2 / (4 * observations**2)
    )
    return (center - margin) / denominator


def _maximum_drawdown(values: np.ndarray) -> float:
    if not len(values):
        return 0.0
    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    peaks = np.maximum.accumulate(cumulative)
    return float(abs(np.min(cumulative - peaks)))


def _evaluate_selection(selected: pd.DataFrame, eligible_sessions: set[str]) -> dict[str, Any]:
    calls = int(len(selected))
    resolved = selected.loc[selected["outcome_status"] == "resolved"]
    resolved_fills = int(len(resolved))
    wins = int(resolved["label"].sum()) if resolved_fills else 0
    active_sessions = int(selected["session"].nunique()) if calls else 0
    symbol_win_share = 0.0
    session_win_share = 0.0
    if wins:
        symbol_win_share = float(
            selected.loc[selected["label"] == 1, "scrip_code"].value_counts().iloc[0] / wins
        )
        session_win_share = float(
            selected.loc[selected["label"] == 1, "session"].value_counts().iloc[0] / wins
        )

    session_rates: list[float] = []
    if calls:
        for _session, group in selected.groupby("session", sort=False):
            resolved_group = group.loc[group["outcome_status"] == "resolved"]
            rate = float(resolved_group["label"].mean()) if len(resolved_group) else 0.0
            session_rates.append(rate)

    fold_mean_net_r: list[float] = []
    if calls and "_outer_fold" in selected:
        for _fold, group in selected.groupby("_outer_fold", sort=True):
            fold_resolved = group.loc[group["outcome_status"] == "resolved"]
            if len(fold_resolved):
                fold_mean_net_r.append(float(fold_resolved["net_r"].mean()))
    positive_fold_share = (
        sum(value > 0 for value in fold_mean_net_r) / len(fold_mean_net_r)
        if fold_mean_net_r
        else 0.0
    )
    net_r_values = resolved.sort_values(["session", "decision_at"])["net_r"].to_numpy(
        dtype=float
    )
    return {
        "selected_calls": calls,
        "resolved_fills": resolved_fills,
        "unfilled_calls": int((selected["outcome_status"] == "unfilled").sum()) if calls else 0,
        "unresolved_calls": (
            int((selected["outcome_status"] == "unresolved").sum()) if calls else 0
        ),
        "strict_wins": wins,
        "strict_success_rate": wins / resolved_fills if resolved_fills else 0.0,
        "call_success_rate": wins / calls if calls else 0.0,
        "wilson95_lower": _wilson95_lower(wins, resolved_fills),
        "fill_rate": resolved_fills / calls if calls else 0.0,
        "active_sessions": active_sessions,
        "zero_call_sessions": max(len(eligible_sessions) - active_sessions, 0),
        "active_session_coverage": (
            active_sessions / len(eligible_sessions) if eligible_sessions else 0.0
        ),
        "sessions_at_or_above_70pct": (
            sum(value >= 0.70 for value in session_rates) / len(session_rates)
            if session_rates
            else 0.0
        ),
        "sessions_at_or_above_80pct": (
            sum(value >= 0.80 for value in session_rates) / len(session_rates)
            if session_rates
            else 0.0
        ),
        "mean_net_r": float(resolved["net_r"].mean()) if resolved_fills else 0.0,
        "maximum_drawdown_r": _maximum_drawdown(net_r_values),
        "max_single_symbol_win_share": symbol_win_share,
        "max_single_session_win_share": session_win_share,
        "folds_with_resolved_calls": len(fold_mean_net_r),
        "positive_mean_net_r_fold_share": positive_fold_share,
        "fold_mean_net_r": fold_mean_net_r,
    }


def _check_development_gates(
    metrics: dict[str, Any],
    random_metrics: dict[str, Any],
    settings: RaceSettings,
) -> dict[str, Any]:
    protocol = DEFAULT_OSR_PROTOCOL
    failures = []
    if metrics["resolved_fills"] < protocol.minimum_resolved_selected_fills:
        failures.append(f"resolved_fills_below_{protocol.minimum_resolved_selected_fills}")
    if metrics["active_sessions"] < protocol.minimum_active_sessions:
        failures.append(f"active_sessions_below_{protocol.minimum_active_sessions}")
    if metrics["active_session_coverage"] < protocol.minimum_active_session_coverage:
        failures.append("active_session_coverage_below_40pct")
    if metrics["strict_success_rate"] < protocol.minimum_observed_strict_success_rate:
        failures.append("strict_success_rate_below_50pct")
    if metrics["wilson95_lower"] < protocol.minimum_wilson95_lower:
        failures.append("wilson95_lower_below_40pct")
    if metrics["unresolved_calls"] > protocol.maximum_unresolved_selected_calls:
        failures.append("unresolved_selected_calls_above_zero")
    if metrics["mean_net_r"] <= protocol.minimum_mean_net_r:
        failures.append("mean_net_r_not_positive")
    random_advantage = metrics["mean_net_r"] - random_metrics["mean_net_r"]
    if random_advantage < protocol.minimum_random_advantage_r:
        failures.append("insufficient_advantage_over_matched_random")
    if metrics["max_single_symbol_win_share"] > protocol.maximum_single_symbol_win_share:
        failures.append("single_symbol_win_concentration_above_25pct")
    if metrics["folds_with_resolved_calls"] < settings.minimum_scored_outer_folds:
        failures.append("insufficient_outer_folds_with_resolved_calls")
    if (
        metrics["positive_mean_net_r_fold_share"]
        < settings.minimum_positive_net_r_fold_share
    ):
        failures.append("positive_net_r_fold_share_below_two_thirds")
    return {
        "passed": not failures,
        "failures": tuple(failures),
        "random_advantage_r": random_advantage,
    }


def _prepare_pipeline_frame(dataset: pd.DataFrame, spec: PipelineSpec) -> pd.DataFrame:
    subset = dataset.loc[
        (dataset["mode"] == spec.mode) & (dataset["geometry_id"] == spec.geometry_id)
    ].copy()
    return subset.reset_index(drop=True)


def _cv_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    working = frame.copy()
    working["armed_on"] = pd.to_datetime(working["session"])
    working["label_end_date"] = working["armed_on"]
    calendar = pd.DatetimeIndex(sorted(working["armed_on"].unique()))
    return working, calendar


def _fit_causally_calibrated_selector(
    train: pd.DataFrame,
    selector_kind: str,
    settings: RaceSettings,
) -> tuple[Any, Any, dict[str, Any]]:
    inner, calendar = _cv_frame(train)
    folds = _walk_forward(
        inner,
        calendar,
        splits=settings.inner_splits,
        embargo=settings.inner_embargo_sessions,
    )
    raw_scores = np.full(len(inner), np.nan, dtype=float)
    boundaries = []
    for fold_index, fold in enumerate(folds):
        inner_train = inner.iloc[fold.train]
        inner_test = inner.iloc[fold.test]
        if inner_train["label"].nunique() < 2:
            continue
        model = fit_selector(
            selector_kind,
            inner_train[list(FEATURE_NAMES)],
            inner_train["label"].to_numpy(dtype=int),
        )
        raw_scores[fold.test] = score_selector(
            selector_kind, model, inner_test[list(FEATURE_NAMES)]
        )
        train_max = str(inner_train["session"].max())
        test_min = str(inner_test["session"].min())
        if train_max >= test_min:
            raise ValueError("inner selector fold is not strictly chronological")
        boundaries.append(
            {
                "fold": fold_index,
                "train_rows": int(len(inner_train)),
                "test_rows": int(len(inner_test)),
                "train_session_max": train_max,
                "test_session_min": test_min,
            }
        )
    valid = np.isfinite(raw_scores)
    calibrator = fit_platt_calibrator(
        raw_scores[valid], inner.loc[valid, "label"].to_numpy(dtype=int)
    )
    final_model = fit_selector(
        selector_kind,
        inner[list(FEATURE_NAMES)],
        inner["label"].to_numpy(dtype=int),
    )
    return final_model, calibrator, {
        "inner_folds_scored": len(boundaries),
        "calibration_rows": int(valid.sum()),
        "boundaries": boundaries,
        "fitted_model": model_summary(final_model),
        "calibrator": model_summary(calibrator),
    }


def _select_calls(scored: pd.DataFrame, *, top_k: int) -> pd.DataFrame:
    if top_k not in TOP_K_POLICIES:
        raise ValueError(f"top_k must be one of {TOP_K_POLICIES}")
    chosen = []
    for _session, group in scored.groupby("session", sort=True):
        qualifying = group.loc[
            group["selection_probability"] >= QUALIFICATION_PROBABILITY
        ].sort_values(
            ["selection_probability", "opportunity_id"],
            ascending=[False, True],
            kind="stable",
        )
        qualifying = qualifying.drop_duplicates("scrip_code", keep="first")
        chosen.append(qualifying.head(top_k))
    return pd.concat(chosen, ignore_index=False) if chosen else scored.iloc[0:0]


def _matched_random_selection(
    population: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    seed: int,
) -> pd.DataFrame:
    if selected.empty:
        return population.iloc[0:0]
    rng = np.random.default_rng(seed)
    selected_counts = selected.groupby("session").size().to_dict()
    picks = []
    for session in sorted(selected_counts):
        count = int(selected_counts[session])
        candidates = population.loc[population["session"] == session].sort_values(
            ["scrip_code", "opportunity_id"], kind="stable"
        )
        candidates = candidates.drop_duplicates("scrip_code", keep="first")
        if count > len(candidates):
            raise ValueError("matched-random population cannot match selected call count")
        positions = np.sort(rng.choice(len(candidates), size=count, replace=False))
        picks.append(candidates.iloc[positions])
    return pd.concat(picks, ignore_index=False) if picks else population.iloc[0:0]


def _summarize_random_controls(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics:
        raise ValueError("matched-random controls cannot be empty")
    scalar_keys = [
        key
        for key, value in metrics[0].items()
        if isinstance(value, (int, float)) and key != "fold_mean_net_r"
    ]
    result = {
        key: float(np.mean([float(item[key]) for item in metrics])) for key in scalar_keys
    }
    result.update(
        {
            "cohorts": len(metrics),
            "strict_success_rate_p05": float(
                np.quantile([item["strict_success_rate"] for item in metrics], 0.05)
            ),
            "strict_success_rate_p95": float(
                np.quantile([item["strict_success_rate"] for item in metrics], 0.95)
            ),
            "mean_net_r_p05": float(
                np.quantile([item["mean_net_r"] for item in metrics], 0.05)
            ),
            "mean_net_r_p95": float(
                np.quantile([item["mean_net_r"] for item in metrics], 0.95)
            ),
        }
    )
    return result


def _run_one_spec(
    dataset: pd.DataFrame,
    spec: PipelineSpec,
    *,
    settings: RaceSettings,
    keep_selected_rows: bool = False,
) -> dict[str, Any]:
    subset = _prepare_pipeline_frame(dataset, spec)
    if subset.empty:
        return {"spec_id": spec.id, "status": "no_data"}
    working, calendar = _cv_frame(subset)
    folds = _walk_forward(
        working,
        calendar,
        splits=settings.outer_splits,
        embargo=settings.outer_embargo_sessions,
    )
    if not folds:
        return {"spec_id": spec.id, "status": "insufficient_sessions_for_walk_forward"}

    oos_frames = []
    scored_frames = []
    fold_diagnostics = []
    for fold_index, fold in enumerate(folds):
        train = working.iloc[fold.train].copy()
        test = working.iloc[fold.test].copy()
        test["_outer_fold"] = fold_index
        oos_frames.append(test)
        train_max = str(train["session"].max())
        test_min = str(test["session"].min())
        if train_max >= test_min:
            raise ValueError("outer selector fold is not strictly chronological")
        diagnostic: dict[str, Any] = {
            "fold": fold_index,
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "train_session_max": train_max,
            "test_session_min": test_min,
            "scored": False,
        }
        try:
            model, calibrator, inner_diagnostic = _fit_causally_calibrated_selector(
                train, spec.selector_kind, settings
            )
            raw_scores = score_selector(
                spec.selector_kind, model, test[list(FEATURE_NAMES)]
            )
            test["selection_probability"] = calibrated_probabilities(
                calibrator, raw_scores
            )
            scored_frames.append(test)
            diagnostic.update(scored=True, inner=inner_diagnostic)
        except ValueError as exc:
            diagnostic["skip_reason"] = str(exc)
        fold_diagnostics.append(diagnostic)

    oos = pd.concat(oos_frames, ignore_index=False)
    scored = pd.concat(scored_frames, ignore_index=False) if scored_frames else oos.iloc[0:0]
    eligible_sessions = set(str(value) for value in oos["session"].unique())
    unfiltered = _evaluate_selection(oos, eligible_sessions)
    top_k_reports: dict[int, dict[str, Any]] = {}
    selected_frames: dict[int, pd.DataFrame] = {}

    for top_k in TOP_K_POLICIES:
        selected = _select_calls(scored, top_k=top_k)
        selected_frames[top_k] = selected
        metrics = _evaluate_selection(selected, eligible_sessions)
        controls = [
            _evaluate_selection(
                _matched_random_selection(
                    oos,
                    selected,
                    seed=settings.seed + top_k * 100_000 + cohort,
                ),
                eligible_sessions,
            )
            for cohort in range(settings.matched_random_cohorts)
        ]
        random_summary = _summarize_random_controls(controls)
        top_k_reports[top_k] = {
            "metrics": metrics,
            "matched_random_control": random_summary,
            "gate": _check_development_gates(metrics, random_summary, settings),
        }

    result: dict[str, Any] = {
        "spec_id": spec.id,
        "status": "evaluated",
        "mode": spec.mode,
        "geometry_id": spec.geometry_id,
        "selector_kind": spec.selector_kind,
        "outer_folds": len(folds),
        "outer_folds_scored": len(scored_frames),
        "oos_calls": int(len(oos)),
        "unfiltered_baseline": unfiltered,
        "fold_diagnostics": fold_diagnostics,
        "top_k": top_k_reports,
    }
    if keep_selected_rows:
        result["_selected_frames"] = selected_frames
    return result


def _validate_population(frame: pd.DataFrame) -> None:
    required = {
        *FEATURE_NAMES,
        "label",
        "net_r",
        "gross_r",
        "outcome_status",
        "filled",
        "outcome_reason",
        "session",
        "scrip_code",
        "mode",
        "geometry_id",
        "opportunity_id",
        "decision_at",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"OSR selection population is missing columns: {sorted(missing)}")
    if frame.duplicated(list(IDENTITY_COLUMNS)).any():
        raise ValueError("OSR selection population contains duplicate identities")
    if not set(frame["outcome_status"]).issubset(OUTCOME_STATUSES):
        raise ValueError("OSR selection population contains an unexpected outcome status")
    matrix = frame[list(FEATURE_NAMES)].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("OSR selection population feature matrix is not finite")
    if frame.loc[frame["outcome_status"] != "resolved", "label"].ne(0).any():
        raise ValueError("OSR nonresolved calls cannot be strict successes")


def run_selector_race(
    dataset: pd.DataFrame,
    *,
    settings: RaceSettings = DEFAULT_RACE_SETTINGS,
    keep_selected_rows: bool = False,
) -> dict[str, Any]:
    _validate_population(dataset)
    specs = registered_pipeline_specs()
    trials = [
        _run_one_spec(
            dataset,
            spec,
            settings=settings,
            keep_selected_rows=keep_selected_rows,
        )
        for spec in specs
    ]
    qualified = [
        {"spec_id": trial["spec_id"], "top_k": top_k}
        for trial in trials
        if trial.get("status") == "evaluated"
        for top_k in TOP_K_POLICIES
        if trial["top_k"][top_k]["gate"]["passed"]
    ]
    return {
        "trial_budget": DEFAULT_OSR_PROTOCOL.initial_pipeline_trial_budget,
        "trials_registered": len(specs),
        "trials": trials,
        "qualified_candidates": qualified,
        "stress_gates_evaluated": False,
        "evidence_class": "consumed_historical_development",
        "canonical_baseline_strict_success_rate": BASELINE_STRICT_SUCCESS_RATE,
        "canonical_baseline_wilson95_lower": BASELINE_WILSON95_LOWER,
        "registry": race_registry(settings),
    }


def selected_calls_for_spec(
    dataset: pd.DataFrame,
    spec_id: str,
    *,
    top_k: int,
    settings: RaceSettings = DEFAULT_RACE_SETTINGS,
) -> pd.DataFrame:
    if top_k not in TOP_K_POLICIES:
        raise ValueError(f"top_k must be one of {TOP_K_POLICIES}")
    spec = next((item for item in registered_pipeline_specs() if item.id == spec_id), None)
    if spec is None:
        raise ValueError(f"unregistered OSR pipeline spec id: {spec_id}")
    trial = _run_one_spec(dataset, spec, settings=settings, keep_selected_rows=True)
    if trial.get("status") != "evaluated":
        raise ValueError(f"OSR spec did not evaluate: {trial.get('status')}")
    return trial["_selected_frames"][top_k]


def _load_selection_population(
    output: Path, population_run_id: str | None
) -> tuple[str, dict[str, Any], pd.DataFrame]:
    if population_run_id is None:
        pointer = json.loads(
            (output / "osr/selection_population/latest.json").read_text(encoding="utf-8")
        )
        population_run_id = str(pointer["id"])
    folder = output / "osr/selection_population/runs" / population_run_id
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if report.get("id") != population_run_id:
        raise ValueError("OSR selection population pointer and report differ")
    if report.get("source_osr_contract_sha256") != DEFAULT_OSR_CONTRACT.sha256:
        raise ValueError("OSR selection population contract differs from the frozen contract")
    if report.get("evidence_class") != "consumed_historical_development":
        raise ValueError("OSR selection population evidence class is invalid")
    calls_path = folder / "calls.csv"
    if digest(calls_path) != report.get("calls_sha256"):
        raise ValueError("OSR selection population fingerprint differs from its report")
    frame = pd.read_csv(calls_path)
    if len(frame) != int(report["rows"]):
        raise ValueError("OSR selection population row count differs from its report")
    _validate_population(frame)
    return population_run_id, report, frame


def freeze_selector_race(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    population_run_id: str | None = None,
) -> dict[str, Any]:
    """Run the one preregistered race configuration and freeze its development result."""

    root, output = Path(root).resolve(), Path(output).resolve()
    population_run_id, population_report, frame = _load_selection_population(
        output, population_run_id
    )
    result = run_selector_race(
        frame,
        settings=DEFAULT_RACE_SETTINGS,
        keep_selected_rows=True,
    )
    run_id = uuid4().hex
    target = output / "osr/selector_race/runs" / run_id
    target.mkdir(parents=True, exist_ok=True)

    qualified_rows = []
    for trial in result["trials"]:
        selected_frames = trial.pop("_selected_frames", {})
        for candidate in result["qualified_candidates"]:
            if candidate["spec_id"] != trial["spec_id"]:
                continue
            selected = selected_frames[candidate["top_k"]].copy()
            selected.insert(0, "spec_id", candidate["spec_id"])
            selected.insert(1, "top_k", candidate["top_k"])
            qualified_rows.append(selected)
    qualified_path = target / "qualified_selected_calls.csv"
    qualified_sha256 = None
    if qualified_rows:
        pd.concat(qualified_rows, ignore_index=True).to_csv(qualified_path, index=False)
        qualified_sha256 = digest(qualified_path)

    nominated = bool(result["qualified_candidates"])
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "osr-milestone-3-selector-race-v1",
        "status": "selector_race_evaluated",
        "milestone": 3,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": True,
        "development_candidate_nominated": nominated,
        "selection_population_run_id": population_run_id,
        "selection_population_rows": int(len(frame)),
        "selection_population_sha256": population_report["calls_sha256"],
        **result,
        "qualified_selected_calls_sha256": qualified_sha256,
        "implementation_sha256": digest(Path(__file__)),
        "selector_implementation_sha256": digest(
            Path(__file__).with_name("osr_selectors.py")
        ),
        "decision": {
            "register_candidate_for_stress_replay": nominated,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "At least one preregistered development candidate cleared every frozen "
                "gate. It is nominated only for mandatory stress replay; consumed "
                "development history cannot establish a new baseline."
                if nominated
                else "No preregistered OSR specification cleared every frozen development "
                "gate. OSR is rejected at Milestone 3 and the 21.50% AEM v1 canonical "
                "baseline remains unchanged."
            ),
        },
    }
    write_json(target / "report.json", report)
    write_json(
        output / "osr/selector_race/latest.json",
        {"id": run_id, "path": str(target / "report.json")},
    )
    return report
