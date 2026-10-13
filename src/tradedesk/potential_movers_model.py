"""Learns to rank stocks by the chance of a >= +5% close next session, and only replaces the
fixed tier rules when it beats them on data it never trained or was tuned on.

Rows are split by date into train / validation / locked blocks with a purge between them.
Every block is scored the same way: per session take the top-K names and measure how often
they really closed >= +5% and what they returned on average. The model is adopted only if it
beats the tier rules on BOTH the validation and the locked block and its picks have a positive
mean next-day return (so it is not merely finding volatile names that fall as often as they
rise). Otherwise the rules stay in charge and the reason is recorded. No live authority.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from tradedesk.potential_movers import LIST_SIZE, MOVE, TIER_ORDER

FEATURES = [
    "below50", "pos6m", "d5", "ret1", "ret20", "gap", "close_loc", "atr_pct",
    "dist_hi20", "rng_rel", "vol_rel", "age_bars",
]  # fmt: skip
EVENT_FEATURES = ["res_today", "res_next", "hist_res_move"]
PURGE_SESSIONS = 3
VALIDATION_SESSIONS = 120
LOCKED_SESSIONS = 120
MIN_TRAIN_SESSIONS = 500
RETRAIN_AFTER_DAYS = 1  # the task runs once a day: learn from each new day's movers
MODEL_NAME = "model.joblib"
REPORT_NAME = "model_report.json"


def split_dates(dates: list[date]) -> dict[str, list[date]] | None:
    ordered = sorted(set(dates))
    need = MIN_TRAIN_SESSIONS + VALIDATION_SESSIONS + LOCKED_SESSIONS + 2 * PURGE_SESSIONS
    if len(ordered) < need:
        return None
    locked = ordered[-LOCKED_SESSIONS:]
    rest = ordered[: -LOCKED_SESSIONS - PURGE_SESSIONS]
    validation = rest[-VALIDATION_SESSIONS:]
    train = rest[: -VALIDATION_SESSIONS - PURGE_SESSIONS]
    return {"train": train, "validation": validation, "locked": locked}


def _rule_scores(frame: pd.DataFrame) -> pd.Series:
    """The tier rules as a score: tier first, then depth under EMA50."""

    rank = frame["tier"].map({t: len(TIER_ORDER) - i for i, t in enumerate(TIER_ORDER)})
    return rank.fillna(0) * 10 - frame["below50"]


def top_k_by_session(
    frame: pd.DataFrame, score: pd.Series, *, only_tiered: bool = False
) -> pd.DataFrame:
    work = frame.assign(_score=score.to_numpy())
    if only_tiered:
        work = work[work["tier"] != ""]
    work = work.sort_values(["date", "_score"], ascending=[True, False])
    return work.groupby("date", sort=False).head(LIST_SIZE)


def score_picks(picks: pd.DataFrame, universe: pd.DataFrame) -> dict[str, Any]:
    n = len(picks)
    if n == 0:
        return {"picks": 0}
    up = int((picks["next_ret"] >= MOVE).sum())
    down = int((picks["next_ret"] <= -MOVE).sum())
    base = float((universe["next_ret"] >= MOVE).mean())
    return {
        "picks": n,
        "sessions": int(picks["date"].nunique()),
        "up5_rate": up / n,
        "down5_rate": down / n,
        "mean_next_return": float(picks["next_ret"].mean()),
        # what a buyer at the next open (after any gap) actually keeps of that session
        "mean_open_to_close": float(picks["next_open_to_close"].mean()),
        "universe_up5_rate": base,
        "lift": (up / n) / base if base else None,
    }


def _fit(train: pd.DataFrame, features: list[str] | None = None) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.08,
        max_leaf_nodes=24,
        min_samples_leaf=200,
        l2_regularization=1.0,
        random_state=0,
    )
    columns = features or FEATURES
    model.fit(train[columns], (train["next_ret"] >= MOVE).astype(int))
    return model


def train_and_evaluate(panel: pd.DataFrame) -> dict[str, Any]:
    known = panel[panel["next_ret"].notna()]
    splits = split_dates(list(known["date"].unique()))
    if splits is None:
        return {"adopted": False, "reason": "not enough sessions for train/validation/locked"}
    blocks = {name: known[known["date"].isin(set(days))] for name, days in splits.items()}
    # A/B: the price-only model against the same model plus results-calendar features.
    has_events = bool(known["res_today"].sum() + known["res_next"].sum())
    candidates = {"price_only": FEATURES}
    if has_events:
        candidates["price_and_results"] = FEATURES + EVENT_FEATURES
    fitted = {name: _fit(blocks["train"], cols) for name, cols in candidates.items()}
    val = blocks["validation"]
    val_rate = {
        name: score_picks(
            top_k_by_session(
                val, pd.Series(m.predict_proba(val[candidates[name]])[:, 1], index=val.index)
            ),
            val,
        )["up5_rate"]
        for name, m in fitted.items()
    }
    chosen = max(val_rate, key=lambda name: val_rate[name])  # chosen on validation only
    model = fitted[chosen]
    features = candidates[chosen]
    report: dict[str, Any] = {
        "feature_set": chosen,
        "validation_up5_by_feature_set": val_rate,
        "splits": {k: [v[0].isoformat(), v[-1].isoformat(), len(v)] for k, v in splits.items()},
        "features": features,
        "trained_at": datetime.now().isoformat(),
    }
    for name in ("validation", "locked"):
        block = blocks[name]
        proba = pd.Series(model.predict_proba(block[features])[:, 1], index=block.index)
        report[name] = {
            "model": score_picks(top_k_by_session(block, proba), block),
            "rules": score_picks(
                top_k_by_session(block, _rule_scores(block), only_tiered=True), block
            ),
        }
    reasons: list[str] = []
    for name in ("validation", "locked"):
        m, r = report[name]["model"], report[name]["rules"]
        if not m.get("picks") or m["up5_rate"] <= r.get("up5_rate", 0):
            reasons.append(f"{name}: model up5 rate does not beat the rules")
        if m.get("mean_next_return", 0) <= 0:
            reasons.append(f"{name}: model picks have a non-positive mean next-day return")
    report["adopted"] = not reasons
    report["reason"] = (
        "beats the rules on validation and the locked block" if not reasons else "; ".join(reasons)
    )
    report["_model"] = _fit(known, features) if report["adopted"] else None
    return report


def load_or_train(
    panel: pd.DataFrame, directory: Path, today: date
) -> tuple[Any | None, dict[str, Any]]:
    """Reuse a recent model, else retrain and re-judge it. Returns (model or None, report)."""

    directory.mkdir(parents=True, exist_ok=True)
    model_path, report_path = directory / MODEL_NAME, directory / REPORT_NAME
    if model_path.exists() and report_path.exists():
        modified = datetime.fromtimestamp(model_path.stat().st_mtime).date()
        if (today - modified).days < RETRAIN_AFTER_DAYS:
            return joblib.load(model_path), json.loads(report_path.read_text(encoding="utf-8"))
    report = train_and_evaluate(panel)
    model = report.pop("_model", None)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if model is not None:
        joblib.dump(model, model_path)
    else:
        model_path.unlink(missing_ok=True)
    return model, report


def score_rows(model: Any, rows: pd.DataFrame) -> pd.Series:
    columns = list(model.feature_names_in_)
    return pd.Series(model.predict_proba(rows[columns])[:, 1], index=rows.index)
