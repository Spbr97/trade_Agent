"""Nested chronological model comparison; fixed CPCV stress test; immutable reports."""

from __future__ import annotations

import hashlib

import joblib
import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT, digest, git, verify_base, write_json
from tradedesk_lab.dataset import Dataset
from tradedesk_lab.economics import replay
from tradedesk_lab.metrics import evaluate, paired_bootstrap, select_threshold
from tradedesk_lab.models import agreement, fit, grids
from tradedesk_lab.registry import Registry
from tradedesk_lab.validation import (
    cpcv,
    deflated_sharpe,
    probability_of_overfitting,
    purge,
    reconstruct,
    walk_forward,
)


def tune(
    family: str,
    choices: list[dict],
    df: pd.DataFrame,
    ds: Dataset,
    registry: Registry,
    experiment: str,
    scope: str,
) -> tuple[dict, float, np.ndarray]:
    folds = walk_forward(df, ds.calendar, splits=2)
    candidates = []
    for hp in choices:
        predictions = np.full(len(df), np.nan)
        errors = []
        for fold in folds:
            try:
                model = fit(family, hp, df.iloc[fold.train], ds.features, ds.calendar)
                predictions[fold.test] = model.predict(df.iloc[fold.test])
            except ValueError as exc:
                errors.append(str(exc))
        metrics = evaluate(df, predictions)
        registry.candidate(
            experiment,
            family,
            {**hp, "scope": scope},
            metrics,
            status="completed" if metrics["brier"] is not None else "failed",
            error="; ".join(errors) or None,
        )
        if metrics["brier"] is not None:
            candidates.append((metrics["brier"], hp, predictions))
    if not candidates:
        raise ValueError(f"No usable inner folds for {family}")
    _, best, predictions = min(candidates, key=lambda item: item[0])
    threshold, _ = select_threshold(df, predictions)
    return best, threshold, predictions


def stage_report(
    ds: Dataset,
    df: pd.DataFrame,
    probabilities: dict[str, np.ndarray],
    masks: dict[str, np.ndarray],
    valid: np.ndarray,
) -> tuple[dict, dict]:
    result, returns = {}, {}
    for name, mask in masks.items():
        p = probabilities.get(name, probabilities["ensemble"])
        p = np.where(valid, p, np.nan)
        selected = mask & valid
        metrics = evaluate(df, p, selected)
        metrics.update(paired_bootstrap(df.loc[valid].reset_index(drop=True), selected[valid]))
        econ, daily = replay(ds, df.loc[valid].reset_index(drop=True), selected[valid])
        metrics["portfolio"] = econ
        result[name] = metrics
        returns[name] = daily
    return result, returns


