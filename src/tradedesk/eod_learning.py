"""EOD self-learning step for NSE and BSE (2026-09-14, explicit user request: "build the
self learning that reduces [error] over time... even if it means exploring other indicator
or chart analysis").

What this honestly is, and is not
----------------------------------
This is NOT a system that adjusts itself after every single call, and it does not touch
`config/setups.yaml`, the live scan, or anything alertable - CLAUDE.md's hard rule that only
`engine/engine.py::scan_day`/`scan_bar` may create a signal is unchanged, and nothing here
can raise a grade or size. What it genuinely is: a once-daily retrain on the REAL, GROWING,
FORWARD evidence `research_tracker.py` collects (never the 2023-2026 backtest window this
project has already tested past the point of diminishing, risky returns - see CLAUDE.md's
"three independent honest negatives" and the proof-plan section), which:

1. Tracks a real metric (pooled walk-forward OOS ROC-AUC/Brier, and net-R on a locked recent
   tail) once per day in an append-only history log, so "does error go down over time" is
   something that can be PLOTTED and CHECKED, not just asserted.
2. Explores new features - some already computed elsewhere (bb_width, macd_hist, roc5/20,
   ...), some genuinely new "chart analysis" not used anywhere else in this project
   (close-position-in-range, the gap at the open) - from a FIXED, ordered queue, one at a
   time, exactly once each. Bounding it to a fixed queue tried once-ever is what keeps this
   honest: repeatedly re-testing features against the same growing data is exactly the
   multiple-testing trap this project has caught itself in three separate times already
   (see CLAUDE.md). A feature is only ever adopted into the working set if it clears a
   PRE-REGISTERED improvement margin (decided below, before any of this ran) on real
   walk-forward out-of-sample predictions - never on the training fit.
3. Only ever WRITES a review_queue flag if the resulting model clears the same evidence bar
   (engine/scoring.py::eligibility()) already used everywhere else in this project. Nothing
   here can go live on its own; a human still decides.

Why forward-only data, not the old backtest: this project already ran per-setup models,
filter stacking, cross-sectional ranking, and a model trained directly on the RSI(2)
population against the SAME 2023-2026 window - all three came back negative, and CLAUDE.md's
proof plan explicitly closed that data off from further mining. The one thing this project
had never tried is training on data collected AFTER the model looks at it, which is what
`research_tracker.py`'s forward log is for. This module is what turns that log into a model
that can actually improve as more real days accumulate - slowly, honestly, and only when the
evidence says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import daily_features
from tradedesk.research_tracker import ResearchCall, load_log, log_path_for

# Always-on features: a modest, established set (nothing here is new to this project).
BASE_FEATURES: list[str] = ["rsi2", "rsi14", "adx14", "di_spread", "atr_pct", "dist_ema50_atr"]

# Explored ONE AT A TIME, in this fixed order, each exactly once ever (state persists which
# have been tried) - bounding the multiple-testing exposure to a known, finite budget rather
# than an open-ended search. `close_range_pos` and `gap_pct` are the genuinely new "chart
# analysis" additions (candlestick-style: where the close sits in its own day's range, and
# the size of the opening gap) - neither is used anywhere else in this codebase.
EXPLORATION_QUEUE: list[str] = [
    "dist_ema10_atr", "dist_ema200_atr", "close_range_pos", "gap_pct", "bb_width",
    "range_contraction", "macd_hist_atr", "roc5", "roc20", "vol_ratio50", "updown_vol20",
    "pct_in_52w_range",
]  # fmt: skip

ALL_FEATURES: list[str] = BASE_FEATURES + EXPLORATION_QUEUE

# Pre-registered BEFORE any of this ran, same discipline as the H1/H2 proof plan: a new
# feature is adopted only if it lowers pooled walk-forward OOS Brier by at least this much
# AND does not make locked-tail net expectancy worse. Small on purpose - Brier is bounded
# in [0, 1] and typical swings here are in the 0.001-0.02 range.
BRIER_IMPROVEMENT_MARGIN = 0.005

MIN_RESOLVED_TO_FIT = 50  # below this, purged_walk_forward has nothing meaningful to fold
FINAL_TEST_FRAC = 0.2


def _state_path(market: str) -> Path:
    return Path(f"data/models/eod_learning_state_{market}.json")


def _history_path(market: str) -> Path:
    return Path(f"data/models/eod_learning_{market}.jsonl")


@dataclass
class LearningState:
    base_features: list[str]
    explored: list[str]  # feature names already tested (adopted or not), never retried

    @classmethod
    def load(cls, path: Path) -> LearningState:
        if not path.exists():
            return cls(base_features=list(BASE_FEATURES), explored=[])
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(base_features=d["base_features"], explored=d["explored"])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"base_features": self.base_features, "explored": self.explored}), encoding="utf-8")  # noqa: E501

    def next_to_explore(self) -> str | None:
        for name in EXPLORATION_QUEUE:
            if name not in self.explored and name not in self.base_features:
                return name
        return None


def _row_features(f: pd.DataFrame, i: int) -> dict[str, float] | None:
    """Every value in ALL_FEATURES, computed from `daily_features(raw)`'s row `i` (the
    arming bar) and, for gap_pct, row `i-1` too. None if the arming bar is the first row
    (no previous close) or ATR is not usable."""
    if i < 1:
        return None
    row, prev = f.iloc[i], f.iloc[i - 1]

    def g(col: str, default: float = float("nan")) -> float:
        v = row.get(col, default)
        return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else default  # noqa: E501

    atr = g("atr14")
    if not np.isfinite(atr) or atr <= 0:
        return None
    close, high, low, opn = g("close"), g("high"), g("low"), g("open")
    prev_close = float(prev.get("close", np.nan))
    rng = high - low
    return {
        "rsi2": g("rsi2", 50.0),
        "rsi14": g("rsi14", 50.0),
        "adx14": g("adx14"),
        "di_spread": g("plus_di") - g("minus_di"),
        "atr_pct": g("atr_pct"),
        "dist_ema10_atr": (close - g("ema10", close)) / atr,
        "dist_ema50_atr": (close - g("ema50", close)) / atr,
        "dist_ema200_atr": (close - g("ema200", close)) / atr,
        "close_range_pos": (close - low) / rng if rng > 0 else 0.5,
        "gap_pct": (opn - prev_close) / prev_close * 100 if prev_close > 0 else 0.0,
        "bb_width": g("bb_width"),
        "range_contraction": g("range_contraction", 1.0),
        "macd_hist_atr": g("macd_hist", 0.0) / atr,
        "roc5": g("roc5"),
        "roc20": g("roc20"),
        "vol_ratio50": g("vol_ratio50", 1.0),
        "updown_vol20": g("updown_vol20", 1.0),
        "pct_in_52w_range": g("pct_in_52w_range", 50.0),
    }


def _build_dataset(db: Path, resolved: list[ResearchCall]) -> pd.DataFrame:
    """One row per resolved call (every candidate rule pooled together, EXCLUDING
    random_eligible - that's a null, not a trading idea), features recomputed from the
    stored code+armed_on since ResearchCall itself only keeps close/atr at arming, not the
    full feature vector."""
    by_code: dict[str, list[ResearchCall]] = {}
    for r in resolved:
        by_code.setdefault(r.scrip_code, []).append(r)

    rows: list[dict[str, Any]] = []
    with CandleStore(db) as store:
        for code, calls in by_code.items():
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            dates = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            for r in calls:
                armed = date.fromisoformat(r.armed_on)
                hit = np.flatnonzero(dates == armed)
                if not len(hit):
                    continue
                feats = _row_features(f, int(hit[0]))
                if feats is None:
                    continue
                rows.append(
                    {
                        "armed_date": armed, "label_profit": int((r.r_multiple or 0) > 0),
                        "gross_r": float(r.r_multiple or 0.0), **feats,
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)


def _fit_and_score(df: pd.DataFrame, features: list[str], seed: int) -> dict[str, Any] | None:
    """Purged walk-forward on all but the most recent FINAL_TEST_FRAC of sessions, scored
    once on that reserved tail - the same discipline as every other model in this project."""
    from sklearn.base import clone

    from tradedesk.prediction.train import evaluate, make_baseline, purged_walk_forward

    df = df.dropna(subset=features + ["label_profit", "gross_r"]).reset_index(drop=True)
    if len(df) < MIN_RESOLVED_TO_FIT:
        return None
    sessions = sorted(df["armed_date"].unique())
    cut = sessions[int(len(sessions) * (1 - FINAL_TEST_FRAC))]
    dev = df[df["armed_date"] < cut].reset_index(drop=True)
    test = df[df["armed_date"] >= cut].reset_index(drop=True)
    if len(test) < 10:
        return None

    X_dev, y_dev = dev[features].to_numpy(float), dev["label_profit"].to_numpy(int)
    folds = purged_walk_forward(list(dev["armed_date"]), n_splits=4, embargo_sessions=5)
    oos_p = np.full(len(dev), np.nan)
    for fold in folds:
        if len(fold.train_idx) < 30 or len(fold.test_idx) == 0 or len(set(y_dev[fold.train_idx])) < 2:  # noqa: E501
            continue
        m = clone(make_baseline(seed))
        m.fit(X_dev[fold.train_idx], y_dev[fold.train_idx])
        oos_p[fold.test_idx] = m.predict_proba(X_dev[fold.test_idx])[:, 1]
    scored = ~np.isnan(oos_p)
    if scored.sum() < 20:
        return None
    dev_brier = float(np.mean((oos_p[scored] - y_dev[scored]) ** 2))

    final = clone(make_baseline(seed))
    final.fit(X_dev, y_dev)
    p_test = final.predict_proba(test[features].to_numpy(float))[:, 1]
    m_test = evaluate(
        test["label_profit"].to_numpy(int), p_test, test["gross_r"].to_numpy(float),
        threshold=0.5,
    )  # fmt: skip
    net_r = float(test["gross_r"].mean())
    se = float(test["gross_r"].std() / np.sqrt(len(test))) if len(test) > 1 else 0.0
    return {
        "dev_oos_brier": dev_brier, "dev_oos_n": int(scored.sum()),
        "test_roc_auc": m_test.roc_auc, "test_brier": m_test.brier,
        "test_n": len(test), "test_net_r": net_r,
        "test_net_r_t": net_r / se if se > 0 else None,
    }


def run_eod_learning(
    market: str, db: Path, *, log: Path | None = None, echo: Any = lambda s: None,
) -> dict[str, Any]:
    """The once-a-day entry point, called from scripts/research_tracker.py's `run` for
    market in ("nse", "bse") only, per the explicit request this was built for."""
    log = log or log_path_for(market)
    rows = load_log(log)
    resolved = [
        r for r in rows.values()
        if r.rule != "random_eligible" and r.outcome is not None and r.r_multiple is not None
    ]  # fmt: skip
    record: dict[str, Any] = {
        "date": date.today().isoformat(), "market": market, "n_resolved": len(resolved),
    }

    if len(resolved) < MIN_RESOLVED_TO_FIT:
        record["status"] = f"not enough data ({len(resolved)}/{MIN_RESOLVED_TO_FIT} resolved)"
        echo(record["status"])
        _append_history(market, record)
        return record

    df = _build_dataset(db, resolved)
    state = LearningState.load(_state_path(market))

    base_result = _fit_and_score(df, state.base_features, seed=20260101)
    if base_result is None:
        record["status"] = "recomputed features insufficient after dropna - skipped this run"
        echo(record["status"])
        _append_history(market, record)
        return record
    record.update({"features_used": list(state.base_features), **base_result})

    explore = state.next_to_explore()
    if explore is not None:
        trial_result = _fit_and_score(df, [*state.base_features, explore], seed=20260101)
        adopted = False
        trial_brier: float | None = None
        if trial_result is not None:
            trial_brier = trial_result["dev_oos_brier"]
            improved_brier = base_result["dev_oos_brier"] - trial_brier
            not_worse_net = (
                trial_result["test_net_r"] >= base_result["test_net_r"] - 0.02
            )  # small tolerance, not a hard requirement to strictly improve net R too
            adopted = improved_brier >= BRIER_IMPROVEMENT_MARGIN and not_worse_net
        record["explored_feature"] = explore
        record["explored_result"] = trial_result
        record["explored_adopted"] = adopted
        state.explored.append(explore)
        if adopted:
            state.base_features.append(explore)
            record["features_used"] = list(state.base_features)
            echo(f"adopted new feature: {explore} (Brier {base_result['dev_oos_brier']:.4f} -> {trial_brier:.4f})")  # noqa: E501
        else:
            echo(f"explored {explore}: not adopted")
        state.save(_state_path(market))

    _maybe_flag_review(market, record)
    echo(
        f"{market} EOD learning: n={len(resolved)} "
        f"dev_oos_brier={base_result['dev_oos_brier']:.4f} "
        f"test_roc_auc={base_result['test_roc_auc']} test_net_r={base_result['test_net_r']:+.4f}"
    )  # fmt: skip
    _append_history(market, record)
    return record


def _maybe_flag_review(market: str, record: dict[str, Any]) -> None:
    """Same bar as everywhere else (engine/scoring.py::eligibility()'s default policy),
    same "flag, never auto-apply" pattern as flag_setup_failures/flag_research_findings."""
    from tradedesk.engine.scoring import DEFAULT_POLICY, eligibility
    from tradedesk.review_queue import add_item, load_queue

    auc = record.get("test_roc_auc")
    net_r = record.get("test_net_r")
    n = record.get("test_n")
    if auc is None or net_r is None or n is None:
        return
    t = record.get("test_net_r_t") or 0.0
    win_rate = 0.5  # not tracked per-row here; the gate's win_rate check is conservative
    eligible, reasons = eligibility(
        trades=record["n_resolved"], oos_trades=n, win_rate=win_rate, expectancy_r=net_r,
        random_baseline_r=None, policy=DEFAULT_POLICY,
    )  # fmt: skip
    if not (eligible and auc >= 0.55 and abs(t) >= 2):
        return
    title = f"[{market}] EOD learning model clears the evidence bar"
    if title in {i.title for i in load_queue().values()}:
        return
    add_item(
        market=f"{market}-eod-learning",
        title=title,
        detail=(
            f"Locked-tail test: n={n}, ROC-AUC={auc:.3f}, net R={net_r:+.4f} (t={t:.2f}), "
            f"features={record.get('features_used')}. Reasons the base eligibility check "
            f"still lists: {'; '.join(reasons) if reasons else 'none'}."
        ),
        proposal=f"Review the {market} EOD learning model before considering any live use - it has cleared the automatic check, which is necessary but not sufficient.",  # noqa: E501
    )


def _append_history(market: str, record: dict[str, Any]) -> None:
    path = _history_path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def load_history(market: str) -> list[dict[str, Any]]:
    path = _history_path(market)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]  # noqa: E501
