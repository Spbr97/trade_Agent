"""Classic academic momentum, tested on its own terms - not shoehorned into this project's
ATR-stop/R-multiple framework, which was built for swing trades measured in days, not months.

Context: everything tested this session (base_breakout/trend_pullback/nr7_breakout, RSI(2)
mean reversion) caps at a 10-session hold. The null-timing test found buying strength
(breakouts) loses to random while buying weakness (RSI(2) dips) beats it - consistent with
the documented SHORT-TERM REVERSAL effect (days to a few weeks). Real academic momentum -
buying past winners - is a real, separately documented effect, but at 3-12 MONTH horizons,
which this project has never tested. This script tests it properly: monthly rebalance, rank
by trailing return, hold a fixed multi-month period, measure raw forward return (no ATR
stop - a stop-loss is not part of the classical methodology and would conflate two
different questions).

Null: same rebalance dates, same K per date, but K RANDOM eligible codes instead of the top
trailing-return ones - isolates whether the RANKING SIGNAL (trailing return) adds anything
beyond "something happened on an active rebalance day," the same logic as
cross_sectional_rsi2.py's null.
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


OUT = Path("data/reports/momentum_longhold.csv")
LOOKBACK_GRID = [63, 126, 252]  # ~3, 6, 12 months of trading days
HOLD_GRID = [63, 126]  # ~3, 6 months
K = 5  # top-K by trailing return, per rebalance date


@app.command()
def build(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    start: str = typer.Option("2020-09-01", "--from", help="earlier start - long lookbacks need it"),  # noqa: E501
    end: str = typer.Option("2026-09-13", "--to"),
    split: str = typer.Option("2025-03-01", "--split"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    seed: int = typer.Option(20260101, "--seed"),
    out: Path = typer.Option(OUT, "--out"),
) -> None:
    from datetime import datetime

    from tradedesk.broker.indstocks.models import Interval

    settings = load_config(root)
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    dsplit = datetime.strptime(split, "%Y-%m-%d").date()
    rng = np.random.default_rng(seed)
    max_hold = max(HOLD_GRID)
    max_lookback = max(LOOKBACK_GRID)

    # Per-code: date index, close array, eligibility mask, trailing returns for every
    # lookback, all computed ONCE - the rebalance/ranking loop below only reads from this.
    per_code: dict[str, dict[str, Any]] = {}
    with CandleStore(db) as store:
        codes = store.codes(Interval.D1)
        if max_codes:
            codes = codes[:max_codes]
        typer.echo(f"loading {len(codes)} codes {d0} -> {d1}")
        for n_done, code in enumerate(codes, 1):
            if n_done % 400 == 0:
                typer.echo(f"  {n_done}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < max_lookback + max_hold + 20:
                continue
            dates = pd.DatetimeIndex(raw.index).tz_convert("Asia/Kolkata").date
            close = raw["close"].to_numpy(float)
            open_ = raw["open"].to_numpy(float)
            volume = raw["volume"].to_numpy(float)
            turnover20 = pd.Series(close * volume).rolling(20).mean().to_numpy()
            eligible = (turnover20 >= min_turnover) & (close >= min_price)
            trailing_ret = {}
            for lb in LOOKBACK_GRID:
                r = np.full(len(close), np.nan)
                r[lb:] = close[lb:] / close[:-lb] - 1.0
                trailing_ret[lb] = r
            per_code[code] = {
                "dates": dates, "close": close, "open": open_,
                "eligible": eligible, "trailing_ret": trailing_ret,
            }

    typer.echo(f"\n{len(per_code)} codes with enough history")

    # Monthly rebalance dates: first eligible trading date of each real calendar month,
    # taken from the union of all codes' date indexes so it lines up with real trading days.
    all_dates = sorted({d for v in per_code.values() for d in v["dates"] if d0 <= d <= d1})
    month_keys = pd.Series([(d.year, d.month) for d in all_dates])
    rebalance_dates = [all_dates[i] for i in month_keys.drop_duplicates().index]
    typer.echo(f"{len(rebalance_dates)} monthly rebalance dates")

    # date -> positional index, per code (avoids repeated searchsorted in the inner loop).
    pos_cache: dict[str, dict[Any, int]] = {}
    for code, v in per_code.items():
        pos_cache[code] = {d: i for i, d in enumerate(v["dates"])}

    trades: list[dict[str, Any]] = []
    for lookback in LOOKBACK_GRID:
        for reb_i, reb_date in enumerate(rebalance_dates):
            # Cross-sectional snapshot: every eligible code's trailing return as of this date.
            snapshot = []
            for code, v in per_code.items():
                pos = pos_cache[code].get(reb_date)
                if pos is None or not v["eligible"][pos]:
                    continue
                ret = v["trailing_ret"][lookback][pos]
                if np.isnan(ret):
                    continue
                snapshot.append((code, pos, ret))
            if len(snapshot) < K * 2:
                continue
            snap_df = pd.DataFrame(snapshot, columns=["code", "pos", "ret"])
            top = snap_df.nlargest(K, "ret")
            rnd = snap_df.sample(n=min(K, len(snap_df)), random_state=rng.integers(0, 2**31))

            for tag, sel in (("momentum", top), ("random", rnd)):
                for _, row in sel.iterrows():
                    code, pos = row["code"], int(row["pos"])
                    v = per_code[code]
                    for hold in HOLD_GRID:
                        exit_pos = pos + hold
                        if exit_pos >= len(v["close"]):
                            continue
                        entry = float(v["open"][pos + 1]) if pos + 1 < len(v["open"]) else float(v["close"][pos])  # noqa: E501
                        exit_price = float(v["close"][exit_pos])
                        if entry <= 0:
                            continue
                        ret = (exit_price - entry) / entry
                        trades.append(
                            {
                                "lookback": lookback, "hold": hold, "tag": tag,
                                "date": reb_date,
                                "half": "train" if reb_date < dsplit else "test",
                                "ret": ret,
                            }
                        )
            if reb_i % 20 == 0:
                typer.echo(f"  lookback={lookback} rebalance {reb_i}/{len(rebalance_dates)}")

    df = pd.DataFrame(trades)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(f"\n{len(df)} (lookback x hold x tag) trade-rows -> {out}")
    report(path=out)


@app.command()
def report(path: Path = typer.Option(OUT, "--path")) -> None:
    df = pd.read_csv(path)
    typer.echo(f"\n{'lookback':>9} {'hold':>5} {'tag':>9} {'half':>6} {'n':>6} {'ret':>9} {'t':>7}")  # noqa: E501
    for lookback in sorted(df["lookback"].unique()):
        for hold in sorted(df["hold"].unique()):
            for tag in ("momentum", "random"):
                for half in ("train", "test"):
                    g = df[
                        (df.lookback == lookback) & (df.hold == hold)
                        & (df.tag == tag) & (df.half == half)
                    ]["ret"]
                    if len(g) < 10:
                        continue
                    se = g.std() / np.sqrt(len(g))
                    t = g.mean() / se if se > 0 else float("nan")
                    typer.echo(
                        f"{lookback:>9} {hold:>5} {tag:>9} {half:>6} {len(g):>6} "
                        f"{g.mean():>+9.4f} {t:>7.2f}"
                    )

    typer.echo("\nedge (momentum test - random test), per (lookback, hold):")
    for lookback in sorted(df["lookback"].unique()):
        for hold in sorted(df["hold"].unique()):
            m = df[(df.lookback == lookback) & (df.hold == hold) & (df.tag == "momentum") & (df.half == "test")]["ret"]  # noqa: E501
            n = df[(df.lookback == lookback) & (df.hold == hold) & (df.tag == "random") & (df.half == "test")]["ret"]  # noqa: E501
            if len(m) < 10 or len(n) < 10:
                continue
            typer.echo(
                f"  lookback={lookback} hold={hold}: momentum {m.mean():+.4f} (n={len(m)}) "
                f"vs random {n.mean():+.4f} (n={len(n)}) -> edge {m.mean()-n.mean():+.4f}"
            )


if __name__ == "__main__":
    app()
