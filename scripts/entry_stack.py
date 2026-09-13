"""Concentrate the RSI(2) mean-reversion edge found by entry_search.py.

Baseline (locked 2026-09-13, see CLAUDE.md): `rsi2<10 & above ema50`, stop 3xATR, target 2R,
hold 10 - TRAIN gross +0.0725 (t=4.30), TEST gross +0.0665 (t=3.35), but NET is ~breakeven at
policy-compliant risk sizing (0.5-1%). The lever left is concentration: keep only the subset
of those entries that a second condition marks as better, at the cost of fewer trades.

Same discipline as entry_search.py's `optimize`, one level up: each candidate ADDITIONAL
condition is scored on the TRAIN half only (by NET expectancy at a fixed 0.75% risk - the
approximate breakeven point already measured), the single best one is locked, and ONLY THEN
scored once on the held-out TEST half. Testing ~10 stacks against the same train half is
mild multiple-testing risk on top of the ~15 base rules and 96 geometries already searched
in the two prior passes - treat a marginal test result here with extra suspicion, not less.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore

app = typer.Typer(add_completion=False)

STOP_ATR = 3.0
TARGET_R = 2.0
MAX_HOLD = 10
RISK_PCT_FOR_SELECTION = 0.0075  # ~breakeven for the unconditioned rule, from CLAUDE.md
MIN_TRAIN_N = 150  # below this, a "winning" stack is just a small-sample fluke


def _base_mask(f: pd.DataFrame) -> pd.Series[bool]:
    return (f["rsi2"] < 10) & (f["close"] > f["ema50"])


# Every candidate is an ADDITIONAL condition ANDed onto _base_mask. Each has a stated
# trading rationale, not just "whatever scores highest" - a condition with no story is a
# multiple-testing trap waiting to happen.
STACKS: dict[str, Callable[[pd.DataFrame], pd.Series[bool]]] = {
    "+ volume confirmation (vol_ratio50>1.2)": lambda f: f["vol_ratio50"] > 1.2,
    "+ heavy volume (vol_ratio50>1.5)": lambda f: f["vol_ratio50"] > 1.5,
    "+ not near 52w low (pct_in_52w>30%)": lambda f: (
        (f["close"] - f["low52w"]) / (f["high52w"] - f["low52w"]).replace(0, np.nan) > 0.30
    ),
    "+ ADX>20 (trending, not choppy)": lambda f: f["adx14"] > 20,
    "+ ADX<25 (avoid strong downtrend)": lambda f: f["adx14"] < 25,
    "+ close off the day's low (close>mid of range)": lambda f: (
        f["close"] > (f["high"] + f["low"]) / 2
    ),
    "+ deeper oversold (rsi2<5)": lambda f: f["rsi2"] < 5,
    "+ not extended below ema50 (>ema50-1atr)": lambda f: (
        f["close"] > f["ema50"] - f["atr14"]
    ),
    "+ above ema200 too (stronger trend)": lambda f: f["close"] > f["ema200"],
    "+ atr_pct_rank<70 (not in its own vol spike)": lambda f: f["atr_pct_rank"] < 70,
}


def _barrier(
    o: Any, h: Any, low_: Any, c: Any, start: int, *, entry: float, stop: float, target: float
) -> float | None:
    risk = entry - stop
    if risk <= 0:
        return None
    n = min(len(c) - start, MAX_HOLD + 1)
    if n <= 0:
        return None
    for k in range(n):
        i = start + k
        if o[i] <= stop:
            return (float(o[i]) - entry) / risk
        if low_[i] <= stop:
            return -1.0
        if h[i] >= target:
            return TARGET_R
    return (float(c[start + n - 1]) - entry) / risk


@app.command()
def run(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    start: str = typer.Option("2023-09-01", "--from"),
    end: str = typer.Option("2026-09-13", "--to"),
    split: str = typer.Option("2025-03-01", "--split"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    out: Path = typer.Option(Path("data/reports/entry_stack.csv"), "--out"),
) -> None:
    from datetime import datetime

    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.indicators import daily_features
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    settings = load_config(root)
    cm = EquityCostModel(settings.risk.costs)
    risk_rupees = float(settings.risk.trading_capital) * RISK_PCT_FOR_SELECTION
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    dsplit = datetime.strptime(split, "%Y-%m-%d").date()

    recs: list[dict[str, Any]] = []
    with CandleStore(db) as store:
        codes = store.codes(Interval.D1)
        if max_codes:
            codes = codes[:max_codes]
        typer.echo(f"scanning {len(codes)} codes {d0} -> {d1} (split {dsplit})")
        for n_done, code in enumerate(codes, 1):
            if n_done % 400 == 0:
                typer.echo(f"  {n_done}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            f["_d"] = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            turnover = (f["close"] * f["volume"]).rolling(20).mean()
            base_elig = (
                (turnover >= min_turnover) & (f["close"] >= min_price)
                & (f["_d"] >= d0) & (f["_d"] <= d1)
                & f["atr14"].notna() & f["ema200"].notna()
            )  # fmt: skip
            base_mask = _base_mask(f).fillna(False) & base_elig
            if not base_mask.any():
                continue

            o = f["open"].to_numpy(float)
            h = f["high"].to_numpy(float)
            low_ = f["low"].to_numpy(float)
            c = f["close"].to_numpy(float)
            atr = f["atr14"].to_numpy(float)
            dates = f["_d"].to_numpy()
            last_entry = len(c) - MAX_HOLD - 2

            stack_masks: dict[str, Any] = {"(base only)": base_mask.to_numpy()}
            for name, cond in STACKS.items():
                try:
                    stack_masks[name] = (base_mask & cond(f).fillna(False)).to_numpy()
                except Exception:
                    continue

            for stack_name, m in stack_masks.items():
                for i in np.flatnonzero(m):
                    if i >= last_entry or i < 1:
                        continue
                    entry, a = float(o[i + 1]), float(atr[i])
                    if not np.isfinite(entry) or not np.isfinite(a) or a <= 0 or entry <= 0:
                        continue
                    stop = entry - STOP_ATR * a
                    r = _barrier(o, h, low_, c, i + 1, entry=entry, stop=stop,
                                target=entry + TARGET_R * (entry - stop))  # fmt: skip
                    if r is None:
                        continue
                    qty = max(1.0, round(risk_rupees / (entry - stop)))
                    cost = cm.round_trip_cost(
                        trade_type=TradeType.DELIVERY, qty=qty,
                        entry_price=Decimal(str(round(entry, 2))),
                        exit_price=Decimal(str(round(entry, 2))),
                    )
                    cost_r = float(cost.total) / (qty * (entry - stop))
                    recs.append(
                        {
                            "stack": stack_name,
                            "half": "train" if dates[i] < dsplit else "test",
                            "gross_r": r,
                            "net_r": r - cost_r,
                        }
                    )

    df = pd.DataFrame(recs)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(f"\n{len(df)} simulated entries -> {out}\n")
    _report(df)


def _report(df: pd.DataFrame) -> None:
    tr = df[df.half == "train"]
    agg = tr.groupby("stack").agg(
        n=("net_r", "size"), gross=("gross_r", "mean"), net=("net_r", "mean"),
        sd=("net_r", "std"),
    ).reset_index()  # fmt: skip
    agg["se"] = agg["sd"] / np.sqrt(agg["n"])
    agg["t_net"] = agg["net"] / agg["se"]
    eligible = agg[agg.n >= MIN_TRAIN_N].sort_values("net", ascending=False)
    typer.echo(f"TRAIN half (risk {RISK_PCT_FOR_SELECTION:.2%}, min n={MIN_TRAIN_N}):")
    typer.echo(
        eligible[["stack", "n", "gross", "net", "t_net"]].to_string(
            index=False, float_format=lambda x: f"{x:.4f}"
        )
    )

    if eligible.empty:
        typer.echo("\nno stack had enough train samples")
        return
    base_net = float(agg.set_index("stack").loc["(base only)", "net"])
    non_base = eligible[eligible["stack"] != "(base only)"]
    best = non_base.iloc[0] if len(non_base) else None
    if best is None:
        typer.echo("\nno stack beat the sample floor")
        return

    best_name = best["stack"]
    te = df[(df.half == "test") & (df["stack"] == best_name)]
    te_base = df[(df.half == "test") & (df["stack"] == "(base only)")]
    typer.echo(f"\n=== LOCKED: '{best_name}', scored once on TEST ===")
    typer.echo(
        f"  base only  - train net={base_net:+.4f}  "
        f"TEST: n={len(te_base):>5} gross={te_base.gross_r.mean():+.4f} "
        f"net={te_base.net_r.mean():+.4f}"
    )
    if te.empty:
        typer.echo("  best stack - no test samples")
        return
    se_te = te.net_r.std() / np.sqrt(len(te))
    typer.echo(
        f"  best stack - train net={best.net:+.4f} (n={int(best.n)})  "
        f"TEST: n={len(te):>5} gross={te.gross_r.mean():+.4f} "
        f"net={te.net_r.mean():+.4f}  t(net>0)={te.net_r.mean()/se_te:+.2f}"
    )
    typer.echo(
        "\nCompare the two TEST net numbers directly - that is the honest answer to whether"
        "\nconcentrating the entries with this condition is worth the trade count given up."
    )


@app.command()
def report(path: Path = typer.Option(Path("data/reports/entry_stack.csv"), "--path")) -> None:
    _report(pd.read_csv(path))


if __name__ == "__main__":
    app()
