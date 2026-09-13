"""Cross-sectional ranking of the RSI(2) mean-reversion entry, instead of a fixed threshold.

Context: `rsi2<10 & above ema50` (locked geometry: stop 3xATR, target 2R, hold 10) showed a
real, replicated gross edge (+0.067R, t=3.35 on a locked test half - see CLAUDE.md 2026-09-13)
that does not survive costs at safe position sizing. A fixed threshold takes EVERY day where
a stock happens to close under RSI(2)=10, regardless of how oversold the wider market is that
day. Cross-sectional ranking is a different, well-established factor-model technique: each
day, rank all eligible stocks by RSI(2) and take only the K most oversold - concentrating on
the most extreme dips rather than every dip that clears an arbitrary absolute bar.

The correct null for THIS question is not "random day" (already answered) but "random STOCK
on the same day" - same dates, same trade count, but the K stocks each day are picked at
random from the eligible universe instead of by RSI(2) rank. That isolates whether the
RANKING criterion itself adds anything beyond "something happened on an active day."

Two commands: `build` computes the cross-sectional panel once (slow, full universe) and dumps
selected trades for a grid of K; `report` reads the dump and prints the train/test comparison.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore

app = typer.Typer(add_completion=False)


@app.callback()
def _cli() -> None:
    """See scripts/validate_intraday_setup.py's callback docstring."""


STOP_ATR = 3.0
TARGET_R = 2.0
MAX_HOLD = 10
K_GRID = [1, 3, 5, 10]
OUT = Path("data/reports/cross_sectional_rsi2.csv")


