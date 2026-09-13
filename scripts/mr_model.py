"""Train a meta-labeling model ON the RSI(2) mean-reversion entries.

Why this and not `tradedesk train`: that trains on signals from engine/engine.py::scan_day,
and those setups are anti-predictive - random entry timing beats them by ~0.24R
(scripts/barrier_sweep.py null-test). A filter cannot rescue a population that is worse than
a coin flip, which is why every methodology fix left OOS ROC-AUC at ~0.556. The RSI(2)
population is the opposite case: a real, replicated GROSS edge (+0.067R, t=3.35 on a locked
half) that is simply too thin to clear costs. Filtering a positive-edge population down to
its best subset is the job meta-labeling is actually for, so this points the same machinery
at the population where it can work.

Methodology is deliberately train.py's, imported rather than reimplemented so it cannot
drift: purged walk-forward by date with an embargo, the most recent `final_test_frac` of
history reserved and scored EXACTLY ONCE, model choice by pooled OOS Brier, threshold chosen
on validation folds only, calibrated estimators, fixed seed.

The bar this has to clear is not ROC-AUC. It is: does gating the RSI(2) entries on the
model's probability produce a NET expectancy, after real NSE costs, that beats simply taking
every RSI(2) entry? That comparison is the entire point, and it is reported on the locked
half.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore

app = typer.Typer(add_completion=False)

# The geometry locked by entry_search.py's train-half search (see CLAUDE.md).
STOP_ATR = 3.0
TARGET_R = 2.0
MAX_HOLD = 10

DATASET = Path("data/reports/mr_dataset.csv")

# Everything here is a trailing-window column of engine/indicators.py::daily_features read at
# the ARMING bar, or a ratio of such columns - nothing from after the decision point. Raw
# price levels are deliberately excluded; they do not generalise across stocks, so distances
# are carried in ATR units and volatility as a self-referential rank.
FEATURES = [
    "dist_ema10_atr", "dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr",
    "ema20_slope", "ema50_slope",
    "adx14", "di_spread", "rsi14", "rsi2", "macd_hist_atr", "roc5", "roc20",
    "atr_pct", "atr_pct_rank", "bb_width", "range_contraction",
    "vol_ratio50", "vol_dryup", "updown_vol20",
    "nr7", "inside_day",
    "dist_52w_high_atr", "pct_in_52w_range", "dist_high20_atr",
    "day_of_week", "nifty_return_1d", "nifty_return_5d", "vix", "vix_change_5d",
]  # fmt: skip


def _barrier(
    o: Any, h: Any, low_: Any, c: Any, start: int, *, entry: float, stop: float, target: float
) -> tuple[int, float] | None:
    """(label, gross_r). Same gap/tie/vertical rules as prediction/labeling.py."""
    risk = entry - stop
    if risk <= 0:
        return None
    n = min(len(c) - start, MAX_HOLD + 1)
    if n <= 0:
        return None
    for k in range(n):
        i = start + k
        if o[i] <= stop:
            return 0, (float(o[i]) - entry) / risk
        if low_[i] <= stop:
            return 0, -1.0
        if h[i] >= target:
            return 1, TARGET_R
    return 0, (float(c[start + n - 1]) - entry) / risk


@app.command()
def build(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    start: str = typer.Option("2023-09-01", "--from"),
    end: str = typer.Option("2026-09-13", "--to"),
    max_codes: int = typer.Option(0, "--max-codes"),
    out: Path = typer.Option(DATASET, "--out"),
) -> None:
    """One row per RSI(2) entry: features at the arming close, triple-barrier label, gross R."""
    from datetime import datetime

    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.indicators import daily_features

    settings = load_config(root)
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()

    with CandleStore(db) as store:
        # Market context, loaded once and joined by date - the same series the regime calc
        # already uses, so no new data pipeline.
        bench = store.index_code(settings.universe.benchmark)
        vix_code = store.index_code(settings.universe.volatility_index)
        nifty = store.load(bench, Interval.D1) if bench else None
        vixdf = store.load(vix_code, Interval.D1) if vix_code else None
        ctx = pd.DataFrame()
        if nifty is not None and len(nifty):
            n = nifty["close"]
            ctx = pd.DataFrame(
                {
                    "nifty_return_1d": n.pct_change() * 100,
                    "nifty_return_5d": n.pct_change(5) * 100,
                },
                index=pd.DatetimeIndex(nifty.index).tz_convert("Asia/Kolkata").date,
            )
        if vixdf is not None and len(vixdf):
            v = vixdf["close"]
            vx = pd.DataFrame(
                {"vix": v, "vix_change_5d": v.pct_change(5) * 100},
                index=pd.DatetimeIndex(vixdf.index).tz_convert("Asia/Kolkata").date,
            )
            ctx = vx if ctx.empty else ctx.join(vx, how="outer")
        typer.echo(f"context: benchmark={bench} vix={vix_code} rows={len(ctx)}")

        rows: list[dict[str, Any]] = []
        codes = store.codes(Interval.D1)
        if max_codes:
            codes = codes[:max_codes]
        typer.echo(f"building RSI(2) dataset over {len(codes)} codes {d0} -> {d1}")
        for n_done, code in enumerate(codes, 1):
            if n_done % 400 == 0:
                typer.echo(f"  {n_done}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            f["_d"] = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            turnover = (f["close"] * f["volume"]).rolling(20).mean()
            mask = (
                (f["rsi2"] < 10) & (f["close"] > f["ema50"])
                & (turnover >= min_turnover) & (f["close"] >= min_price)
                & (f["_d"] >= d0) & (f["_d"] <= d1)
                & f["atr14"].notna() & f["ema200"].notna()
            )  # fmt: skip
            if not mask.any():
                continue
            o = f["open"].to_numpy(float)
            h = f["high"].to_numpy(float)
            low_ = f["low"].to_numpy(float)
            c = f["close"].to_numpy(float)
            atr = f["atr14"].to_numpy(float)
            last = len(c) - MAX_HOLD - 2
            for i in np.flatnonzero(mask.to_numpy()):
                if i < 1 or i >= last:
                    continue
                entry, a = float(o[i + 1]), float(atr[i])
                if not (np.isfinite(entry) and np.isfinite(a)) or a <= 0 or entry <= 0:
                    continue
                stop = entry - STOP_ATR * a
                res = _barrier(
                    o, h, low_, c, i + 1,
                    entry=entry, stop=stop, target=entry + TARGET_R * (entry - stop),
                )  # fmt: skip
                if res is None:
                    continue
                label, gross_r = res
                r = f.iloc[i]
                # `label` (reached 2R before the stop) has a ~3% base rate at this geometry -
                # a 2R target on a 3xATR stop is a 6-ATR move inside 10 sessions, which
                # almost never happens, so the rule's positive expectancy comes from the
                # TIMEOUT exits, not the target. Training on a 3%-positive label would be
                # predicting the wrong event; `label_profit` (did the trade end green) is
                # balanced and is what the P&L actually turns on. Both are stored; the
                # trainer uses label_profit and threshold selection optimises net R
                # regardless, since it scores on gross_r. Decided from the base rate BEFORE
                # any model was fit or any test row was looked at.
                hi52, lo52 = float(r.get("high52w", np.nan)), float(r.get("low52w", np.nan))
                span = hi52 - lo52
                close_i = float(r["close"])
                rows.append(
                    {
                        "scrip_code": code,
                        "armed_on": f["_d"].iloc[i],
                        "entry_date": f["_d"].iloc[i + 1],
                        "entry": entry,
                        "stop": stop,
                        "label": label,
                        "label_profit": int(gross_r > 0),
                        "gross_r": gross_r,
                        "dist_ema10_atr": (close_i - float(r["ema10"])) / a,
                        "dist_ema20_atr": (close_i - float(r["ema20"])) / a,
                        "dist_ema50_atr": (close_i - float(r["ema50"])) / a,
                        "dist_ema200_atr": (close_i - float(r["ema200"])) / a,
                        "ema20_slope": float(r.get("ema20_slope", 0.0) or 0.0) * 100,
                        "ema50_slope": float(r.get("ema50_slope", 0.0) or 0.0) * 100,
                        "adx14": float(r.get("adx14", np.nan)),
                        "di_spread": float(r.get("plus_di", np.nan))
                        - float(r.get("minus_di", np.nan)),
                        "rsi14": float(r.get("rsi14", np.nan)),
                        "rsi2": float(r.get("rsi2", np.nan)),
                        "macd_hist_atr": float(r.get("macd_hist", 0.0) or 0.0) / a,
                        "roc5": float(r.get("roc5", np.nan)),
                        "roc20": float(r.get("roc20", np.nan)),
                        "atr_pct": float(r.get("atr_pct", np.nan)),
                        "atr_pct_rank": float(r.get("atr_pct_rank", np.nan)),
                        "bb_width": float(r.get("bb_width", np.nan)),
                        "range_contraction": float(r.get("range_contraction", np.nan)),
                        "vol_ratio50": float(r.get("vol_ratio50", np.nan)),
                        "vol_dryup": float(r.get("vol_dryup", np.nan)),
                        "updown_vol20": float(r.get("updown_vol20", np.nan)),
                        "nr7": 1.0 if bool(r.get("nr7", False)) else 0.0,
                        "inside_day": 1.0 if bool(r.get("inside_day", False)) else 0.0,
                        "dist_52w_high_atr": (close_i - hi52) / a,
                        "pct_in_52w_range": 50.0 if span <= 0 else (close_i - lo52) / span * 100,
                        "dist_high20_atr": (close_i - float(r.get("high20", close_i))) / a,
                        "day_of_week": float(pd.Timestamp(f.index[i]).weekday()),
                    }
                )

    df = pd.DataFrame(rows)
    if len(df) and not ctx.empty:
        df = df.join(ctx, on="armed_on")
    for col in ("nifty_return_1d", "nifty_return_5d", "vix", "vix_change_5d"):
        if col not in df.columns:
            df[col] = np.nan
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(
        f"\n{len(df)} RSI(2) entries -> {out}\n"
        f"base rate (hit {TARGET_R}R before {STOP_ATR}xATR stop): {df['label'].mean():.4f}\n"
        f"mean gross R: {df['gross_r'].mean():+.4f}"
    )


@app.command()
def train(
    dataset: Path = typer.Option(DATASET, "--dataset"),
    root: Path = typer.Option(Path("."), "--root"),
    n_splits: int = typer.Option(4, "--splits"),
    final_test_frac: float = typer.Option(0.2, "--final-test-frac"),
    risk_pct: float = typer.Option(0.0075, "--risk-pct"),
    out: Path = typer.Option(Path("data/reports/mr_model_report.txt"), "--out"),
) -> None:
    """Purged walk-forward on the dev portion, one locked scoring of the reserved tail."""
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType
    from tradedesk.prediction.train import (
        DEFAULT_SEED,
        evaluate,
        make_baseline,
        make_xgboost,
        purged_walk_forward,
        select_threshold,
    )

    settings = load_config(root)
    cm = EquityCostModel(settings.risk.costs)
    risk_rupees = float(settings.risk.trading_capital) * risk_pct

    df = pd.read_csv(dataset, parse_dates=["armed_on", "entry_date"])
    df["armed_date"] = df["armed_on"].dt.date
    df = df.sort_values("armed_date").reset_index(drop=True)
    df[FEATURES] = df[FEATURES].replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["label_profit", "gross_r"]).reset_index(drop=True)

    # Per-trade cost in R, from the real cost model at the chosen sizing.
    cost_r = []
    for e, s in zip(df["entry"].to_numpy(), df["stop"].to_numpy(), strict=True):
        rps = e - s
        qty = max(1.0, round(risk_rupees / rps))
        c = cm.round_trip_cost(
            trade_type=TradeType.DELIVERY, qty=qty,
            entry_price=Decimal(str(round(e, 2))), exit_price=Decimal(str(round(e, 2))),
        )  # fmt: skip
        cost_r.append(float(c.total) / (qty * rps))
    df["cost_r"] = cost_r
    df["net_r"] = df["gross_r"] - df["cost_r"]

    # Locked final test: the most recent final_test_frac of DISTINCT SESSIONS, reserved
    # before any fold, tuning or threshold choice sees the data.
    sessions = sorted(df["armed_date"].unique())
    cut = sessions[int(len(sessions) * (1 - final_test_frac))]
    dev = df[df["armed_date"] < cut].reset_index(drop=True)
    test = df[df["armed_date"] >= cut].reset_index(drop=True)

    lines: list[str] = []

    def say(s: str = "") -> None:
        typer.echo(s)
        lines.append(s)

    say(
        f"dataset {dataset}  rows={len(df)}  "
        f"base_rate(profitable)={df['label_profit'].mean():.4f}  "
        f"base_rate(hit 2R)={df['label'].mean():.4f}"
    )
    say(f"dev={len(dev)} (< {cut})   LOCKED TEST={len(test)} (>= {cut})")
    say(f"cost drag at {risk_pct:.2%} risk: mean {df['cost_r'].mean():.4f}R")
    say()

    X_dev = dev[FEATURES].to_numpy(float)
    y_dev = dev["label_profit"].to_numpy(int)
    r_dev = dev["gross_r"].to_numpy(float)
    folds = purged_walk_forward(
        list(dev["armed_date"]), n_splits=n_splits,
        embargo_sessions=settings.ml.embargo_sessions,
    )  # fmt: skip
    say(f"purged walk-forward: {len(folds)} folds, embargo {settings.ml.embargo_sessions}")

    candidates: dict[str, Any] = {"logistic": make_baseline(DEFAULT_SEED)}
    xgb = make_xgboost(DEFAULT_SEED)
    if xgb is not None:
        candidates["xgboost"] = xgb

    best_name, best_brier, best_oos = None, float("inf"), None
    for name, est in candidates.items():
        oos_p = np.full(len(dev), np.nan)
        for fold in folds:
            # Fold carries positional indices into the date sequence it was given, which is
            # dev's own row order - use them directly rather than re-deriving.
            tr_idx, te_idx = fold.train_idx, fold.test_idx
            if len(tr_idx) < 50 or len(te_idx) == 0 or len(set(y_dev[tr_idx])) < 2:
                continue
            from sklearn.base import clone

            m = clone(est)
            m.fit(np.nan_to_num(X_dev[tr_idx]), y_dev[tr_idx])
            oos_p[te_idx] = m.predict_proba(np.nan_to_num(X_dev[te_idx]))[:, 1]
        scored = ~np.isnan(oos_p)
        if scored.sum() < 100:
            say(f"  {name}: too few OOS predictions, skipped")
            continue
        brier = float(np.mean((oos_p[scored] - y_dev[scored]) ** 2))
        say(f"  {name}: OOS n={int(scored.sum())} Brier={brier:.5f}")
        if brier < best_brier:
            best_name, best_brier, best_oos = name, brier, (oos_p, scored)

    if best_name is None or best_oos is None:
        say("no usable model")
        out.write_text("\n".join(lines), encoding="utf-8")
        return

    oos_p, scored = best_oos
    say(f"\nchosen: {best_name} (OOS Brier {best_brier:.5f})")
    m_dev = evaluate(y_dev[scored], oos_p[scored], r_dev[scored], threshold=0.5)
    say(f"dev OOS: ROC-AUC={m_dev.roc_auc}  PR-AUC={m_dev.pr_auc}  Brier={m_dev.brier:.5f}")

    thr, grid, has_edge = select_threshold(y_dev[scored], oos_p[scored], r_dev[scored])
    say(f"threshold chosen on validation folds: {thr:.2f}  has_edge={has_edge}")

    # Refit on ALL dev data, then score the locked test exactly once.
    from sklearn.base import clone

    final = clone(candidates[best_name])
    final.fit(np.nan_to_num(X_dev), y_dev)
    p_test = final.predict_proba(np.nan_to_num(test[FEATURES].to_numpy(float)))[:, 1]

    say("\n" + "=" * 72)
    say("LOCKED TEST - scored once")
    say("=" * 72)
    m_te = evaluate(
        test["label_profit"].to_numpy(int), p_test, test["gross_r"].to_numpy(float),
        threshold=thr,
    )
    say(f"ROC-AUC={m_te.roc_auc}  PR-AUC={m_te.pr_auc}  Brier={m_te.brier:.5f}")
    say()

    base_net = float(test["net_r"].mean())
    base_se = float(test["net_r"].std() / np.sqrt(len(test)))
    say(f"{'gate':<22} {'n':>6} {'hit':>7} {'gross':>9} {'net':>9} {'t(net>0)':>9}")
    say(
        f"{'ALL RSI(2) entries':<22} {len(test):>6} {test['label_profit'].mean():>7.4f} "
        f"{test['gross_r'].mean():>+9.4f} {base_net:>+9.4f} {base_net / base_se:>9.2f}"
    )
    for t in (thr, 0.5, 0.55, 0.6, 0.65):
        sel = test[p_test >= t]
        if len(sel) < 30:
            say(f"{'p >= ' + format(t, '.2f'):<22} {len(sel):>6}   (too few to judge)")
            continue
        se = float(sel["net_r"].std() / np.sqrt(len(sel)))
        tag = f"p >= {t:.2f}" + (" *chosen" if t == thr else "")
        say(
            f"{tag:<22} {len(sel):>6} {sel['label_profit'].mean():>7.4f} "
            f"{sel['gross_r'].mean():>+9.4f} {sel['net_r'].mean():>+9.4f} "
            f"{(sel['net_r'].mean() / se if se > 0 else float('nan')):>9.2f}"
        )
    say()
    say("The question is whether any gated row beats 'ALL RSI(2) entries' on NET by enough")
    say("to justify the trades given up. ROC-AUC above is secondary to that column.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    typer.echo(f"\nreport -> {out}")


if __name__ == "__main__":
    app()
