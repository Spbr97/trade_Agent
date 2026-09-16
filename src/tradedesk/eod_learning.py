"""EOD self-learning: the one loop in this project that can still move the needle.

Everything else has been measured and found wanting - the three live setups are worse than
random on BOTH exchanges, multi-month momentum loses to random, cross-sectional ranking adds
nothing, and three separate models trained on this data all landed on a coin flip. The one
thing never tried until now is learning from evidence collected AFTER the model looks at it,
which is what `research_tracker.py`'s forward log produces. This module turns that log into a
model that can genuinely improve as real days accumulate.

What it is NOT: it does not adjust anything per-call, and it touches nothing alertable -
`config/setups.yaml`, the live scan and the eligibility gate are untouched. It can only ever
write a review-queue item for a human to look at.

The lifecycle (2026-09-14 rework - the original version was a one-pass queue that stopped
learning forever after 12 runs, which is a fatal flaw in something whose whole job is to keep
improving):

    EXPLORE  -> test the next untried feature once, adopt only if it clears the margin
    REVALIDATE -> re-check an already-adopted feature against the LARGER dataset that now
                  exists, and DROP it if it is no longer earning its place
    CONSOLIDATE -> nothing left to test at the current sample size; just record the trend

These cycle indefinitely, so the feature set keeps getting re-decided with better evidence
instead of being frozen by whatever the first 50 rows happened to say.

**Why re-testing the same features is not the multiple-testing trap this project keeps
warning about.** The trap is searching for a NEW hypothesis over and over until one prints
positive. This does the opposite: the candidate pool is FIXED and finite, and re-tests
re-decide an existing question against genuinely more data, with the decision free to go
either way - a feature can be dropped as easily as kept. Adoption and removal use the SAME
margin, so there is no ratchet that only ever adds.

Two further guards worth knowing:
  - `_max_features_for(n)` caps the active feature count at roughly n/15, so the model cannot
    accumulate 20 features on 60 rows. Below that cap, exploration simply pauses. More
    features on thin data is precisely how you manufacture a good-looking in-sample fit and
    a worthless out-of-sample one.
  - Event features (results/corporate-action proximity) are NSE/BSE only - crypto has no
    earnings calendar, so including them there would add a dead constant column rather than
    information.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import daily_features
from tradedesk.research_tracker import ResearchCall, load_log, log_path_for

# Always on, never dropped - a modest, established base the model is always allowed to use.
CORE_FEATURES: list[str] = ["rsi2", "rsi14", "adx14", "di_spread", "atr_pct", "dist_ema50_atr"]

# The candidate pool, grouped by the hypothesis each family represents. Exploration
# INTERLEAVES across families (one from each in rotation) rather than draining one family
# first, so the early runs - the only ones that happen before there is much data - sample
# genuinely different ideas instead of six flavours of "where is price vs a moving average".
FEATURE_FAMILIES: dict[str, list[str]] = {
    "trend": ["dist_ema10_atr", "dist_ema200_atr", "ema20_slope", "ema50_slope"],
    "momentum": ["roc5", "roc20", "macd_hist_atr"],
    "volatility": ["bb_width", "atr_pct_rank", "range_contraction"],
    # Multi-day shape (gap at the open, consecutive-close streak, inside/outside relative
    # to yesterday's range). Most of these exist nowhere else in this project.
    "chart_shape": ["gap_pct", "up_streak", "inside_day", "outside_day"],
    # One bar DECOMPOSED into its structural parts rather than read as one blunt summary -
    # 2026-09-14, explicit request to add "smaller call parts to read graphs to learn from".
    # `close_range_pos` (kept here, not a chart_shape catch-all) says where the close landed;
    # these break the SAME bar down further: how much of the range was real conviction (the
    # body) versus rejection (the wicks), and on which side. A long lower wick and a long
    # upper wick are opposite tells even on a day with an identical close_range_pos, and
    # nothing before this fed that distinction to a model.
    "candle_parts": [
        "close_range_pos", "body_pct", "upper_wick_pct", "lower_wick_pct",
        "body_atr", "bullish_candle",
    ],  # fmt: skip
    "position": ["pct_in_52w_range", "dist_52w_high_atr", "dist_high20_atr"],
    "volume": ["vol_ratio50", "vol_dryup", "updown_vol20"],
    # Real data already in the store that no model here has ever been given. Leakage-safe by
    # construction - see `_near_term_results` for why the forward-looking one is capped.
    "event": ["near_term_results", "days_since_results", "days_since_corp_action"],
}

EVENT_FAMILY = "event"

# A feature is adopted only if it lowers pooled walk-forward OOS Brier by at least this much,
# and is dropped on revalidation only if removing it costs less than this much. Symmetric on
# purpose: the same bar to get in as to stay in, so there is no ratchet. Pre-registered
# before any of this ran, same discipline as the H1/H2 proof plan.
BRIER_IMPROVEMENT_MARGIN = 0.005

MIN_RESOLVED_TO_FIT = 20  # 2026-09-15, lowered from 50 per explicit user request ("let it
# memorise and see a pattern in 20/25 trades, instead of ignoring") - accepted as a real,
# named tradeoff, not a free lunch: purged_walk_forward folds are thinner here than at 50,
# so early MODEL/EXPLORE decisions at n~20-30 are noisier and more likely to be reversed by
# REVALIDATE once more data arrives. That reversal path (REVALIDATE_EVERY, the symmetric
# BRIER_IMPROVEMENT_MARGIN bar to adopt AND to keep) is exactly the safety net that makes
# starting earlier survivable - a bad early call gets re-tested and dropped, not frozen in.
FINAL_TEST_FRAC = 0.2
ROWS_PER_FEATURE = 15  # the sample-size cap behind _max_features_for
REVALIDATE_EVERY = 8  # fits between feature-revalidation passes
MODEL_RACE_EVERY = 12  # fits between model-family races (costlier, slower-moving decision)

# Which learner to fit. Both come from prediction/train.py so this module cannot drift from
# the rest of the project's modelling conventions (both are probability-calibrated; xgboost
# is a guarded import that returns None if the wheel is blocked, as LightGBM is here).
#
# `MIN_ROWS_FOR_TREE` is the honest part: a boosted tree over ~30 features on 60 rows is a
# memorisation machine, and its walk-forward score would be noise dressed as a decision. The
# tree is not even offered until there is enough data for the comparison to mean something.
# Below that floor the loop is not "missing" a better model - it correctly has only one
# candidate worth fitting.
MIN_ROWS_FOR_TREE = 250


def exploration_pool(market: str) -> list[str]:
    """The candidate pool for one market, interleaved across families. Crypto drops the
    event family outright - no earnings calendar exists for a coin, so those columns would
    be a constant, not a signal."""
    families = [
        names for fam, names in FEATURE_FAMILIES.items()
        if not (market == "crypto" and fam == EVENT_FAMILY)
    ]  # fmt: skip
    out: list[str] = []
    for i in range(max(len(n) for n in families)):
        for names in families:
            if i < len(names):
                out.append(names[i])
    return out


ALL_FEATURE_NAMES: list[str] = CORE_FEATURES + [
    n for names in FEATURE_FAMILIES.values() for n in names
]


def _max_features_for(n_rows: int) -> int:
    """Cap the active feature count by how much data actually exists. ~15 rows per feature is
    a conventional floor for a linear model; below it, added features buy in-sample fit and
    sell out-of-sample truth. Never caps below the core set."""
    return max(len(CORE_FEATURES), n_rows // ROWS_PER_FEATURE)


def _state_path(market: str) -> Path:
    return Path(f"data/models/eod_learning_state_{market}.json")


def _history_path(market: str) -> Path:
    return Path(f"data/models/eod_learning_{market}.jsonl")


@dataclass
class LearningState:
    """Persisted across runs. `adopted` is ordered by adoption so revalidation can rotate
    through it; `dropped` records features that earned a place once and later lost it."""

    adopted: list[str] = field(default_factory=list)
    explored: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    fit_runs: int = 0
    model_kind: str = "logistic"

    @classmethod
    def load(cls, path: Path) -> LearningState:
        if not path.exists():
            return cls()
        d = json.loads(path.read_text(encoding="utf-8"))
        # Tolerates the pre-2026-09-14 shape, which stored base_features (core + adopted
        # mixed together) and had no dropped/fit_runs/model_kind at all.
        adopted = d.get("adopted")
        if adopted is None:
            adopted = [f for f in d.get("base_features", []) if f not in CORE_FEATURES]
        return cls(
            adopted=adopted,
            explored=d.get("explored", []),
            dropped=d.get("dropped", []),
            fit_runs=d.get("fit_runs", 0),
            model_kind=d.get("model_kind", "logistic"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "adopted": self.adopted, "explored": self.explored,
                    "dropped": self.dropped, "fit_runs": self.fit_runs,
                    "model_kind": self.model_kind,
                }
            ),
            encoding="utf-8",
        )  # fmt: skip

    def active_features(self) -> list[str]:
        return CORE_FEATURES + self.adopted

    def next_to_explore(self, market: str) -> str | None:
        """Next untried candidate; once the whole pool has been tried, previously-dropped
        features become eligible again - that is not a re-roll of the same dice, it is the
        same question asked against materially more data than when it was dropped."""
        pool = exploration_pool(market)
        for name in pool:
            if name not in self.explored and name not in self.adopted:
                return name
        for name in self.dropped:
            if name not in self.adopted:
                return name
        return None

    def next_to_revalidate(self) -> str | None:
        if not self.adopted:
            return None
        return self.adopted[self.fit_runs % len(self.adopted)]


# ------------------------------------------------------------------ features


def _event_dates(store: CandleStore, code: str) -> tuple[list[date], list[date]]:
    """(results meeting dates, corporate-action ex-dates) for one code, both ascending.
    Empty lists when the market has no such data (crypto) or the symbol is unknown."""
    symbol = store.symbol_for(code)
    if not symbol:
        return [], []
    results = sorted(
        r[0] for r in store.con.execute(
            "SELECT event_date FROM results_events WHERE symbol = ?", [symbol]
        ).fetchall()
    )  # fmt: skip
    actions = sorted(a.ex_date for a in store.corporate_actions(symbol))
    return results, actions


def _days_since(dates: list[date], on: date, cap: float = 250.0) -> float:
    """Calendar days since the most recent date strictly before `on`; `cap` when there is
    none or the gap exceeds it - the same "absence is a large sentinel" convention
    prediction/features.py already uses."""
    i = bisect.bisect_left(dates, on)
    if i == 0:
        return cap
    return min(float((on - dates[i - 1]).days), cap)


def _near_term_results(dates: list[date], on: date, window_days: int = 4) -> float:
    """1.0 only if a results event falls within `window_days` CALENDAR days ahead.

    Deliberately capped, and this matters: `results_events` stores only the meeting date,
    never when it was publicly announced. SEBI LODR requires just ~2 working days' advance
    intimation, so an uncapped "days to next results" feature would claim knowledge of a date
    that plausibly was not yet public on the arming day - a real look-ahead. Four calendar
    days covers two working days across a weekend. This will miss genuinely-known-further-out
    dates, which is the correct direction to err."""
    i = bisect.bisect_right(dates, on)
    if i >= len(dates):
        return 0.0
    return 1.0 if (dates[i] - on).days <= window_days else 0.0


def _row_features(
    f: pd.DataFrame, i: int, results: list[date], actions: list[date], on: date
) -> dict[str, float] | None:
    """Every name in ALL_FEATURE_NAMES, from the arming bar `i` (plus bar i-1 for the
    two-day shapes). None when the bar is unusable (no previous bar, or no valid ATR)."""
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
    prev_high, prev_low = float(prev.get("high", np.nan)), float(prev.get("low", np.nan))
    rng = high - low

    # Consecutive up-closes ending at this bar, capped - a plain "how stretched is this run"
    # read that no indicator in this project expresses.
    streak = 0
    for k in range(i, max(i - 10, 0), -1):
        if float(f["close"].iloc[k]) > float(f["close"].iloc[k - 1]):
            streak += 1
        else:
            break

    return {
        "rsi2": g("rsi2", 50.0),
        "rsi14": g("rsi14", 50.0),
        "adx14": g("adx14"),
        "di_spread": g("plus_di") - g("minus_di"),
        "atr_pct": g("atr_pct"),
        "dist_ema10_atr": (close - g("ema10", close)) / atr,
        "dist_ema50_atr": (close - g("ema50", close)) / atr,
        "dist_ema200_atr": (close - g("ema200", close)) / atr,
        "ema20_slope": g("ema20_slope", 0.0) * 100,
        "ema50_slope": g("ema50_slope", 0.0) * 100,
        "roc5": g("roc5"),
        "roc20": g("roc20"),
        "macd_hist_atr": g("macd_hist", 0.0) / atr,
        "bb_width": g("bb_width"),
        "atr_pct_rank": g("atr_pct_rank", 50.0),
        "range_contraction": g("range_contraction", 1.0),
        "close_range_pos": (close - low) / rng if rng > 0 else 0.5,
        "body_pct": abs(close - opn) / rng if rng > 0 else 0.0,
        "upper_wick_pct": (high - max(opn, close)) / rng if rng > 0 else 0.0,
        "lower_wick_pct": (min(opn, close) - low) / rng if rng > 0 else 0.0,
        "body_atr": (close - opn) / atr,  # signed - carries direction, not just size
        "bullish_candle": 1.0 if close > opn else 0.0,
        "gap_pct": (opn - prev_close) / prev_close * 100 if prev_close > 0 else 0.0,
        "up_streak": float(streak),
        "inside_day": 1.0 if (high <= prev_high and low >= prev_low) else 0.0,
        "outside_day": 1.0 if (high > prev_high and low < prev_low) else 0.0,
        "pct_in_52w_range": g("pct_in_52w_range", 50.0),
        "dist_52w_high_atr": (close - g("high52w", close)) / atr,
        "dist_high20_atr": (close - g("high20", close)) / atr,
        "vol_ratio50": g("vol_ratio50", 1.0),
        "vol_dryup": g("vol_dryup", 1.0),
        "updown_vol20": g("updown_vol20", 1.0),
        "near_term_results": _near_term_results(results, on),
        "days_since_results": _days_since(results, on),
        "days_since_corp_action": _days_since(actions, on),
    }


def _build_dataset(db: Path, resolved: list[ResearchCall]) -> pd.DataFrame:
    """One row per resolved call, all candidate rules pooled (EXCLUDING random_eligible -
    that is the null, not a trading idea). Features are recomputed from the stored
    code+armed_on, since ResearchCall keeps only close/ATR at arming, not a full vector."""
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
            results, actions = _event_dates(store, code)
            for r in calls:
                armed = date.fromisoformat(r.armed_on)
                hit = np.flatnonzero(dates == armed)
                if not len(hit):
                    continue
                feats = _row_features(f, int(hit[0]), results, actions, armed)
                if feats is None:
                    continue
                rows.append(
                    {
                        "armed_date": armed, "label_profit": int((r.r_multiple or 0) > 0),
                        "gross_r": float(r.r_multiple or 0.0), **feats,
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ fitting


def _make_model(kind: str, seed: int) -> Any | None:
    """Both learners come from prediction/train.py rather than being rebuilt here, so this
    module cannot drift from the project's modelling conventions. Returns None when the kind
    is unavailable (xgboost's wheel blocked), which callers treat as "not a candidate"."""
    from tradedesk.prediction.train import make_baseline, make_xgboost

    if kind == "xgboost":
        return make_xgboost(seed)
    return make_baseline(seed)


def available_model_kinds(n_rows: int) -> list[str]:
    """Logistic always; the tree only once there is enough data for the race to be
    meaningful AND the wheel actually imports on this machine."""
    kinds = ["logistic"]
    if n_rows >= MIN_ROWS_FOR_TREE and _make_model("xgboost", 0) is not None:
        kinds.append("xgboost")
    return kinds


def _fit_and_score(
    df: pd.DataFrame, features: list[str], seed: int, cost_r: float, kind: str = "logistic"
) -> dict[str, Any] | None:
    """Purged walk-forward on all but the most recent FINAL_TEST_FRAC of sessions, scored
    once on that reserved tail - the same discipline as every other model in this project.

    `cost_r` (`research_tracker.cost_r_for(market)`) is subtracted from the tail's gross R so
    `test_net_r` is a real net figure; crypto's drag (~0.16R) is meaningfully heavier than
    NSE/BSE's (~0.128R), so reporting gross and calling it net would mislead there first."""
    from sklearn.base import clone

    from tradedesk.prediction.train import evaluate, purged_walk_forward

    estimator = _make_model(kind, seed)
    if estimator is None:
        return None
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
        m = clone(estimator)
        m.fit(X_dev[fold.train_idx], y_dev[fold.train_idx])
        oos_p[fold.test_idx] = m.predict_proba(X_dev[fold.test_idx])[:, 1]
    scored = ~np.isnan(oos_p)
    if scored.sum() < 20:
        return None
    dev_brier = float(np.mean((oos_p[scored] - y_dev[scored]) ** 2))

    final = clone(estimator)
    final.fit(X_dev, y_dev)
    p_test = final.predict_proba(test[features].to_numpy(float))[:, 1]
    m_test = evaluate(
        test["label_profit"].to_numpy(int), p_test, test["gross_r"].to_numpy(float),
        threshold=0.5,
    )  # fmt: skip
    net_series = test["gross_r"] - cost_r
    net_r = float(net_series.mean())
    se = float(net_series.std() / np.sqrt(len(test))) if len(test) > 1 else 0.0
    return {
        "dev_oos_brier": dev_brier, "dev_oos_n": int(scored.sum()),
        "test_roc_auc": m_test.roc_auc, "test_brier": m_test.brier,
        "test_n": len(test), "test_gross_r": float(test["gross_r"].mean()),
        "test_net_r": net_r, "test_net_r_t": net_r / se if se > 0 else None,
        "model_kind": kind,
    }


# ------------------------------------------------------------------ the run


def run_eod_learning(
    market: str, db: Path, *, log: Path | None = None, echo: Any = lambda s: None,
) -> dict[str, Any]:
    """One learning pass for one market. Called as the last step of
    `scripts/research_tracker.py run` (NSE/BSE once daily, crypto twice)."""
    from tradedesk.research_tracker import cost_r_for

    log = log or log_path_for(market)
    cost_r = cost_r_for(market)
    rows = load_log(log)
    resolved = [
        r for r in rows.values()
        if r.rule != "random_eligible" and r.outcome is not None and r.r_multiple is not None
    ]  # fmt: skip
    record: dict[str, Any] = {
        "date": date.today().isoformat(), "market": market, "n_resolved": len(resolved),
    }

    if len(resolved) < MIN_RESOLVED_TO_FIT:
        record["phase"] = "waiting"
        record["summary"] = f"not enough data ({len(resolved)}/{MIN_RESOLVED_TO_FIT} resolved)"
        echo(record["summary"])
        _append_history(market, record)
        return record

    df = _build_dataset(db, resolved)
    state = LearningState.load(_state_path(market))
    active = state.active_features()

    base = _fit_and_score(df, active, seed=20260101, cost_r=cost_r, kind=state.model_kind)
    if base is None:
        record["phase"] = "waiting"
        record["summary"] = "usable rows fell below the fitting floor after dropna"
        echo(record["summary"])
        _append_history(market, record)
        return record

    state.fit_runs += 1
    record.update({"features_used": list(active), **base})
    cap = _max_features_for(len(df))
    record["feature_cap"] = cap

    phase, summary = _advance(state, df, base, market, cap, cost_r, record)
    record["phase"] = phase
    record["summary"] = summary
    record["features_used"] = state.active_features()
    # After a race this is the NEWLY chosen learner, not the one `base` was fitted with -
    # the history row should say what the model will be from here, not what it just was.
    record["model_kind"] = state.model_kind
    state.save(_state_path(market))

    _maybe_flag_review(market, record)
    echo(
        f"{market} learning [{phase}] {state.model_kind}: n={len(resolved)} "
        f"brier={base['dev_oos_brier']:.4f} auc={base['test_roc_auc']} "
        f"net={base['test_net_r']:+.4f} | {summary}"
    )
    _append_history(market, record)
    return record


def _advance(
    state: LearningState, df: pd.DataFrame, base: dict[str, Any], market: str,
    cap: int, cost_r: float, record: dict[str, Any],
) -> tuple[str, str]:
    """Pick and execute this run's single decision: race model families, revalidate one
    adopted feature, explore one new one, or neither. Exactly one model-affecting decision
    per run, by design - it keeps the compute bounded and every change attributable to one
    measurement.

    Priority order is deliberate: which LEARNER you use is a bigger lever than any single
    feature, so when a race is due it goes first; confirming what is already in the model
    comes next; adding something new is last."""
    if state.fit_runs % MODEL_RACE_EVERY == 0:
        kinds = [k for k in available_model_kinds(len(df)) if k != state.model_kind]
        if kinds:
            record["raced_models"] = kinds
            best_kind, best_brier = state.model_kind, base["dev_oos_brier"]
            for kind in kinds:
                trial = _fit_and_score(
                    df, state.active_features(), seed=20260101, cost_r=cost_r, kind=kind
                )
                if trial is None:
                    continue
                record[f"race_{kind}_brier"] = trial["dev_oos_brier"]
                if trial["dev_oos_brier"] < best_brier - BRIER_IMPROVEMENT_MARGIN:
                    best_kind, best_brier = kind, trial["dev_oos_brier"]
            if best_kind != state.model_kind:
                was = state.model_kind
                state.model_kind = best_kind
                return "model", (
                    f"switched learner {was} -> {best_kind} "
                    f"(Brier {base['dev_oos_brier']:.4f} -> {best_brier:.4f})"
                )
            return "model", (
                f"raced {', '.join(kinds)} against {state.model_kind} - incumbent kept"
            )

    # Revalidation takes priority on its cadence: confirming what is already in the model
    # matters more than adding to it, and it is the only path by which a bad early adoption
    # can ever be undone.
    if state.adopted and state.fit_runs % REVALIDATE_EVERY == 0:
        name = state.next_to_revalidate()
        if name is not None:
            without = [f for f in state.active_features() if f != name]
            trial = _fit_and_score(
                df, without, seed=20260101, cost_r=cost_r, kind=state.model_kind
            )
            record["revalidated_feature"] = name
            if trial is None:
                return "revalidate", f"could not refit without {name} - kept, no change"
            cost_of_removal = trial["dev_oos_brier"] - base["dev_oos_brier"]
            record["revalidate_brier_delta"] = cost_of_removal
            if cost_of_removal < BRIER_IMPROVEMENT_MARGIN:
                state.adopted.remove(name)
                if name not in state.dropped:
                    state.dropped.append(name)
                record["dropped_feature"] = name
                return "revalidate", (
                    f"dropped {name} - removing it costs only {cost_of_removal:+.4f} Brier, "
                    f"below the {BRIER_IMPROVEMENT_MARGIN} bar it had to clear to get in"
                )
            return "revalidate", f"kept {name} - removing it would cost {cost_of_removal:+.4f} Brier"  # noqa: E501

    if len(state.active_features()) >= cap:
        return "consolidate", (
            f"at the feature cap for this sample size ({len(state.active_features())}/{cap} "
            f"on {len(df)} rows) - exploration pauses until more calls resolve"
        )

    name = state.next_to_explore(market)
    if name is None:
        return "consolidate", f"whole pool explored; {len(state.adopted)} features adopted"

    trial = _fit_and_score(
        df, [*state.active_features(), name], seed=20260101, cost_r=cost_r,
        kind=state.model_kind,
    )
    record["explored_feature"] = name
    if name not in state.explored:
        state.explored.append(name)
    if trial is None:
        return "explore", f"could not fit with {name} - skipped"
    gain = base["dev_oos_brier"] - trial["dev_oos_brier"]
    record["explore_brier_delta"] = gain
    record["explored_adopted"] = gain >= BRIER_IMPROVEMENT_MARGIN
    if gain >= BRIER_IMPROVEMENT_MARGIN:
        state.adopted.append(name)
        if name in state.dropped:
            state.dropped.remove(name)
        return "explore", (
            f"adopted {name} - Brier {base['dev_oos_brier']:.4f} -> {trial['dev_oos_brier']:.4f}"
        )
    return "explore", (
        f"tried {name} - not adopted (Brier moved {gain:+.4f}, needs {BRIER_IMPROVEMENT_MARGIN})"  # noqa: E501
    )


def _maybe_flag_review(market: str, record: dict[str, Any]) -> None:
    """Same bar as everywhere else (engine/scoring.py::eligibility()'s default policy), same
    "flag, never auto-apply" pattern as flag_setup_failures/flag_research_findings."""
    from tradedesk.engine.scoring import DEFAULT_POLICY, eligibility
    from tradedesk.review_queue import add_item, load_queue

    auc, net_r, n = record.get("test_roc_auc"), record.get("test_net_r"), record.get("test_n")
    if auc is None or net_r is None or n is None:
        return
    t = record.get("test_net_r_t") or 0.0
    eligible, reasons = eligibility(
        trades=record["n_resolved"], oos_trades=n, win_rate=0.5, expectancy_r=net_r,
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
            f"features={record.get('features_used')}. Remaining gate reasons: "
            f"{'; '.join(reasons) if reasons else 'none'}."
        ),
        proposal=f"Review the {market} EOD learning model before any live use - it cleared the automatic check, which is necessary but not sufficient.",  # noqa: E501
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
