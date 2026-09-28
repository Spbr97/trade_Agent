"""Milestone 4, Stage 2: the bounded AEM v2 pipeline race.

Registers exactly 24 pipeline specifications (4 entry-mode groupings x 3 quick-profit
geometries x 2 models), runs each through a nested chronological walk-forward
evaluation over a labeled development dataset (``aem_v2_development_experiment``'s
Stage 1 output), and checks every candidate against the frozen Milestone-0 development
gates. Nothing here trains on a row it will be scored on: the OUTER split is
``tradedesk_lab.validation.walk_forward`` (chronological, purged, embargoed) and the
INNER split, for the Precision Ladder, is Milestone 3's own
``fit_oof_calibration`` - both fit only on their own training rows.

Disclosed limitation: the frozen protocol's mandatory execution stresses (cost x1.25/
x1.5, slippage x2, one-bar delay, adversarial missed fills) are not evaluated by this
module. AEM v1's own validation was staged the same way - a baseline scorecard first,
registered stress gates as a separate later checkpoint
(``aem_staged_validation.py``) - and this follows that precedent rather than silently
skipping the requirement. A candidate that clears every gate here still needs that
stress pass before Milestone 4 can be called complete.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT, DEFAULT_AEM_V2_PROTOCOL
from tradedesk_lab.aem_v2_precision_ladder import (
    REGISTERED_FEATURE_NAMES,
    ScoredCandidate,
    calibrated_probability,
    fit_logistic_control,
    fit_oof_calibration,
    hard_veto_reasons,
    score_candidates,
    score_logistic_control,
    select_calls,
)
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.validation import walk_forward

ENTRY_MODE_GROUPS = ("anticipatory_impulse", "confirmed_pullback", "breakout_retest", "pooled")
MODEL_KINDS = ("precision_ladder", "logistic_control")
TOP_K_POLICIES = (1, 2, 3)

# The canonical AEM v1 baseline this challenger must exceed, not re-derive.
BASELINE_WILSON95_LOWER = 0.18603500195342146
BASELINE_STRICT_SUCCESS_RATE = 149 / 693

# A disclosed concentration proxy: the plan requires that "no single symbol, week or
# narrow regime accounts for most of the result" without naming an exact number.
MAXIMUM_SINGLE_SYMBOL_WIN_SHARE = 0.50

MINIMUM_TRAIN_ROWS_FOR_FOLD = 30


@dataclass(frozen=True)
class PipelineSpec:
    id: str
    entry_mode_group: str
    geometry_id: str
    model_kind: str


def registered_pipeline_specs() -> tuple[PipelineSpec, ...]:
    """The frozen, preregistered trial list - exactly 24, at the plan's own budget."""

    specs = tuple(
        PipelineSpec(
            id=f"{mode}__{geometry.id}__{model_kind}",
            entry_mode_group=mode,
            geometry_id=geometry.id,
            model_kind=model_kind,
        )
        for mode in ENTRY_MODE_GROUPS
        for geometry in DEFAULT_AEM_V2_CONTRACT.geometries
        for model_kind in MODEL_KINDS
    )
    if len(specs) > DEFAULT_AEM_V2_PROTOCOL.initial_pipeline_trial_budget:
        raise ValueError("registered pipeline specs exceed the frozen trial budget")
    return specs


def _wilson95_lower(successes: int, n: int) -> float:
    if n == 0:
        return 0.0
    z = 1.959963985
    phat = successes / n
    denominator = 1 + z**2 / n
    center = phat + z**2 / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2))
    return (center - margin) / denominator


def _evaluate_selection(selected: pd.DataFrame, eligible_sessions: set) -> dict[str, Any]:
    n = len(selected)
    wins = int(selected["label"].sum()) if n else 0
    active_sessions = int(selected["session"].nunique()) if n else 0
    symbol_win_share = 0.0
    if wins:
        by_symbol = selected.loc[selected["label"] == 1, "scrip_code"].value_counts()
        symbol_win_share = float(by_symbol.iloc[0] / wins)
    return {
        "resolved_fills": n,
        "strict_wins": wins,
        "strict_success_rate": wins / n if n else 0.0,
        "wilson95_lower": _wilson95_lower(wins, n),
        "active_sessions": active_sessions,
        "active_session_coverage": (
            active_sessions / len(eligible_sessions) if eligible_sessions else 0.0
        ),
        "mean_net_r": float(selected["net_r"].mean()) if n else 0.0,
        "max_single_symbol_win_share": symbol_win_share,
    }