def run(dataset: Dataset, *, stress: bool = True) -> dict:
    frame = dataset.frame
    unique = sorted(frame.armed_on.unique())
    split = pd.Timestamp(unique[int(len(unique) * 0.8)])
    final_idx = np.flatnonzero(frame.armed_on >= split)
    dev_idx = np.flatnonzero(frame.armed_on < split)
    dev_idx = purge(frame, dev_idx, final_idx, dataset.calendar)
    dev = frame.iloc[dev_idx].reset_index(drop=True)
    final = frame.iloc[final_idx].reset_index(drop=True)
    search, missing = grids()
    if len(search) < 2:
        raise ValueError("Install at least two model families before ensemble evaluation")
    content_hash = hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    ).hexdigest()
    metadata = {
        **dataset.manifest,
        "dataset_sha256": content_hash,
        "features": dataset.features,
        "git_commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")),
        "holdout_start": str(split.date()),
        "holdout_role": "historical diagnostic",
        "search_grid": search,
        "unavailable_families": missing,
        "decision_rule": "Inner-fold positive net R, >=100 resolved, maximize Wilson lower bound",
        "agreement_spread": 0.10,
        "cpcv": {"groups": 6, "held_out": 2},
        "promotes_live": False,
    }
    with Registry(OUTPUT / "registry.sqlite") as registry:
        identifier = registry.begin(metadata)
        folder = OUTPUT / "runs" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        try:
            probabilities = {family: np.full(len(dev), np.nan) for family in search}
            masks = {family: np.zeros(len(dev), dtype=bool) for family in search}
            masks.update(
                {
                    name: np.zeros(len(dev), dtype=bool)
                    for name in ("ensemble", "ensemble_regime", "ensemble_agreement")
                }
            )
            ensemble_p = np.full(len(dev), np.nan)
            baseline_p = np.full(len(dev), np.nan)
            outer_details = []
            for outer_i, fold in enumerate(walk_forward(dev, dataset.calendar, splits=3)):
                print(
                    f"Outer fold {outer_i + 1}: {len(fold.train)} train / {len(fold.test)} test",
                    flush=True,
                )
                train = dev.iloc[fold.train].reset_index(drop=True)
                baseline_p[fold.test] = float(train.label.mean())
                inner, outer = {}, {}
                detail = {"test_start": str(dev.iloc[fold.test].armed_on.min()), "families": {}}
                for family, choices in search.items():
                    hp, threshold, oof = tune(
                        family, choices, train, dataset, registry, identifier, f"outer-{outer_i}"
                    )
                    model = fit(family, hp, train, dataset.features, dataset.calendar)
                    outer[family] = model.predict(dev.iloc[fold.test])
                    inner[family] = oof
                    probabilities[family][fold.test] = outer[family]
                    masks[family][fold.test] = outer[family] >= threshold
                    detail["families"][family] = {"hp": hp, "threshold": threshold}
                inner_valid = np.logical_and.reduce([np.isfinite(p) for p in inner.values()])
                mean_inner = np.mean(np.column_stack(list(inner.values())), axis=1)
                mean_inner[~inner_valid] = np.nan
                threshold, _ = select_threshold(train, mean_inner)
                agree = agreement(outer)
                ensemble_p[fold.test] = agree["mean"]
                keep = agree["mean"] >= threshold
                regime = dev.iloc[fold.test].regime.to_numpy() == "risk_on"
                masks["ensemble"][fold.test] = keep
                masks["ensemble_regime"][fold.test] = keep & regime
                masks["ensemble_agreement"][fold.test] = keep & regime & agree["agree"]
                detail["ensemble_threshold"] = threshold
                outer_details.append(detail)
            valid = np.isfinite(ensemble_p)
            if not valid.any():
                raise ValueError("No nested OOS predictions")
            probabilities["ensemble"] = ensemble_p
            probabilities["existing_candidates"] = baseline_p
            masks = {"existing_candidates": valid, **masks}
            print("Replaying development portfolios", flush=True)
            stages, daily = stage_report(dataset, dev, probabilities, masks, valid)
            # Only DEVELOPMENT selects rules; the held-out diagnostic cannot select a winner.
            eligible = [
                name
                for name, m in stages.items()
                if name != "existing_candidates"
                and m["resolved_net"] >= 100
                and (m["net_r"] or -1) > 0
            ]
            champion = (
                max(eligible, key=lambda name: stages[name]["wilson_lower"]) if eligible else None
            )
            write_json(folder / "selection.json", {"champion": champion, "development": stages})
            final_p, final_masks, bundles, inner = {}, {}, {}, {}
            for family, choices in search.items():
                hp, threshold, oof = tune(
                    family, choices, dev, dataset, registry, identifier, "final-fit"
                )
                model = fit(family, hp, dev, dataset.features, dataset.calendar)
                path = folder / f"{family}.joblib"
                joblib.dump(model, path)
                registry.candidate(
                    identifier,
                    family,
                    {**hp, "scope": "artifact"},
                    stages[family],
                    artifact=str(path.relative_to(OUTPUT)),
                    sha=digest(path),
                )
                bundles[family] = (model, threshold)
                inner[family] = oof
            mean_inner = np.mean(np.column_stack(list(inner.values())), axis=1)
            threshold, threshold_grid = select_threshold(dev, mean_inner)
            # The reservation key deliberately ignores content hashes, so changed features
            # cannot reset consumption of the same calendar period.
            scope = f"nse:{split.date()}:{final.armed_on.max().date()}"
            registry.reserve_holdout(scope, identifier)
            for family, (model, t) in bundles.items():
                final_p[family] = model.predict(final)
                final_masks[family] = final_p[family] >= t
            final_agreement = agreement(final_p)
            final_p["ensemble"] = final_agreement["mean"]
            final_p["existing_candidates"] = np.full(len(final), float(dev.label.mean()))
            final_masks["ensemble"] = final_agreement["mean"] >= threshold
            final_masks["ensemble_regime"] = (
                final_masks["ensemble"] & (final.regime == "risk_on").to_numpy()
            )
            final_masks["ensemble_agreement"] = (
                final_masks["ensemble_regime"] & final_agreement["agree"]
            )
            final_masks = {"existing_candidates": np.ones(len(final), dtype=bool), **final_masks}
            write_json(
                folder / "holdout_scores.json",
                {
                    "probabilities": {k: v.tolist() for k, v in final_p.items()},
                    "masks": {k: v.tolist() for k, v in final_masks.items()},
                    "signal_ids": final.signal_id.tolist(),
                },
            )
            print("Replaying frozen historical holdout portfolios", flush=True)
            final_stages, _ = stage_report(
                dataset, final, final_p, final_masks, np.ones(len(final), dtype=bool)
            )
            cpcv_report = (
                stress_test(dataset, dev, search) if stress else {"status": "not requested"}
            )
            # Setup-level PBO on equal calendar sessions and actual portfolio returns.
            setup_returns = []
            for setup in sorted(dev.setup.unique()):
                _, r = replay(
                    dataset,
                    dev.loc[valid].reset_index(drop=True),
                    (dev.loc[valid].setup == setup).to_numpy(),
                )
                setup_returns.append(r)
            matrix = pd.concat(setup_returns, axis=1).fillna(0).to_numpy()
            pbo = probability_of_overfitting(matrix)
            pbo["notes"] = (
                "Three setups only; coarse rankings. This does not test random entry timing."
            )
            # DSR must not use variance across folds as variance across searched strategies.
            # Here the explicit, registered stages are the trials; earlier unrecorded searches
            # make a project-wide DSR unknowable, so label this bounded-scope diagnostic.
            trial_returns = pd.concat(daily, axis=1).fillna(0)
            sd = trial_returns.std(ddof=1)
            srs = (trial_returns.mean() / sd.replace(0, np.nan)).dropna()
            selected_name = champion or "ensemble"
            chosen = daily[selected_name]
            dsr = None
            if len(srs) > 1 and chosen.std(ddof=1) > 0:
                dsr = deflated_sharpe(
                    float(chosen.mean() / chosen.std(ddof=1)),
                    float(srs.std(ddof=1)),
                    len(srs),
                    len(chosen),
                    float(chosen.skew()),
                    float(chosen.kurtosis() + 3),
                )
            report = {
                "id": identifier,
                "metadata": metadata,
                "development": stages,
                "historical_holdout": final_stages,
                "outer_folds": outer_details,
                "threshold_grid": threshold_grid,
                "cpcv": cpcv_report,
                "pbo": pbo,
                "dsr": {
                    "value": dsr,
                    "scope": "displayed stage comparison only",
                    "project_wide": None,
                    "reason": (
                        "Prior searches and full trial return histories are unavailable; "
                        "do not use for promotion."
                    ),
                },
                "champion": champion,
                "eligibility": {
                    "decision": "NO_TRADE",
                    "live_enabled": False,
                    "reasons": [
                        "No new forward validation has completed",
                        "No matched random-timing superiority proof for this filter",
                        "Existing setup eligibility rules remain binding",
                    ],
                },
                "base_integrity": verify_base(),
            }
            predictions = final[
                ["signal_id", "scrip_code", "setup", "armed_on", "label", "net_r"]
            ].copy()
            for family, values in final_p.items():
                predictions[family] = values
            predictions["spread"] = final_agreement["spread"]
            predictions["selected"] = final_masks["ensemble_agreement"]
            predictions.to_json(folder / "predictions.json", orient="records", date_format="iso")
            write_json(folder / "report.json", report)
            registry.finish(identifier, report)
            write_json(OUTPUT / "latest.json", {"id": identifier})
            print(f"Completed research run: {identifier}", flush=True)
            return report
        except Exception as exc:
            registry.finish(identifier, error=f"{type(exc).__name__}: {exc}")
            raise


