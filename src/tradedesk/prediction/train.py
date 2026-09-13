"""Dataset, purged walk-forward validation and training (PLAN.md 10.2-10.4).

Dataset: every signal the backtester TRIGGERED, features at the arming close, the
triple-barrier label from the entry session onward, plus the rule-based score for the
plain-score comparison and the realised R for trading-impact metrics.

Validation: purged walk-forward by DATE. Folds are contiguous date blocks; the training
set for a fold ends `embargo` sessions before the test block starts, so overlapping trades
cannot leak. Signals firing on the same day are never split across train and test.

Model: calibrated logistic regression on standardised features (the baseline). LightGBM is
tried only if importable and only kept if it beats the baseline out of sample.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from tradedesk.backtest.runner import BacktestResult, MarketData
from tradedesk.broker.indstocks.models import IST
from tradedesk.engine.lifecycle import SignalState
from tradedesk.prediction.features import FEATURE_NAMES, FEATURE_VERSION, signal_features
from tradedesk.prediction.labeling import label_signal

META_COLUMNS = [
    "signal_id",
    "scrip_code",
    "setup",
    "armed_on",
    "entry_date",
    "label",
    "outcome",
    "realised_r",
    "plain_score",
]


# ------------------------------------------------------------------- dataset


def build_dataset(md: MarketData, result: BacktestResult, *, max_hold: int = 10) -> pd.DataFrame:
    """One row per TRIGGERED signal in the backtest (taken or skipped by the portfolio)."""
    realised = {t.position.signal.id: t.r_multiple for t in result.portfolio.closed}
    rows: list[dict[str, Any]] = []
    for ts in result.signals:
        trig = next((h for h in ts.history if h.to_state is SignalState.TRIGGERED), None)
        if trig is None:
            continue
        sig = ts.signal
        code = sig.scrip_code
        pos = md.pos_by_date.get(code, {}).get(sig.armed_on)
        if pos is None:
            continue
        feats_to_arming = md.features[code].iloc[: pos + 1]
        fill = _fill_from_note(trig.note) or sig.trigger
        lab = label_signal(
            md.features[code],
            entry_date=trig.on,
            entry=fill,
            stop=sig.stop,
            target=sig.t1,
            max_hold=max_hold,
        )
        if lab.outcome == "insufficient":
            continue
        f = signal_features(sig, feats_to_arming, **market_context(md, code, sig.armed_on))
        rows.append(
            {
                "signal_id": sig.id,
                "scrip_code": code,
                "setup": sig.setup.value,
                "armed_on": sig.armed_on,
                "entry_date": trig.on,
                "label": lab.label,
                "outcome": lab.outcome,
                "realised_r": realised.get(sig.id, np.nan),
                "plain_score": np.nan,  # filled by the scan when available
                **f,
            }
        )
    return pd.DataFrame(rows, columns=[*META_COLUMNS, *FEATURE_NAMES])


def _pct_return_1d_5d(closes: pd.Series, on: date) -> tuple[float | None, float | None]:
    s = closes.loc[:on].dropna()
    if len(s) <= 1:
        return None, None
    r1 = (float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100
    r5 = (float(s.iloc[-1]) / float(s.iloc[-6]) - 1) * 100 if len(s) > 5 else None
    return r1, r5


def market_context(md: MarketData, code: str, on: date) -> dict[str, Any]:
    """Breadth, VIX, results proximity and benchmark/sector returns, all as of `on`
    (nothing after it). `nifty_return_1d/5d` reuse `md.benchmark` - already loaded for the
    regime calculation - and `sector_return_1d/5d` (Phase 2, 2026-09-13) reuse
    `md.sector_of`/`md.sector_candles`, populated from config/sector_membership.yaml via the
    CLI's `data_load`/`scan_config` call sites - both None when `code`'s stock has no mapped
    sector or that sector has no loaded candles, the same graceful-degradation pattern as
    every other optional context value here (see FEATURE_VERSION's docstring: only 2 of the
    originally-planned 9 sector indices actually have historical data via INDstocks, so
    coverage here is intentionally partial, not a bug)."""
    breadth = md.breadth.get(on)
    vix_last = vix_chg = None
    if md.vix is not None:
        v = md.vix.loc[:on].dropna()
        if len(v):
            vix_last = float(v.iloc[-1])
            if len(v) > 5:
                vix_chg = (vix_last / float(v.iloc[-6]) - 1) * 100
    nifty_1d = nifty_5d = None
    bench_close = md.benchmark["close"].loc[:on].dropna() if "close" in md.benchmark else None
    if bench_close is not None and len(bench_close) > 1:
        nifty_1d, nifty_5d = _pct_return_1d_5d(bench_close, on)
    sector_1d = sector_5d = None
    sector_name = md.sector_of.get(code)
    if sector_name and sector_name in md.sector_candles:
        sector_1d, sector_5d = _pct_return_1d_5d(md.sector_candles[sector_name], on)
    # PLANNED, NOT BUILT - options context (`nifty_atm_iv`, `nifty_pcr`). scripts/
    # options_snapshot.py has been appending ATM implied volatility and the put/call OI
    # ratio to data/reports/options_snapshot.jsonl daily since 2026-09-13; once that log
    # has a few months of history, those two could join here exactly the way `vix` does
    # above (a `.loc[:on]` lookup against a loaded series). They are deliberately NOT in
    # FEATURE_NAMES yet: INDstocks has no HISTORICAL option-chain endpoint, only a live
    # one, so every already-labelled signal in the training set predates the snapshot log
    # and would get None for 100% of training rows - that is noise, not a feature. Revisit
    # when the log covers a meaningful slice of the dataset's date range.

    return {
        "breadth_pct": float(breadth) if breadth is not None and pd.notna(breadth) else None,
        "vix": vix_last,
        "vix_change_5d": vix_chg,
        "sessions_to_results": _sessions_to_results(md, code, on),
        "nifty_return_1d": nifty_1d,
        "nifty_return_5d": nifty_5d,
        "sector_return_1d": sector_1d,
        "sector_return_5d": sector_5d,
    }


def _fill_from_note(note: str) -> float | None:
    import re

    m = re.search(r"(?:filled|close)\s+([0-9]+(?:\.[0-9]+)?)", note or "")
    return float(m.group(1)) if m else None


def _sessions_to_results(md: MarketData, code: str, on: date) -> int | None:
    import bisect

    dates = md.results_dates.get(code)
    if not dates:
        return None
    k = bisect.bisect_right(dates, on)
    if k >= len(dates):
        return None
    j = bisect.bisect_left(md.calendar, dates[k])
    i = bisect.bisect_left(md.calendar, on)
    return j - i


# ------------------------------------------------------------ walk-forward


@dataclass(frozen=True)
class Fold:
    train_idx: np.ndarray
    test_idx: np.ndarray
    test_start: date
    test_end: date


def purged_walk_forward(
    dates: Sequence[date], *, n_splits: int = 4, embargo_sessions: int = 10, min_train: int = 30
) -> list[Fold]:
    """Contiguous date blocks as test folds; train = everything before the block minus an
    embargo of `embargo_sessions` distinct sessions. Same-day rows always land together."""
    arr = np.array(sorted(set(dates)))
    if len(arr) < n_splits + 1:
        return []
    blocks = np.array_split(
        arr[len(arr) // (n_splits + 1) :], n_splits
    )  # first chunk is train-only
    d = np.array(dates)
    folds: list[Fold] = []
    for block in blocks:
        if len(block) == 0:
            continue
        start, end = block[0], block[-1]
        cutoff_pos = int(np.searchsorted(arr, start)) - embargo_sessions
        if cutoff_pos <= 0:
            continue
        cutoff = arr[cutoff_pos - 1]
        train_idx = np.flatnonzero(d <= cutoff)
        test_idx = np.flatnonzero((d >= start) & (d <= end))
        if len(train_idx) < min_train or len(test_idx) == 0:
            continue
        folds.append(Fold(train_idx, test_idx, start, end))
    return folds


# -------------------------------------------------------------------- model


@dataclass
class Metrics:
    n: int
    brier: float
    base_rate: float
    calibration: list[tuple[float, float, int]]  # (mean predicted, realised rate, count)
    expectancy_all_r: float
    expectancy_above_r: float
    n_above: int
    threshold: float
    roc_auc: float | None = None
    pr_auc: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    log_loss: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "brier": round(self.brier, 4),
            "base_rate": round(self.base_rate, 3),
            "calibration": [(round(p, 3), round(r, 3), c) for p, r, c in self.calibration],
            "expectancy_all_r": round(self.expectancy_all_r, 3),
            "expectancy_above_r": round(self.expectancy_above_r, 3),
            "n_above": self.n_above,
            "threshold": self.threshold,
            "roc_auc": round(self.roc_auc, 4) if self.roc_auc is not None else None,
            "pr_auc": round(self.pr_auc, 4) if self.pr_auc is not None else None,
            "precision": round(self.precision, 4) if self.precision is not None else None,
            "recall": round(self.recall, 4) if self.recall is not None else None,
            "f1": round(self.f1, 4) if self.f1 is not None else None,
            "log_loss": round(self.log_loss, 4) if self.log_loss is not None else None,
        }


def evaluate(
    y: np.ndarray, p: np.ndarray, realised_r: np.ndarray, threshold: float, bins: int = 5
) -> Metrics:
    brier = float(np.mean((p - y) ** 2))
    cal: list[tuple[float, float, int]] = []
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum():
            cal.append((float(p[m].mean()), float(y[m].mean()), int(m.sum())))
    r = realised_r
    ok = np.isfinite(r)
    above = ok & (p >= threshold)

    # Full ML metrics (PDF's asks beyond Brier/calibration): AUC-style metrics need both
    # classes present, and log_loss needs clipped probabilities to avoid -inf on a
    # perfectly-confident wrong prediction - both degrade to None on a degenerate y/p
    # rather than raising, since a single walk-forward fold can legitimately have only
    # one class present.
    roc_auc = pr_auc = precision = recall = f1 = ll = None
    if len(y) and len(np.unique(y)) > 1:
        from sklearn.metrics import (
            average_precision_score,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        from sklearn.metrics import (
            log_loss as _log_loss,
        )

        roc_auc = float(roc_auc_score(y, p))
        pr_auc = float(average_precision_score(y, p))
        pred = (p >= threshold).astype(int)
        precision = float(precision_score(y, pred, zero_division=0))
        recall = float(recall_score(y, pred, zero_division=0))
        f1 = float(f1_score(y, pred, zero_division=0))
        ll = float(_log_loss(y, np.clip(p, 1e-6, 1 - 1e-6)))

    return Metrics(
        n=int(len(y)),
        brier=brier,
        base_rate=float(y.mean()) if len(y) else 0.0,
        calibration=cal,
        expectancy_all_r=float(r[ok].mean()) if ok.any() else 0.0,
        expectancy_above_r=float(r[above].mean()) if above.any() else 0.0,
        n_above=int(above.sum()),
        threshold=threshold,
        roc_auc=roc_auc,
        pr_auc=pr_auc,
        precision=precision,
        recall=recall,
        f1=f1,
        log_loss=ll,
    )


DEFAULT_SEED = 20260101  # fixed so `tradedesk train` is reproducible run-to-run on the same data


def _cv(seed: int) -> Any:
    from sklearn.model_selection import StratifiedKFold

    return StratifiedKFold(n_splits=3, shuffle=True, random_state=seed)


def baseline_hyperparameters(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    return {"C": 0.5, "max_iter": 1000, "random_state": seed, "calibration": "sigmoid", "cv": 3}


def lightgbm_hyperparameters(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    return {
        "n_estimators": 200,
        "learning_rate": 0.03,
        "num_leaves": 15,
        "min_child_samples": 20,
        "random_state": seed,
        "calibration": "isotonic",
        "cv": 3,
    }


def make_baseline(seed: int = DEFAULT_SEED) -> Any:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    base = make_pipeline(
        StandardScaler(), LogisticRegression(C=0.5, max_iter=1000, random_state=seed)
    )
    return CalibratedClassifierCV(base, method="sigmoid", cv=_cv(seed))


def make_lightgbm(seed: int = DEFAULT_SEED) -> Any | None:
    try:
        import lightgbm  # noqa: F401
        from lightgbm import LGBMClassifier
        from sklearn.calibration import CalibratedClassifierCV

        return CalibratedClassifierCV(
            LGBMClassifier(
                n_estimators=200,
                learning_rate=0.03,
                num_leaves=15,
                min_child_samples=20,
                random_state=seed,
                verbose=-1,
            ),
            method="isotonic",
            cv=_cv(seed),
        )
    except Exception:  # noqa: BLE001 - optional dependency (Smart App Control may block it)
        return None


def make_xgboost(seed: int = DEFAULT_SEED) -> Any | None:
    """Same guarded-import pattern as make_lightgbm() - xgboost is a compiled C++ wheel like
    LightGBM, so Smart App Control may block it here too (see CLAUDE.md); degrade to None
    rather than crash `tradedesk train` if it isn't importable."""
    try:
        import xgboost  # noqa: F401
        from sklearn.calibration import CalibratedClassifierCV
        from xgboost import XGBClassifier

        return CalibratedClassifierCV(
            XGBClassifier(
                n_estimators=200,
                learning_rate=0.03,
                max_depth=4,
                min_child_weight=5,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=seed,
                eval_metric="logloss",
            ),
            method="isotonic",
            cv=_cv(seed),
        )
    except Exception:  # noqa: BLE001 - optional dependency (Smart App Control may block it)
        return None