def _random_selection(eligible: pd.DataFrame, *, top_k: int, seed: int) -> pd.DataFrame:
    """The matched-random-timing control: same population, same per-session count,
    a random pick instead of the model's ranked one.
    """

    if eligible.empty:
        return eligible
    picks = []
    for session, group in eligible.groupby("session"):
        dedup = group.drop_duplicates("scrip_code")
        n = min(top_k, len(dedup))
        picks.append(dedup.sample(n=n, random_state=seed + hash(session) % 10_000))
    return pd.concat(picks) if picks else eligible.iloc[0:0]


def _check_development_gates(metrics: dict[str, Any], random_metrics: dict[str, Any]) -> dict:
    protocol = DEFAULT_AEM_V2_PROTOCOL
    failures = []
    if metrics["resolved_fills"] < protocol.minimum_resolved_selected_fills:
        failures.append(f"resolved_fills_below_{protocol.minimum_resolved_selected_fills}")
    if metrics["active_sessions"] < protocol.minimum_active_sessions:
        failures.append(f"active_sessions_below_{protocol.minimum_active_sessions}")
    if metrics["active_session_coverage"] < protocol.minimum_active_session_coverage:
        failures.append("active_session_coverage_below_floor")
    if metrics["strict_success_rate"] < protocol.minimum_observed_strict_success_rate:
        failures.append("strict_success_rate_below_50pct")
    if metrics["wilson95_lower"] < protocol.minimum_wilson95_lower:
        failures.append("wilson95_lower_below_40pct")
    if metrics["wilson95_lower"] <= BASELINE_WILSON95_LOWER:
        failures.append("wilson95_lower_does_not_exceed_aem_v1_baseline")
    if metrics["mean_net_r"] < protocol.minimum_mean_net_r:
        failures.append("mean_net_r_negative")
    advantage = metrics["mean_net_r"] - random_metrics["mean_net_r"]
    if advantage < protocol.minimum_random_advantage_r:
        failures.append("insufficient_advantage_over_matched_random")
    if metrics["max_single_symbol_win_share"] > MAXIMUM_SINGLE_SYMBOL_WIN_SHARE:
        failures.append("single_symbol_concentration_too_high")
    return {"passed": not failures, "failures": tuple(failures), "random_advantage_r": advantage}


def _prepare_pipeline_frame(dataset: pd.DataFrame, spec: PipelineSpec) -> pd.DataFrame:
    subset = (
        dataset
        if spec.entry_mode_group == "pooled"
        else dataset.loc[dataset["mode"] == spec.entry_mode_group]
    )
    subset = subset.loc[subset["geometry_id"] == spec.geometry_id]
    return subset.reset_index(drop=True)


