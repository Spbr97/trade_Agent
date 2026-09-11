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
from tradedesk.prediction.features import FEATURE_NAMES, signal_features
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


def market_context(md: MarketData, code: str, on: date) -> dict[str, Any]:
    """Breadth, VIX and results proximity as of `on` (nothing after it)."""
    breadth = md.breadth.get(on)
    vix_last = vix_chg = None
    if md.vix is not None:
        v = md.vix.loc[:on].dropna()
        if len(v):
            vix_last = float(v.iloc[-1])
            if len(v) > 5:
                vix_chg = (vix_last / float(v.iloc[-6]) - 1) * 100
    return {
        "breadth_pct": float(breadth) if breadth is not None and pd.notna(breadth) else None,
        "vix": vix_last,
        "vix_change_5d": vix_chg,
        "sessions_to_results": _sessions_to_results(md, code, on),
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
    return Metrics(
        n=int(len(y)),
        brier=brier,
        base_rate=float(y.mean()) if len(y) else 0.0,
        calibration=cal,
        expectancy_all_r=float(r[ok].mean()) if ok.any() else 0.0,
        expectancy_above_r=float(r[above].mean()) if above.any() else 0.0,
        n_above=int(above.sum()),
        threshold=threshold,
    )


def make_baseline() -> Any:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    base = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=1000))
    return CalibratedClassifierCV(base, method="sigmoid", cv=3)


def make_lightgbm() -> Any | None:
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
                verbose=-1,
            ),
            method="isotonic",
            cv=3,
        )
    except Exception:  # noqa: BLE001 - optional dependency (Smart App Control may block it)
        return None


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

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict_proba(X[self.features].to_numpy(dtype=float))[:, 1])

    def coefficients(self) -> dict[str, float] | None:
        """Baseline coefficients (mean over calibration folds) for the sanity check."""
        try:
            cals = self.model.calibrated_classifiers_
            coefs = np.mean([c.estimator[-1].coef_[0] for c in cals], axis=0)
            return dict(zip(self.features, (float(x) for x in coefs), strict=True))
        except Exception:  # noqa: BLE001
            return None

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.version}.joblib"
        joblib.dump(self, path)
        (folder / f"{self.version}.json").write_text(
            json.dumps(
                {
                    "version": self.version,
                    "kind": self.kind,
                    "threshold": self.threshold,
                    "oos": self.oos,
                    "trained_on": self.trained_on,
                    "notes": self.notes,
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


@dataclass
class TrainReport:
    folds: list[dict[str, Any]]
    oos: Metrics | None
    plain_expectancy_above_r: float | None
    bundle: ModelBundle | None
    notes: list[str]

    def text(self) -> str:
        lines = [f"folds: {len(self.folds)}"]
        for f in self.folds:
            lines.append(
                f"  {f['test_start']} - {f['test_end']}: n={f['n']} "
                f"brier={f['brier']:.3f} base={f['base_rate']:.2f}"
            )
        if self.oos:
            o = self.oos.to_dict()
            lines.append(f"out of sample: {o}")
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
) -> TrainReport:
    notes: list[str] = []
    if df.empty or df["label"].nunique() < 2:
        return TrainReport(
            [], None, None, None, ["not enough labelled signals (need both classes)"]
        )
    X = df[FEATURE_NAMES].to_numpy(dtype=float)
    y = df["label"].to_numpy(dtype=int)
    r = df["realised_r"].to_numpy(dtype=float)
    dates = [d if isinstance(d, date) else pd.Timestamp(d).date() for d in df["armed_on"]]
    folds = purged_walk_forward(dates, n_splits=n_splits, embargo_sessions=embargo_sessions)
    if not folds:
        return TrainReport(
            [], None, None, None, ["not enough history for a purged walk-forward split"]
        )

    candidates: list[tuple[str, Any]] = [("logistic", make_baseline())]
    lgb = make_lightgbm() if prefer_lightgbm else None
    if lgb is not None:
        candidates.append(("lightgbm", lgb))
    else:
        notes.append("lightgbm not available; baseline only")

    results: dict[str, tuple[list[dict[str, Any]], np.ndarray, np.ndarray]] = {}
    for kind, proto in candidates:
        fold_rows = []
        oos_p = np.full(len(y), np.nan)
        for f in folds:
            model = _clone(proto)
            if len(np.unique(y[f.train_idx])) < 2:
                continue
            model.fit(X[f.train_idx], y[f.train_idx])
            p = model.predict_proba(X[f.test_idx])[:, 1]
            oos_p[f.test_idx] = p
            m = evaluate(y[f.test_idx], p, r[f.test_idx], threshold)
            fold_rows.append({"test_start": f.test_start, "test_end": f.test_end, **m.to_dict()})
        mask = np.isfinite(oos_p)
        results[kind] = (fold_rows, oos_p, mask)

    def brier_of(kind: str) -> float:
        rows, p, mask = results[kind]
        return float(np.mean((p[mask] - y[mask]) ** 2)) if mask.any() else 1.0

    best = min(results, key=brier_of)
    if best != "logistic":
        notes.append(
            "lightgbm beat the baseline out of sample "
            f"({brier_of('lightgbm'):.4f} vs {brier_of('logistic'):.4f})"
        )
    rows, oos_p, mask = results[best]
    oos = evaluate(y[mask], oos_p[mask], r[mask], threshold) if mask.any() else None

    plain_above = None
    if df["plain_score"].notna().any():
        ps = df["plain_score"].to_numpy(dtype=float)
        top = np.isfinite(r) & (ps >= np.nanmedian(ps))
        plain_above = float(r[top].mean()) if top.any() else None

    final = _clone(dict(candidates)[best])
    final.fit(X, y)
    bundle = ModelBundle(
        version=datetime.now(IST).strftime("%Y%m%d-%H%M%S"),
        model=final,
        features=list(FEATURE_NAMES),
        threshold=threshold,
        kind=best,
        oos=oos.to_dict() if oos else {},
        trained_on=int(len(y)),
        notes=notes,
    )
    coefs = bundle.coefficients()
    if coefs is not None:
        for name, sign in (("rs_percentile", 1), ("stop_atr", -1)):
            if name in coefs and coefs[name] * sign < 0:
                notes.append(
                    f"sanity: coefficient of {name} has an unexpected sign ({coefs[name]:+.3f})"
                )
    bundle.notes = notes
    return TrainReport(rows, oos, plain_above, bundle, notes)


def _clone(model: Any) -> Any:
    from sklearn.base import clone

    return clone(model)