def xgboost_hyperparameters(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    return {
        "n_estimators": 200,
        "learning_rate": 0.03,
        "max_depth": 4,
        "min_child_weight": 5,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": seed,
        "calibration": "isotonic",
        "cv": 3,
    }


# ---------------------------------------------------- hyperparameter grids (tuning)
#
# Small, fixed grids - not optuna/hyperopt, ParameterGrid-style manual loops are enough on a
# dataset this size and keep everything deterministic under `seed`. Each grid entry is
# (hyperparameters actually used, the fitted-estimator prototype) so the winning entry's
# hyperparameters can be recorded verbatim in the saved artifact, rather than a fixed
# constant that may not match what was actually chosen.


def _baseline_grid(seed: int) -> list[tuple[dict[str, Any], Any]]:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    out = []
    for c in (0.1, 0.3, 0.5, 1.0, 3.0):
        base = make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=1000, random_state=seed))  # noqa: E501
        hp = {"C": c, "max_iter": 1000, "random_state": seed, "calibration": "sigmoid", "cv": 3}
        out.append((hp, CalibratedClassifierCV(base, method="sigmoid", cv=_cv(seed))))
    return out


def _lightgbm_grid(seed: int) -> list[tuple[dict[str, Any], Any]]:
    try:
        from lightgbm import LGBMClassifier
        from sklearn.calibration import CalibratedClassifierCV
    except Exception:  # noqa: BLE001 - optional dependency (Smart App Control may block it)
        return []
    out = []
    for n in (100, 200, 400):
        for nl in (7, 15, 31):
            hp = {
                "n_estimators": n, "learning_rate": 0.03, "num_leaves": nl,
                "min_child_samples": 20, "random_state": seed, "calibration": "isotonic", "cv": 3,
            }  # fmt: skip
            model = CalibratedClassifierCV(
                LGBMClassifier(
                    n_estimators=n, learning_rate=0.03, num_leaves=nl,
                    min_child_samples=20, random_state=seed, verbose=-1,
                ),  # fmt: skip
                method="isotonic", cv=_cv(seed),
            )
            out.append((hp, model))
    return out


