"""Bounded precision research, with chronological selection and no live promotion.

All saved historical periods have already been inspected. These are diagnostics,
including the final reserved calendar block, never fresh prospective evidence.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd

from tradedesk.reliability import wilson_lower_bound
from tradedesk_lab.artifacts import OUTPUT, digest, write_json
from tradedesk_lab.dataset import Dataset
from tradedesk_lab.models import fit
from tradedesk_lab.validation import purge, walk_forward

THRESHOLDS = (0.6, 0.7, 0.8, 0.9)
REGIMES = (None, "risk_on", "neutral", "risk_off")


def candidate_grid() -> tuple[dict, dict]:
    """One preregistered configuration each keeps repeated-search risk bounded."""
    choices = {
        "logistic": {"C": 0.1},
        "extratrees": {"depth": 6, "iterations": 120},
    }
    unavailable = {}
    for family in ("xgboost", "catboost"):
        try:
            __import__(family)
            choices[family] = {"depth": 3, "iterations": 100}
        except Exception as exc:
            unavailable[family] = f"{type(exc).__name__}: {exc}"
    return choices, unavailable


def select_calls(frame: pd.DataFrame, probabilities: np.ndarray, policy: dict) -> np.ndarray:
    """Rank at the common arming close, without future entry dates or outcomes.

    One symbol per arming session prevents duplicate setups inflating call volume.
    Realized entry dates cannot be used to choose the top calls retrospectively.
    """
    p = np.asarray(probabilities, dtype=float)
    mask = np.isfinite(p) & (p >= policy["threshold"])
    if policy.get("regime"):
        mask &= frame.regime.to_numpy() == policy["regime"]
    positions = np.flatnonzero(mask)
    candidates = frame.iloc[positions][["armed_on", "signal_id", "scrip_code"]].copy()
    candidates["probability"] = p[positions]
    candidates["position"] = positions
    candidates = candidates.sort_values(
        ["armed_on", "probability", "signal_id"], ascending=[True, False, True]
    ).drop_duplicates(["armed_on", "scrip_code"])
    candidates = candidates.groupby("armed_on", sort=False).head(policy["top_k"])
    result = np.zeros(len(frame), dtype=bool)
    result[candidates.position.to_numpy(dtype=int)] = True
    return result


def session_metrics(frame: pd.DataFrame, selected: np.ndarray, calendar: pd.DatetimeIndex) -> dict:
    """All sessions in the supplied evaluation window, including zero-call days."""
    chosen = frame.loc[selected].copy()
    n = len(chosen)
    wins = int(chosen.label.sum())
    net = chosen.net_r.to_numpy(dtype=float)
    finite = np.isfinite(net)
    counts = chosen.groupby("armed_on").agg(calls=("label", "size"), wins=("label", "sum"))
    counts = counts.reindex(calendar, fill_value=0)
    active = counts.calls > 0
    rate = counts.loc[active, "wins"] / counts.loc[active, "calls"]
    lower = float(wilson_lower_bound(wins, n)) if n else None
    mean_net = float(net[finite].mean()) if finite.any() else None
    precision = wins / n if n else None
    eligible = (
        n >= 100
        and int(active.sum()) >= 30
        and precision is not None
        and precision >= 0.8
        and lower is not None
        and lower >= 0.7
        and int(finite.sum()) == n
        and mean_net is not None
        and mean_net > 0
    )
    return {
        "selected": n,
        "wins": wins,
        "precision": precision,
        "wilson_lower": lower,
        "resolved_net": int(finite.sum()),
        "mean_net_r": mean_net,
        "net_win_rate": float((net[finite] > 0).mean()) if finite.any() else None,
        "sessions": len(calendar),
        "active_sessions": int(active.sum()),
        "zero_call_sessions": int((~active).sum()),
        "session_coverage": float(active.mean()) if len(counts) else 0.0,
        "mean_calls_per_session": float(counts.calls.mean()) if len(counts) else 0.0,
        "active_sessions_at_least_70pct": float((rate >= 0.7).mean()) if len(rate) else None,
        "active_sessions_at_least_80pct": float((rate >= 0.8).mean()) if len(rate) else None,
        "sessions_below_70pct": int((rate < 0.7).sum()),
        "historical_target_met": bool(eligible),
        "session_definition": "arming-date cohorts, eventual outcomes; not intraday win rate",
        "wilson_caveat": "IID diagnostic only; correlated calls reduce effective sample size",
    }


def _calendar(frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if frame.empty:
        return pd.DatetimeIndex([])
    return calendar[(calendar >= frame.armed_on.min()) & (calendar <= frame.armed_on.max())]


def choose_policy(
    frame: pd.DataFrame, p: np.ndarray, calendar: pd.DatetimeIndex, trials: list, scope: str
) -> tuple[dict, dict]:
    finite = np.isfinite(p)
    scored = frame.loc[finite].reset_index(drop=True)
    scores = p[finite]
    sessions = _calendar(scored, calendar)
    policies = []
    for threshold in THRESHOLDS:
        for top_k in (1, 2, 3):
            for regime in REGIMES:
                policy = {"threshold": threshold, "top_k": top_k, "regime": regime}
                metrics = session_metrics(scored, select_calls(scored, scores, policy), sessions)
                trials.append({"scope": scope, "kind": "selector", "policy": policy, **metrics})
                policies.append((policy, metrics))
    eligible = [(p, m) for p, m in policies if m["historical_target_met"]]
    if not eligible:
        return {"threshold": 1.01, "top_k": 1, "regime": None}, {
            "reason": (
                "No inner policy met 80% precision / 70% Wilson / positive net / volume gates"
            ),
            "qualifying_policies": 0,
        }
    policy, metrics = max(
        eligible,
        key=lambda item: (item[1]["wilson_lower"], item[1]["mean_net_r"], item[1]["selected"]),
    )
    return policy, {"qualifying_policies": len(eligible), "inner_metrics": metrics}


def _fit_logged(family, hp, train, features, calendar, trials, scope):
    trial = {
        "scope": scope,
        "kind": "model_fit",
        "family": family,
        "parameters": hp,
        "rows": len(train),
        "train_start": str(train.armed_on.min()),
        "train_end": str(train.armed_on.max()),
        "latest_training_label_end": str(train.label_end_date.max()),
        "status": "started",
    }
    trials.append(trial)
    try:
        bundle = fit(family, hp, train, features, calendar)
        trial["status"] = "completed"
        return bundle
    except (ValueError, ImportError) as exc:
        trial.update(status="skipped", reason=f"{type(exc).__name__}: {exc}")
        return None


def _stack(train, inner, outer, calendar, trials, scope):
    """Meta train only on causal OOF values; reserve later OOF for policy selection."""
    families = sorted(inner)
    if len(families) < 2:
        return None, None, None, "Fewer than two fitted base families"
    values = np.column_stack([inner[name] for name in families])
    valid = np.isfinite(values).all(axis=1)
    meta = train.loc[valid].copy().reset_index(drop=True)
    names = [f"probability_{name}" for name in families]
    meta[names] = values[valid]
    dates = np.sort(meta.armed_on.unique())
    if len(dates) < 60 or len(meta) < 300:
        return None, None, None, "Fewer than 300 causal OOF rows / 60 dates for meta split"
    cutoff = dates[int(len(dates) * 0.7)]
    validation = np.flatnonzero(meta.armed_on >= cutoff)
    training = np.flatnonzero(meta.armed_on < cutoff)
    training = purge(meta, training, validation, calendar)
    bundle = _fit_logged(
        "logistic", {"C": 0.1}, meta.iloc[training], names, calendar, trials, scope + "/stack"
    )
    if bundle is None:
        return None, None, None, "Insufficient purged meta training/calibration rows"
    prediction = np.full(len(train), np.nan)
    prediction[np.flatnonzero(valid)[validation]] = bundle.predict(meta.iloc[validation])
    future = pd.DataFrame(
        {name: outer[family] for name, family in zip(names, families, strict=True)}
    )
    return prediction, bundle.predict(future), bundle, None


def _fold_predictions(train, test, dataset, choices, trials, scope):
    inner, outer, bundles = {}, {}, {}
    folds = walk_forward(train, dataset.calendar, splits=3)
    for family, hp in choices.items():
        prediction = np.full(len(train), np.nan)
        for number, fold in enumerate(folds):
            bundle = _fit_logged(
                family,
                hp,
                train.iloc[fold.train],
                dataset.features,
                dataset.calendar,
                trials,
                f"{scope}/{family}/inner-{number + 1}",
            )
            if bundle is not None:
                prediction[fold.test] = bundle.predict(train.iloc[fold.test])
        bundle = _fit_logged(
            family, hp, train, dataset.features, dataset.calendar, trials, f"{scope}/{family}/fit"
        )
        if bundle is not None and np.isfinite(prediction).any():
            inner[family] = prediction
            outer[family] = bundle.predict(test)
            bundles[family] = bundle
    if len(inner) >= 2:
        inner["mean_ensemble"] = np.mean(np.column_stack(list(inner.values())), axis=1)
        outer["mean_ensemble"] = np.mean(np.column_stack(list(outer.values())), axis=1)
    base_inner = {name: inner[name] for name in bundles}
    base_outer = {name: outer[name] for name in bundles}
    ip, op, stack, reason = _stack(train, base_inner, base_outer, dataset.calendar, trials, scope)
    if stack is not None:
        inner["stacked_logistic"] = ip
        outer["stacked_logistic"] = op
        bundles["stacked_logistic"] = stack
    else:
        trials.append({"scope": scope, "kind": "stack", "status": "skipped", "reason": reason})
    return inner, outer, bundles


def _calibration(frame, p):
    valid = np.isfinite(p)
    if not valid.any():
        return {"rows": 0, "brier": None, "bins": []}
    y = frame.label.to_numpy(dtype=float)
    bins = []
    for lo in np.arange(0, 1, 0.1):
        mask = valid & (p >= lo) & (p <= 1 if lo > 0.89 else p < lo + 0.1)
        if mask.any():
            bins.append(
                {
                    "predicted": float(p[mask].mean()),
                    "observed": float(y[mask].mean()),
                    "rows": int(mask.sum()),
                }
            )
    return {
        "rows": int(valid.sum()),
        "brier": float(np.mean((p[valid] - y[valid]) ** 2)),
        "bins": bins,
    }


def run_reliability(dataset: Dataset, *, output: Path = OUTPUT, outer_splits: int = 3) -> dict:
    """Run a fixed bounded search; export candidates without changing active cohorts."""
    frame = dataset.frame.sort_values(["armed_on", "signal_id"]).reset_index(drop=True)
    if dataset.manifest.get("label_version") != "net-target-v2":
        raise ValueError("Reliability comparison requires freshly rebuilt net-target-v2 labels")
    dates = np.sort(frame.armed_on.unique())
    if len(dates) < 120:
        raise ValueError("At least 120 distinct dates required for nested temporal validation")
    split = dates[int(len(dates) * 0.8)]
    test_idx = np.flatnonzero(frame.armed_on >= split)
    dev_idx = purge(frame, np.flatnonzero(frame.armed_on < split), test_idx, dataset.calendar)
    dev, test = (
        frame.iloc[dev_idx].reset_index(drop=True),
        frame.iloc[test_idx].reset_index(drop=True),
    )
    choices, missing = candidate_grid()
    trials, details = [], []
    accumulated, selected, validity = {}, {}, np.zeros(len(dev), dtype=bool)
    for number, fold in enumerate(walk_forward(dev, dataset.calendar, splits=outer_splits)):
        scope = f"outer-{number + 1}"
        print(
            f"Reliability {scope}: {len(fold.train)} train / {len(fold.test)} diagnostic",
            flush=True,
        )
        train, evaluation = dev.iloc[fold.train].reset_index(drop=True), dev.iloc[fold.test]
        inner, outer, _ = _fold_predictions(train, evaluation, dataset, choices, trials, scope)
        policies = {}
        for family, p in outer.items():
            policy, evidence = choose_policy(
                train, inner[family], dataset.calendar, trials, f"{scope}/{family}"
            )
            accumulated.setdefault(family, np.full(len(dev), np.nan))[fold.test] = p
            selected.setdefault(family, np.zeros(len(dev), dtype=bool))[fold.test] = select_calls(
                evaluation, p, policy
            )
            policies[family] = {"policy": policy, **evidence}
        if outer:
            validity[fold.test] = True
        details.append(
            {
                "scope": scope,
                "test_start": str(evaluation.armed_on.min()),
                "test_end": str(evaluation.armed_on.max()),
                "policies": policies,
            }
        )
    if not validity.any():
        raise ValueError("No usable nested temporal folds")
    evaluation = dev.loc[validity]
    calendar = _calendar(evaluation, dataset.calendar)
    development = {
        family: {
            **session_metrics(evaluation, selected[family][validity], calendar),
            "calibration": _calibration(dev, p),
        }
        for family, p in accumulated.items()
    }
    # The outer development folds select the family; final diagnostic outcomes never do.
    eligible = [name for name, metrics in development.items() if metrics["historical_target_met"]]
    candidate = (
        max(eligible, key=lambda name: development[name]["wilson_lower"]) if eligible else None
    )
    print("Reliability: freezing final candidates and historical diagnostic policies", flush=True)
    inner, probabilities, bundles = _fold_predictions(dev, test, dataset, choices, trials, "final")
    identifier = uuid4().hex
    folder = output / "reliability" / identifier
    folder.mkdir(parents=True, exist_ok=False)
    artifacts, policies, historical = {}, {}, {}
    for family, bundle in bundles.items():
        path = folder / f"{family}.joblib"
        joblib.dump(bundle, path)
        artifacts[family] = {"path": path.name, "sha256": digest(path), "features": bundle.features}
    final_calendar = _calendar(test, dataset.calendar)
    grid_diagnostics = []
    for family, p in probabilities.items():
        policy, evidence = choose_policy(
            dev, inner[family], dataset.calendar, trials, f"final/{family}"
        )
        policies[family] = {"policy": policy, **evidence}
        historical[family] = {
            **session_metrics(test, select_calls(test, p, policy), final_calendar),
            "calibration": _calibration(test, p),
        }
        # Full transparent grid is descriptive; it cannot revise candidate or policies.
        for threshold in THRESHOLDS:
            for top_k in (1, 2, 3):
                for regime in REGIMES:
                    diagnostic = {"threshold": threshold, "top_k": top_k, "regime": regime}
                    metrics = session_metrics(
                        test, select_calls(test, p, diagnostic), final_calendar
                    )
                    row = {"family": family, "policy": diagnostic, **metrics}
                    grid_diagnostics.append(row)
                    trials.append(
                        {"scope": "historical-diagnostic-only", "kind": "selector", **row}
                    )
    report = {
        "id": identifier,
        "created_at": datetime.now(UTC).isoformat(),
        "metadata": {
            **dataset.manifest,
            "features": dataset.features,
            "dataset_sha256": hashlib.sha256(
                pd.util.hash_pandas_object(frame, index=True).values.tobytes()
            ).hexdigest(),
            "search_grid": choices,
            "unavailable_families": missing,
            "thresholds": list(THRESHOLDS),
            "top_k": [1, 2, 3],
            "regime_filters": list(REGIMES),
            "diagnostic_start": str(pd.Timestamp(split).date()),
            "historical_role": (
                "previously inspected diagnostic only; not fresh out-of-sample proof"
            ),
            "prediction_time": (
                "arming close; per-session cap groups armed_on, never future entry_date"
            ),
            "target": {"precision": 0.8, "wilson_lower": 0.7, "calls": 100, "active_sessions": 30},
        },
        "development": development,
        "historical_diagnostic": historical,
        "diagnostic_selector_grid": grid_diagnostics,
        "outer_folds": details,
        "frozen_policies": policies,
        "artifacts": artifacts,
        "candidate_for_prospective_review": candidate,
        "eligibility": {
            "decision": "NO_TRADE",
            "live_enabled": False,
            "auto_promote": False,
            "reasons": [
                "Fresh prospective results required; inspected historical periods cannot qualify",
                "Existing setup, cost, risk, sample-size and operational gates still apply",
                "No guaranteed session accuracy; zero-call policies fail availability objective",
            ],
        },
        "trials": trials,
        "attempted_trials": len(trials),
    }
    write_json(folder / "report.json", report)
    write_json(
        folder / "predictions.json",
        {
            "signal_ids": test.signal_id.tolist(),
            "probabilities": {name: values.tolist() for name, values in probabilities.items()},
        },
    )
    write_json(
        folder / "contract.json",
        {
            "label_version": "net-target-v2",
            "features": dataset.features,
            "artifacts": artifacts,
            "policies": policies,
            "stack_inputs": sorted(name for name in bundles if name != "stacked_logistic"),
            "training_label_end": str(dev.label_end_date.max()),
            "prospective_activation": None,
            "requires_explicit_new_cohort": True,
        },
    )
    write_json(output / "reliability" / "latest.json", {"id": identifier})
    print(f"Completed bounded reliability diagnostic: {identifier}", flush=True)
    return report