def stress_test(dataset: Dataset, dev: pd.DataFrame, search: dict) -> dict:
    """Fixed, preregistered first configuration per family; never tune on test blocks."""
    folds, path_map, groups = cpcv(dev, dataset.calendar)
    predictions = []
    for i, fold in enumerate(folds):
        print(f"CPCV fixed-pipeline split {i + 1}/{len(folds)}", flush=True)
        matrix = []
        for family, choices in search.items():
            model = fit(
                family, choices[0], dev.iloc[fold.train], dataset.features, dataset.calendar
            )
            matrix.append(model.predict(dev.iloc[fold.test]))
        p = np.full(len(dev), np.nan)
        p[fold.test] = np.mean(matrix, axis=0)
        predictions.append(p)
    paths = reconstruct(predictions, path_map, groups)
    reports = []
    for i in range(paths.shape[1]):
        m = evaluate(dev, paths[:, i], paths[:, i] >= 0.5)
        econ, _ = replay(dataset, dev, paths[:, i] >= 0.5)
        reports.append({"path": i + 1, **m, "sharpe": econ["sharpe"], "portfolio": econ})
    return {
        "n_groups": 6,
        "held_out_groups": 2,
        "n_combinations": len(folds),
        "n_paths": paths.shape[1],
        "paths": reports,
        "path_map": path_map.tolist(),
        "scope": "Fixed ensemble stress test; future-trained scenarios are not walk-forward",
        "purging": "Actual label intervals plus ten-session post-test embargo",
        "brier_mean": float(np.mean([r["brier"] for r in reports])),
    }