def _xgboost_grid(seed: int) -> list[tuple[dict[str, Any], Any]]:
    try:
        from sklearn.calibration import CalibratedClassifierCV
        from xgboost import XGBClassifier
    except Exception:  # noqa: BLE001 - optional dependency (Smart App Control may block it)
        return []
    out = []
    for n in (100, 200, 400):
        for d in (3, 4, 6):
            hp = {
                "n_estimators": n, "learning_rate": 0.03, "max_depth": d, "min_child_weight": 5,
                "subsample": 0.8, "colsample_bytree": 0.8, "random_state": seed,
                "calibration": "isotonic", "cv": 3,
            }  # fmt: skip
            model = CalibratedClassifierCV(
                XGBClassifier(
                    n_estimators=n, learning_rate=0.03, max_depth=d, min_child_weight=5,
                    subsample=0.8, colsample_bytree=0.8, random_state=seed, eval_metric="logloss",
                ),  # fmt: skip
                method="isotonic", cv=_cv(seed),
            )
            out.append((hp, model))
    return out


def _coefficients_of(model: Any, features: Sequence[str]) -> dict[str, float] | None:
    """Free-function version of ModelBundle.coefficients() that takes a raw fitted
    estimator instead of `self` - lets the per-fold tuning/stability loop in train() reuse
    the exact same reflection logic the saved bundle uses, instead of a second copy."""
    try:
        cals = model.calibrated_classifiers_
        coefs = np.mean([c.estimator[-1].coef_[0] for c in cals], axis=0)
        return dict(zip(features, (float(x) for x in coefs), strict=True))
    except Exception:  # noqa: BLE001
        return None