def _barrier(o: Any, h: Any, low_: Any, c: Any, start: int, *, entry: float, stop: float, target: float) -> float | None:  # noqa: E501
    """Same gap/tie/vertical rules as entry_search.py/labeling.py."""
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
def build(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    start: str = typer.Option("2023-09-01", "--from"),
    end: str = typer.Option("2026-09-13", "--to"),
    split: str = typer.Option("2025-03-01", "--split"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    seed: int = typer.Option(20260101, "--seed"),
    out: Path = typer.Option(OUT, "--out"),
) -> None:
    from datetime import datetime

    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.indicators import daily_features

    settings = load_config(root)
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    dsplit = datetime.strptime(split, "%Y-%m-%d").date()
    rng = np.random.default_rng(seed)

    panel_rows: list[dict[str, Any]] = []
    arrays: dict[str, tuple[Any, ...]] = {}
    with CandleStore(db) as store:
        codes = store.codes(Interval.D1)
        if max_codes:
            codes = codes[:max_codes]
        typer.echo(f"loading {len(codes)} codes {d0} -> {d1}")
        for n_done, code in enumerate(codes, 1):
            if n_done % 400 == 0:
                typer.echo(f"  {n_done}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            f["_d"] = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            turnover = (f["close"] * f["volume"]).rolling(20).mean()
            elig = (
                (f["close"] > f["ema50"]) & (turnover >= min_turnover) & (f["close"] >= min_price)  # noqa: E501
                & (f["_d"] >= d0) & (f["_d"] <= d1) & f["atr14"].notna() & f["rsi2"].notna()
            )  # fmt: skip
            if not elig.any():
                continue
            arrays[code] = (
                f["open"].to_numpy(float), f["high"].to_numpy(float),
                f["low"].to_numpy(float), f["close"].to_numpy(float), f["atr14"].to_numpy(float),
            )  # fmt: skip
            idx_pos = np.flatnonzero(elig.to_numpy())
            for i in idx_pos:
                panel_rows.append(
                    {"date": f["_d"].iloc[i], "code": code, "pos": int(i), "rsi2": float(f["rsi2"].iloc[i])}  # noqa: E501
                )

    panel = pd.DataFrame(panel_rows)
    typer.echo(f"\npanel: {len(panel)} eligible (date, code) rows across {panel['code'].nunique()} codes")  # noqa: E501

    # Rank within each date, ascending RSI(2) = most oversold first.
    panel["rank"] = panel.groupby("date")["rsi2"].rank(method="first", ascending=True)
    panel["day_size"] = panel.groupby("date")["code"].transform("size")

    trades: list[dict[str, Any]] = []
    for K in K_GRID:
        selected = panel[panel["rank"] <= K]
        # Matched null: same dates, same K per date, but a RANDOM eligible code instead of
        # the K most oversold - isolates whether RANKING itself (not just "an active day")
        # is what adds value.
        null_frames = []
        for _date, grp in panel.groupby("date"):
            n_take = min(K, len(grp))
            null_frames.append(grp.sample(n=n_take, random_state=rng.integers(0, 2**31)))
        null_selected = pd.concat(null_frames) if null_frames else panel.iloc[:0]

        # Tag is "random", never "null" - pandas' read_csv treats the literal string "null"
        # as a missing-value sentinel by default, so a "null" tag silently becomes NaN on
        # the round trip through the saved CSV and vanishes from value_counts() (found by
        # actually inspecting the written file, not assumed safe).
        for tag, sel in (("ranked", selected), ("random", null_selected)):
            last_entry_cache: dict[str, int] = {}
            for _, row in sel.iterrows():
                code, pos = row["code"], row["pos"]
                arr = arrays.get(code)
                if arr is None:
                    continue
                o, h, low_, c, atr = arr
                if pos + 1 >= len(c) - MAX_HOLD - 1:
                    continue
                # one trade per code at a time - skip if still "in" a prior trade's window
                if pos <= last_entry_cache.get(code, -1):
                    continue
                entry, a = float(o[pos + 1]), float(atr[pos])
                if not (np.isfinite(entry) and np.isfinite(a)) or a <= 0 or entry <= 0:
                    continue
                stop = entry - STOP_ATR * a
                r = _barrier(o, h, low_, c, pos + 1, entry=entry, stop=stop, target=entry + TARGET_R * (entry - stop))  # noqa: E501
                if r is None:
                    continue
                last_entry_cache[code] = pos + MAX_HOLD
                trades.append(
                    {
                        "k": K, "tag": tag, "date": row["date"],
                        "half": "train" if row["date"] < dsplit else "test",
                        "gross_r": r,
                    }
                )

    df = pd.DataFrame(trades)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(f"\n{len(df)} trades across K={K_GRID} -> {out}")
    report(path=out)


@app.command()
def report(path: Path = typer.Option(OUT, "--path")) -> None:
    df = pd.read_csv(path)
    typer.echo(f"\n{'K':>4} {'tag':>7} {'half':>6} {'n':>6} {'gross_r':>9} {'t':>7}")
    rows = []
    for k in sorted(df["k"].unique()):
        for tag in ("ranked", "random"):
            for half in ("train", "test"):
                g = df[(df.k == k) & (df.tag == tag) & (df.half == half)]["gross_r"]
                if len(g) < 5:
                    continue
                se = g.std() / np.sqrt(len(g))
                t = g.mean() / se if se > 0 else float("nan")
                rows.append((k, tag, half, len(g), g.mean(), t))
                typer.echo(f"{k:>4} {tag:>7} {half:>6} {len(g):>6} {g.mean():>+9.4f} {t:>7.2f}")

    typer.echo("\nedge = ranked test mean - null test mean, per K:")
    for k in sorted(df["k"].unique()):
        r = df[(df.k == k) & (df.tag == "ranked") & (df.half == "test")]["gross_r"]
        n = df[(df.k == k) & (df.tag == "random") & (df.half == "test")]["gross_r"]
        if len(r) < 5 or len(n) < 5:
            continue
        typer.echo(f"  K={k}: ranked {r.mean():+.4f} (n={len(r)}) vs null {n.mean():+.4f} (n={len(n)}) -> edge {r.mean()-n.mean():+.4f}R")  # noqa: E501


if __name__ == "__main__":
    app()