def _run_one_spec(
    dataset: pd.DataFrame,
    spec: PipelineSpec,
    *,
    splits: int,
    embargo: int,
    seed: int,
    keep_selected_rows: bool = False,
) -> dict[str, Any]:
    subset = _prepare_pipeline_frame(dataset, spec)
    if subset.empty:
        return {"spec_id": spec.id, "status": "no_data"}

    geometry = next(g for g in DEFAULT_AEM_V2_CONTRACT.geometries if g.id == spec.geometry_id)
    working = subset.copy()
    working["armed_on"] = pd.to_datetime(working["session"])
    working["label_end_date"] = working["armed_on"]
    calendar = pd.DatetimeIndex(sorted(working["armed_on"].unique()))
    folds = walk_forward(working, calendar, splits=splits, embargo=embargo)
    if not folds:
        return {"spec_id": spec.id, "status": "insufficient_sessions_for_walk_forward"}

    veto_reasons = working.apply(
        lambda row: hard_veto_reasons(
            {name: row[name] for name in REGISTERED_FEATURE_NAMES},
            mode=row["mode"],
            geometry=geometry,
        ),
        axis=1,
    )
    eligible_mask = veto_reasons.apply(len) == 0

    selected_by_topk: dict[int, list[pd.DataFrame]] = {k: [] for k in TOP_K_POLICIES}
    random_by_topk: dict[int, list[pd.DataFrame]] = {k: [] for k in TOP_K_POLICIES}
    eligible_rows_all: list[pd.DataFrame] = []

    for fold_index, fold in enumerate(folds):
        train = working.iloc[fold.train]
        test = working.iloc[fold.test]
        train_eligible = train.loc[eligible_mask.iloc[fold.train].to_numpy()]
        test_eligible = test.loc[eligible_mask.iloc[fold.test].to_numpy()]
        eligible_rows_all.append(test_eligible)
        if len(train_eligible) < MINIMUM_TRAIN_ROWS_FOR_FOLD or test_eligible.empty:
            continue

        x_train = train_eligible[list(REGISTERED_FEATURE_NAMES)]
        y_train = train_eligible["label"].to_numpy()
        x_test = test_eligible[list(REGISTERED_FEATURE_NAMES)]
        try:
            if spec.model_kind == "precision_ladder":
                oof = fit_oof_calibration(
                    x_train, y_train, train_eligible["session"].to_numpy()
                )
                probabilities = calibrated_probability(
                    oof.calibrator, score_candidates(oof.fitted, x_test)
                )
            else:
                model = fit_logistic_control(x_train, y_train, seed=seed)
                probabilities = score_logistic_control(model, x_test)
        except ValueError:
            continue

        candidates = [
            ScoredCandidate(
                identifier=row.opportunity_id,
                session=row.session,
                scrip_code=row.scrip_code,
                calibrated_probability=float(probability),
            )
            for row, probability in zip(
                test_eligible.itertuples(), probabilities, strict=True
            )
        ]
        for top_k in TOP_K_POLICIES:
            chosen_ids = set(select_calls(candidates, top_k=top_k))
            selected_by_topk[top_k].append(
                test_eligible.loc[test_eligible["opportunity_id"].isin(chosen_ids)]
            )
            random_by_topk[top_k].append(
                _random_selection(test_eligible, top_k=top_k, seed=seed + fold_index)
            )

    empty = working.iloc[0:0]
    eligible_all = pd.concat(eligible_rows_all) if eligible_rows_all else empty
    eligible_sessions = set(eligible_all["session"])
    unfiltered_baseline = _evaluate_selection(eligible_all, eligible_sessions)

    top_k_reports = {}
    for top_k in TOP_K_POLICIES:
        selected = pd.concat(selected_by_topk[top_k]) if selected_by_topk[top_k] else empty
        random_selected = pd.concat(random_by_topk[top_k]) if random_by_topk[top_k] else empty
        metrics = _evaluate_selection(selected, eligible_sessions)
        random_metrics = _evaluate_selection(random_selected, eligible_sessions)
        top_k_reports[top_k] = {
            "metrics": metrics,
            "matched_random_control": random_metrics,
            "gate": _check_development_gates(metrics, random_metrics),
        }

    result: dict[str, Any] = {
        "spec_id": spec.id,
        "status": "evaluated",
        "entry_mode_group": spec.entry_mode_group,
        "geometry_id": spec.geometry_id,
        "model_kind": spec.model_kind,
        "folds": len(folds),
        "eligible_candidates": len(eligible_all),
        "unfiltered_baseline": unfiltered_baseline,
        "top_k": top_k_reports,
    }
    if keep_selected_rows:
        result["_selected_frames"] = {
            top_k: (pd.concat(selected_by_topk[top_k]) if selected_by_topk[top_k] else empty)
            for top_k in TOP_K_POLICIES
        }
    return result