def _feature_importance_of(model: Any, features: Sequence[str]) -> dict[str, float] | None:
    """Free-function version of ModelBundle.feature_importance() - see _coefficients_of()."""
    coefs = _coefficients_of(model, features)
    if coefs is not None:
        return dict(sorted(coefs.items(), key=lambda kv: -abs(kv[1])))
    try:
        cals = model.calibrated_classifiers_
        vals = np.mean([c.estimator.feature_importances_ for c in cals], axis=0)
        ranked = sorted(
            zip(features, (float(x) for x in vals), strict=True), key=lambda kv: -kv[1]
        )
        return dict(ranked)
    except Exception:  # noqa: BLE001
        return None


def _feature_stability(
    fold_importances: list[dict[str, float]], top_n: int = 10
) -> tuple[dict[str, list[float]], list[str]]:
    """Per-feature importance across walk-forward folds for the WINNING model, not just the
    single final refit - `feature_importance()` used to run once, on one fit, which is
    exactly why a sign anomaly (e.g. rs_percentile's coefficient pointing the "wrong" way -
    see the sanity check below) could be fold noise or a real problem and nobody could tell
    which. Returns (top-N per-feature value lists by mean |importance|, human-readable
    sign-flip warnings) - a feature whose sign differs across folds is flagged explicitly
    rather than silently averaged away."""
    if not fold_importances:
        return {}, []
    all_features: set[str] = set()
    for d in fold_importances:
        all_features.update(d.keys())
    per_feature = {f: [d.get(f, 0.0) for d in fold_importances] for f in all_features}
    warnings = []
    for f, vals in per_feature.items():
        pos = sum(1 for v in vals if v > 0)
        neg = sum(1 for v in vals if v < 0)
        if pos and neg:
            warnings.append(
                f"unstable sign: {f} (+ in {pos} fold{'s' if pos != 1 else ''}, "
                f"- in {neg} fold{'s' if neg != 1 else ''}) - treat with caution"
            )
    ranked = sorted(per_feature.items(), key=lambda kv: -float(np.mean([abs(v) for v in kv[1]])))
    return dict(ranked[:top_n]), warnings


@dataclass
class ModelBundle:
    version: str
    model: Any
    features: list[str]
    threshold: float
    kind: str
    oos: dict[str, Any]
    trained_on: int
    notes: list[str] = field(default_factory=list)
    training_data_range: tuple[str, str] | None = None
    feature_version: str | None = None
    target_definition: dict[str, Any] = field(default_factory=dict)
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    random_seed: int | None = None
    final_test: dict[str, Any] | None = None
    threshold_grid: list[dict[str, Any]] = field(default_factory=list)
    per_setup: dict[str, dict[str, Any]] | None = None
    feature_stability: dict[str, list[float]] | None = None

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict_proba(X[self.features].to_numpy(dtype=float))[:, 1])

    def coefficients(self) -> dict[str, float] | None:
        """Baseline coefficients (mean over calibration folds) for the sanity check."""
        return _coefficients_of(self.model, self.features)

    def feature_importance(self) -> dict[str, float] | None:
        """Ranked, model-kind-agnostic: logistic's own coefficients (by magnitude) if this
        is the baseline, else a tree model's `feature_importances_` (LightGBM/XGBoost both
        expose it identically on the raw estimator). None if neither is reachable, rather
        than fabricating a ranking - feature importance is not proof of causality either
        way, it just answers "what is the model actually using"."""
        return _feature_importance_of(self.model, self.features)

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.version}.joblib"
        joblib.dump(self, path)
        importance = self.feature_importance()
        (folder / f"{self.version}.json").write_text(
            json.dumps(
                {
                    "version": self.version,
                    "kind": self.kind,
                    "threshold": self.threshold,
                    "oos": self.oos,
                    "trained_on": self.trained_on,
                    "notes": self.notes,
                    "feature_list": self.features,
                    "training_data_range": self.training_data_range,
                    "feature_version": self.feature_version,
                    "target_definition": self.target_definition,
                    "hyperparameters": self.hyperparameters,
                    "random_seed": self.random_seed,
                    "final_test": self.final_test,
                    "threshold_grid": self.threshold_grid,
                    "per_setup": self.per_setup,
                    "feature_importance": (
                        dict(list((importance or {}).items())[:10])
                    ),
                    "feature_stability": self.feature_stability,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load(path: Path) -> ModelBundle:
        obj = joblib.load(path)
        assert isinstance(obj, ModelBundle)
        return obj


def select_threshold(
    y: np.ndarray,
    p: np.ndarray,
    realised_r: np.ndarray,
    grid: Sequence[float] | None = None,
    min_above: int = 20,
) -> tuple[float, list[dict[str, Any]], bool]:
    """Pick the probability threshold that maximises TOTAL R captured
    (expectancy_above_r * n_above) among thresholds that keep at least `min_above` trades -
    not just any threshold, and not just average R. Both guards are load-bearing, found by
    actually running this on real NSE data (2023-2026): without the sample-size floor,
    "maximise total R" degenerates into "pick whichever threshold happens to leave the
    fewest trades", since a threshold with 1 leftover trade can have the least-negative
    total R purely by having almost nothing left to lose, not because it identifies a
    better trade - the real run's default grid had EVERY threshold net-negative, and the
    naive version chose 0.80 because it left exactly one trade. Returns the winning
    threshold, the full grid (each row flagged `eligible` if it met `min_above`), and a
    third `has_edge` bool - False when even the winning eligible threshold's own
    expectancy is not positive, so a caller can say "no threshold shows real improvement"
    instead of silently presenting a best-of-a-bad-bunch pick as if it were a discovery.

    Evaluated only on the data passed in - callers must pass walk-forward VALIDATION
    predictions, never the locked final test set, or the selection stops being an honest
    out-of-sample choice (see train()'s final_test_frac)."""
    grid = list(grid) if grid is not None else [round(0.50 + 0.05 * i, 2) for i in range(7)]
    ok = np.isfinite(realised_r)
    rows: list[dict[str, Any]] = []
    for t in grid:
        above = ok & (p >= t)
        n_above = int(above.sum())
        exp_r = float(realised_r[above].mean()) if n_above else 0.0
        rows.append(
            {
                "threshold": t,
                "n_above": n_above,
                "expectancy_above_r": round(exp_r, 3),
                "total_r": round(exp_r * n_above, 2),
                "eligible": n_above >= min_above,
            }
        )
    eligible = [row for row in rows if row["eligible"]]
    pool = eligible if eligible else rows  # no threshold met the floor: fall back to the full grid
    best = max(pool, key=lambda row: row["total_r"])
    has_edge = bool(eligible) and best["expectancy_above_r"] > 0
    return float(best["threshold"]), rows, has_edge


def per_setup_breakdown(
    setups: np.ndarray,
    mask: np.ndarray,
    oos_p: np.ndarray,
    y: np.ndarray,
    r: np.ndarray,
    threshold: float,
    min_above: int = 20,
) -> dict[str, dict[str, Any]]:
    """The pooled OOS number (`oos` in train()) can hide a real edge in one setup under
    noise from another - a single 0.556 AUC could mean every setup is equally weak, or it
    could mean one setup works and the others are dragging it down. Runs the exact same
    evaluate()/select_threshold() machinery `train()` already uses, once per setup, on
    validation-fold OOS predictions only (never the locked final test set - same rule as
    everywhere else in this module). A setup with fewer than `min_above` resolved OOS rows
    is skipped rather than reported with a single-digit-sample AUC that would look precise
    but mean nothing."""
    out: dict[str, dict[str, Any]] = {}
    for setup in sorted(set(setups[mask])):
        sel = mask & (setups == setup)
        if int(sel.sum()) < min_above:
            continue
        m = evaluate(y[sel], oos_p[sel], r[sel], threshold)
        _, _, has_edge = select_threshold(y[sel], oos_p[sel], r[sel], min_above=min_above)
        out[setup] = {**m.to_dict(), "has_edge": has_edge}
    return out


@dataclass
class TrainReport:
    folds: list[dict[str, Any]]
    oos: Metrics | None
    plain_expectancy_above_r: float | None
    bundle: ModelBundle | None
    notes: list[str]
    final_test: Metrics | None = None
    threshold_grid: list[dict[str, Any]] | None = None
    per_setup: dict[str, dict[str, Any]] | None = None

    def text(self) -> str:
        lines = [f"folds: {len(self.folds)}"]
        for f in self.folds:
            lines.append(
                f"  {f['test_start']} - {f['test_end']}: n={f['n']} "
                f"brier={f['brier']:.3f} base={f['base_rate']:.2f}"
            )
        if self.oos:
            o = self.oos.to_dict()
            lines.append(f"validation (walk-forward, used for model/threshold selection): {o}")
        if self.threshold_grid:
            lines.append(f"threshold grid (validation only): {self.threshold_grid}")
        if self.per_setup:
            lines.append("per-setup breakdown (validation, min_above-eligible setups only):")
            for setup, m in self.per_setup.items():
                lines.append(
                    f"  {setup}: auc={m.get('roc_auc')} "
                    f"expectancy_above_r={m['expectancy_above_r']:+.3f} "
                    f"n_above={m['n_above']} has_edge={m['has_edge']}"
                )
        if self.final_test:
            lines.append(f"FINAL TEST (locked, scored once): {self.final_test.to_dict()}")
        if self.plain_expectancy_above_r is not None:
            lines.append(f"plain-score top half expectancy: {self.plain_expectancy_above_r:+.3f}R")
        lines.extend(self.notes)
        return "\n".join(lines)


def train(
    df: pd.DataFrame,
    *,
    n_splits: int = 4,
    embargo_sessions: int = 10,
    threshold: float = 0.5,
    prefer_lightgbm: bool = True,
    prefer_xgboost: bool = True,
    seed: int = DEFAULT_SEED,
    final_test_frac: float = 0.2,
    auto_threshold: bool = True,
    max_hold: int | None = None,
) -> TrainReport:
    notes: list[str] = []
    if df.empty or df["label"].nunique() < 2:
        return TrainReport(
            [], None, None, None, ["not enough labelled signals (need both classes)"]
        )

    dates_all = [d if isinstance(d, date) else pd.Timestamp(d).date() for d in df["armed_on"]]
    df = df.assign(_armed_date=dates_all)
    unique_dates = sorted(set(dates_all))

    # Phase 1a: reserve the most recent `final_test_frac` of history, untouched by
    # walk-forward fold selection or threshold search - "a final period should remain
    # untouched during model development" (only evaluated once, below, after everything
    # else is finalised).
    dev_df, final_df = df, df.iloc[0:0]
    if 0 < final_test_frac < 1 and len(unique_dates) >= n_splits + 2:
        split_at = unique_dates[int(len(unique_dates) * (1 - final_test_frac))]
        dev_mask = df["_armed_date"] < split_at
        if dev_mask.sum() >= 30 and (~dev_mask).sum() >= 5:
            dev_df, final_df = df[dev_mask], df[~dev_mask]
        else:
            notes.append("not enough history to hold out a final test set; using all data")
    else:
        notes.append("not enough history to hold out a final test set; using all data")

    X = dev_df[FEATURE_NAMES].to_numpy(dtype=float)
    y = dev_df["label"].to_numpy(dtype=int)
    r = dev_df["realised_r"].to_numpy(dtype=float)
    dates = list(dev_df["_armed_date"])
    if len(np.unique(y)) < 2:
        return TrainReport(
            [], None, None, None, ["dev split has only one class after reserving the final test set"]  # noqa: E501
        )
    folds = purged_walk_forward(dates, n_splits=n_splits, embargo_sessions=embargo_sessions)
    if not folds:
        return TrainReport(
            [], None, None, None, ["not enough history for a purged walk-forward split"]
        )

    # Hyperparameter tuning (Phase 1.1, 2026-09-13): each kind now searches a small grid
    # instead of one fixed, guessed config, scored the same way model-selection already
    # worked (lowest pooled OOS Brier across the dev-only walk-forward folds) - never the
    # locked final test set. This is a straightforward widening of "pick among 3 fixed
    # models" to "pick among 3 families x a small grid of settings per family", using the
    # exact same folds/brier/OOS-prediction machinery, so it introduces no new leakage
    # surface. Along the way, also collect each fold's feature importance for the eventual
    # winner (Phase 1.3) - see _feature_stability().
    grids: dict[str, list[tuple[dict[str, Any], Any]]] = {"logistic": _baseline_grid(seed)}
    if prefer_lightgbm:
        lgb_grid = _lightgbm_grid(seed)
        if lgb_grid:
            grids["lightgbm"] = lgb_grid
        else:
            notes.append("lightgbm not available; baseline only")
    if prefer_xgboost:
        xgb_grid = _xgboost_grid(seed)
        if xgb_grid:
            grids["xgboost"] = xgb_grid
        else:
            notes.append("xgboost not available")

    # kind -> (hp, proto, fold_rows, oos_p, mask, fold_importances) for the WINNING config
    # within that kind (selected by pooled OOS Brier among the kind's own grid).
    per_kind_best: dict[str, tuple[dict[str, Any], Any, list[dict[str, Any]], np.ndarray, np.ndarray, list[dict[str, float]]]] = {}  # noqa: E501
    for kind, grid in grids.items():
        kind_candidates = []
        for hp, proto in grid:
            fold_rows = []
            oos_p = np.full(len(y), np.nan)
            fold_importances = []
            for f in folds:
                model = _clone(proto)
                if len(np.unique(y[f.train_idx])) < 2:
                    continue
                model.fit(X[f.train_idx], y[f.train_idx])
                p = model.predict_proba(X[f.test_idx])[:, 1]
                oos_p[f.test_idx] = p
                m = evaluate(y[f.test_idx], p, r[f.test_idx], threshold)
                fold_rows.append({"test_start": f.test_start, "test_end": f.test_end, **m.to_dict()})  # noqa: E501
                imp = _feature_importance_of(model, FEATURE_NAMES)
                if imp is not None:
                    fold_importances.append(imp)
            config_mask = np.isfinite(oos_p)
            brier = float(np.mean((oos_p[config_mask] - y[config_mask]) ** 2)) if config_mask.any() else 1.0  # noqa: E501
            kind_candidates.append((brier, hp, proto, fold_rows, oos_p, config_mask, fold_importances))  # noqa: E501
        winner = min(kind_candidates, key=lambda t: t[0])
        per_kind_best[kind] = winner[1:]

    def brier_of(kind: str) -> float:
        _, _, _, p, mask, _ = per_kind_best[kind]
        return float(np.mean((p[mask] - y[mask]) ** 2)) if mask.any() else 1.0

    best = min(per_kind_best, key=brier_of)
    if best != "logistic":
        notes.append(
            f"{best} beat the baseline out of sample "
            f"({brier_of(best):.4f} vs {brier_of('logistic'):.4f})"
        )
    best_hp, best_proto, rows, oos_p, mask, best_fold_importances = per_kind_best[best]

    # Phase 2c: threshold selection on validation (dev walk-forward OOS) predictions ONLY -
    # never on the final test set reserved above, or the "lock before final test" guarantee
    # is worthless.
    threshold_grid: list[dict[str, Any]] = []
    chosen_threshold = threshold
    if auto_threshold and mask.any():
        chosen_threshold, threshold_grid, has_edge = select_threshold(y[mask], oos_p[mask], r[mask])
        if has_edge:
            notes.append(f"threshold auto-selected on validation folds: {chosen_threshold}")
        else:
            notes.append(
                f"threshold auto-selected on validation folds: {chosen_threshold}, but NO "
                "threshold in the grid showed positive expectancy with an adequate sample "
                "(min_above) - this is the least-bad option, not a discovered edge"
            )

    oos = evaluate(y[mask], oos_p[mask], r[mask], chosen_threshold) if mask.any() else None

    # Per-setup breakdown (Phase 1.2): the pooled `oos` above can hide a real edge in one
    # setup under noise from another - see per_setup_breakdown()'s docstring.
    per_setup = (
        per_setup_breakdown(dev_df["setup"].to_numpy(), mask, oos_p, y, r, chosen_threshold)
        if mask.any() and "setup" in dev_df.columns
        else None
    )

    # Feature-importance stability across folds (Phase 1.3), for the winning kind only -
    # see _feature_stability()'s docstring for why this matters.
    stability, stability_warnings = _feature_stability(best_fold_importances)
    notes.extend(stability_warnings)

    plain_above = None
    if dev_df["plain_score"].notna().any():
        ps = dev_df["plain_score"].to_numpy(dtype=float)
        top = np.isfinite(r) & (ps >= np.nanmedian(ps))
        plain_above = float(r[top].mean()) if top.any() else None

    final = _clone(best_proto)
    final.fit(X, y)

    # Score the locked final test set exactly once, with the already-chosen model and
    # threshold - this is the number that should be trusted, not the validation folds
    # (which fed model AND threshold selection above).
    final_metrics = None
    if len(final_df):
        Xf = final_df[FEATURE_NAMES].to_numpy(dtype=float)
        yf = final_df["label"].to_numpy(dtype=int)
        rf = final_df["realised_r"].to_numpy(dtype=float)
        pf = final.predict_proba(Xf)[:, 1]
        final_metrics = evaluate(yf, pf, rf, chosen_threshold)

    bundle = ModelBundle(
        version=datetime.now(IST).strftime("%Y%m%d-%H%M%S"),
        model=final,
        features=list(FEATURE_NAMES),
        threshold=chosen_threshold,
        kind=best,
        oos=oos.to_dict() if oos else {},
        trained_on=int(len(y)),
        notes=notes,
        training_data_range=(str(min(dates)), str(max(dates))) if dates else None,
        feature_version=FEATURE_VERSION,
        target_definition={"labeling": "triple_barrier", "max_hold": max_hold},
        hyperparameters=best_hp,
        random_seed=seed,
        final_test=final_metrics.to_dict() if final_metrics else None,
        threshold_grid=threshold_grid,
        per_setup=per_setup,
        feature_stability=stability or None,
    )
    coefs = bundle.coefficients()
    if coefs is not None:
        for name, sign in (("rs_percentile", 1), ("stop_atr", -1)):
            if name in coefs and coefs[name] * sign < 0:
                notes.append(
                    f"sanity: coefficient of {name} has an unexpected sign ({coefs[name]:+.3f})"
                )
    bundle.notes = notes
    return TrainReport(rows, oos, plain_above, bundle, notes, final_test=final_metrics, threshold_grid=threshold_grid, per_setup=per_setup)  # noqa: E501


def train_per_setup(
    df: pd.DataFrame, *, min_rows: int = 200, **train_kwargs: Any
) -> dict[str, TrainReport]:
    """One independently-tuned model PER SETUP instead of a single model pooled across all
    of them (2026-09-13, real-data motivated): `nr7_breakout` is 81% of the training rows
    (24,358 of 29,964) and has the weakest per-setup signal (AUC ~0.54-0.57), so pooling all
    three into one training run lets it dominate hyperparameter selection and drown out
    `base_breakout`'s comparatively real (if still modest, AUC ~0.59) signal - confirmed by
    every per_setup_breakdown() this project has run. Training separately means each setup
    gets its own tuned hyperparameters, its own calibration, its own threshold, and its own
    locked final test set - the exact same rigor `train()` already has, just not diluted by
    a different setup's noise.

    Reuses `train()` completely unmodified (calls it once per setup on that setup's own
    slice) rather than duplicating any of its walk-forward/tuning/calibration logic - this
    function is a thin fan-out, not a second training pipeline.

    A setup with fewer than `min_rows` resolved signals is skipped with an explicit note in
    the returned dict (key still present, `TrainReport` with `bundle=None`) rather than
    trained on a sample too small to trust - same "min_above" discipline used everywhere
    else in this module (select_threshold, per_setup_breakdown)."""
    reports: dict[str, TrainReport] = {}
    for setup in sorted(df["setup"].dropna().unique()):
        sub = df[df["setup"] == setup].reset_index(drop=True)
        if len(sub) < min_rows:
            reports[setup] = TrainReport(
                [], None, None, None,
                [f"skipped: only {len(sub)} resolved signals for {setup!r}, need >= {min_rows}"],
            )  # fmt: skip
            continue
        rep = train(sub, **train_kwargs)
        if rep.bundle is not None:
            # Distinguish each setup's saved artifact - train()'s version is a bare
            # timestamp, and three setups trained in the same run/second would otherwise
            # collide on the same "<timestamp>.joblib" filename and silently overwrite
            # each other.
            rep.bundle.version = f"{rep.bundle.version}-{setup}"
        reports[setup] = rep
    return reports


def _clone(model: Any) -> Any:
    from sklearn.base import clone

    return clone(model)


# ------------------------------------------------------ economic comparison


def compare_strategies(
    result: BacktestResult,
    bundle: ModelBundle,
    df: pd.DataFrame,
    starting_capital: float,
    *,
    setup: str | None = None,
) -> dict[str, Any]:
    """Strategy A (existing rules, every triggered signal) vs Strategy C (rules + ML filter
    at the bundle's threshold) - the actual economic question, not just a classification
    metric: does the ML layer improve Sharpe/CAGR/drawdown/profit factor over the existing
    rules, or just look good on Brier/AUC? Strategy B (ML-only, no rule gate) is
    deliberately NOT built: nothing in this codebase generates a trade candidate without a
    setup firing first (engine/engine.py::scan_day is the only place a signal is created,
    by design - CLAUDE.md hard rule), so there is no "ML-only" population of trades to
    construct without inventing a new, unsanctioned signal source.

    Joins each closed trade to its probability via the same `t.position.signal.id` link
    build_dataset() already uses, scored by the already-TRAINED bundle - this never
    retrains and never re-simulates the portfolio's sizing/heat/crowding limits, so it is a
    trade-selection comparison over the ONE backtest that was actually run, not two
    independent backtests with different position-sizing behaviour. See
    backtest/reports.py::equity_curve_from_trades for the simplification this relies on for
    Sharpe/CAGR/drawdown (a serially-compounded trade equity curve, not a bar-by-bar,
    capital-constrained one)."""
    from tradedesk.backtest import reports

    # `setup` (2026-09-13, added for train_per_setup()'s CLI reporting): a per-setup bundle
    # was only ever tuned on ONE setup's feature distribution, so scoring every OTHER
    # setup's rows with it (and comparing against Strategy A's full, all-setups trade
    # population) would be a real correctness bug, not a smaller/faster comparison -
    # base_breakout's model has no business judging a trend_pullback trade. Both `df` and
    # the closed-trade population are restricted to `setup` before anything else runs, so
    # Strategy A and Strategy C are the same, single-setup population - the default
    # `setup=None` keeps every existing (pooled-model) call site's behaviour identical.
    if setup is not None:
        df = df[df["setup"] == setup]

    probs: dict[str, float] = {}
    if len(df):
        p = bundle.predict_proba(df)
        probs = dict(zip(df["signal_id"], p, strict=True))

    all_trades = list(result.portfolio.closed)
    if setup is not None:
        all_trades = [t for t in all_trades if t.position.signal.setup.value == setup]
    filtered = [t for t in all_trades if probs.get(t.position.signal.id, 0.0) >= bundle.threshold]

    start = result.calendar[0] if result.calendar else date.today()
    end = result.calendar[-1] if result.calendar else date.today()

    def side(trades: list[Any]) -> dict[str, Any]:
        m = reports.metrics(trades)
        eq = reports.equity_curve_from_trades(trades, result.calendar, starting_capital)
        daily_ret = reports.log_returns(eq).dropna()
        return {
            **m.to_row(),
            "sharpe": round(reports.sharpe_ratio(daily_ret), 3),
            "cagr": round(reports.cagr(eq, start, end), 4),
            "max_drawdown": round(reports.max_drawdown(eq), 4),
        }

    return {
        "strategy_a_existing_rules": side(all_trades),
        "strategy_c_rules_plus_ml": side(filtered),
        "threshold_used": bundle.threshold,
        "note": (
            "Strategy B (ML-only, no rule gate) is architecturally out of scope - "
            "engine/engine.py::scan_day is the only signal source in this codebase"
        ),
    }