def selected_fills_for_spec(
    dataset: pd.DataFrame,
    spec_id: str,
    *,
    top_k: int,
    splits: int = 3,
    embargo: int = 10,
    seed: int = 20260101,
) -> pd.DataFrame:
    """Re-run one registered spec's nested walk-forward selection and return exactly
    the rows its ``top_k`` policy selected - the real fills a mandatory execution-stress
    replay (``aem_v2_stress_gates``) needs to re-resolve. Deterministic: the same
    dataset/spec/top_k/splits/embargo/seed always reproduces the same selection, since
    every fold's model fit and ranking is seeded.
    """

    if top_k not in TOP_K_POLICIES:
        raise ValueError(f"top_k must be one of {TOP_K_POLICIES}")
    spec = next((s for s in registered_pipeline_specs() if s.id == spec_id), None)
    if spec is None:
        raise ValueError(f"unregistered pipeline spec id: {spec_id}")
    trial = _run_one_spec(
        dataset, spec, splits=splits, embargo=embargo, seed=seed, keep_selected_rows=True
    )
    if trial.get("status") != "evaluated":
        raise ValueError(f"spec {spec_id} did not evaluate: {trial.get('status')}")
    return trial["_selected_frames"][top_k]


def run_pipeline_race(
    dataset: pd.DataFrame, *, splits: int = 3, embargo: int = 10, seed: int = 20260101
) -> dict[str, Any]:
    """Run every registered spec's nested chronological walk-forward evaluation.

    ``dataset`` is Stage 1's output: one row per (opportunity, geometry), with the 34
    registered feature columns plus ``label``, ``net_r``, ``session``, ``scrip_code``,
    ``mode``, ``geometry_id`` and ``opportunity_id``.
    """

    specs = registered_pipeline_specs()
    trials = [
        _run_one_spec(dataset, spec, splits=splits, embargo=embargo, seed=seed) for spec in specs
    ]
    qualified = [
        {"spec_id": trial["spec_id"], "top_k": top_k}
        for trial in trials
        if trial.get("status") == "evaluated"
        for top_k in TOP_K_POLICIES
        if trial["top_k"][top_k]["gate"]["passed"]
    ]
    return {
        "trial_budget": len(specs),
        "trials_registered": len(specs),
        "trials": trials,
        "qualified_candidates": qualified,
        "stress_gates_evaluated": False,
        "baseline_wilson95_lower": BASELINE_WILSON95_LOWER,
        "baseline_strict_success_rate": BASELINE_STRICT_SUCCESS_RATE,
    }


def _load_development_dataset(output: Path, dataset_run_id: str | None) -> tuple[str, pd.DataFrame]:
    if dataset_run_id is None:
        dataset_run_id = json.loads(
            (output / "aem_v2/development_dataset/latest.json").read_text()
        )["id"]
    folder = output / "aem_v2/development_dataset/runs" / dataset_run_id
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if report["id"] != dataset_run_id:
        raise ValueError("development dataset pointer and report differ")
    if not report.get("real_run_completed", True) and report["resolved_rows"] == 0:
        raise ValueError("development dataset run produced no rows")
    frame = pd.read_csv(folder / "events.csv")
    return dataset_run_id, frame


def freeze_pipeline_race(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_run_id: str | None = None,
    splits: int = 3,
    embargo: int = 10,
    seed: int = 20260101,
) -> dict[str, Any]:
    root, output = Path(root).resolve(), Path(output).resolve()
    dataset_run_id, frame = _load_development_dataset(output, dataset_run_id)
    result = run_pipeline_race(frame, splits=splits, embargo=embargo, seed=seed)
    run_id = uuid4().hex
    target = output / "aem_v2/pipeline_race/runs" / run_id
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "aem-v2-milestone-4-pipeline-race-v1",
        "status": "pipeline_race_evaluated",
        "milestone": 4,
        "eligible_for_live": False,
        "baseline_improved": bool(result["qualified_candidates"]),
        "algorithm_evaluated": True,
        "development_dataset_run_id": dataset_run_id,
        "development_dataset_rows": int(len(frame)),
        "splits": splits,
        "embargo": embargo,
        "seed": seed,
        **result,
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "register_candidate": bool(result["qualified_candidates"]),
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "A candidate that clears every development gate here is only "
                "nominated - it still needs the mandatory stress gates (not "
                "evaluated by this module) and Milestone 5's independent locked "
                "evaluation before it can be called a research baseline, let alone "
                "production-eligible."
                if result["qualified_candidates"]
                else "No registered specification cleared every frozen development "
                "gate. AEM v1 remains the canonical baseline; no live change."
            ),
        },
    }
    write_json(target / "report.json", report)
    write_json(
        output / "aem_v2/pipeline_race/latest.json",
        {"id": run_id, "path": str(target / "report.json")},
    )
    return report
