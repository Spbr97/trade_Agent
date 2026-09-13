"""Run the exact same random-timing null test barrier_sweep.py ran for NSE (2026-09-13,
found all 3 setups worse than random by 0.21-0.26R), but for BSE instead.

Genuinely untested ground: every random-baseline check this project has run was NSE only.
BSE has different liquidity and stock composition (a curated 5-stock tracker watchlist plus
the full-universe live monitoring added 2026-09-12) - the same setups could plausibly behave
differently there. Not guaranteed to help; nobody has checked.

Same two-phase shape as barrier_sweep.py: `dump` runs the BSE backtest once and saves every
triggered signal; `test` resolves each real signal from its own entry date AND a matched
random entry (same stock, same window, same stop-width fraction, same target multiple), at
the LIVE config (2R target, 10-session hold - matching config/setups.yaml's
partial_at_r: 2.0), using BSE's own bse.duckdb.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import typer

from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from barrier_sweep import _fast_barrier  # noqa: E402 - reuse the proven barrier, don't duplicate it

app = typer.Typer(add_completion=False)


@app.callback()
def _cli() -> None:
    """See scripts/validate_intraday_setup.py's callback docstring - avoids Typer's
    single-command collapse so `dump`/`test` behave as real subcommands."""


DUMP = Path("data/reports/bse_barrier_signals.csv")
BSE_DB = Path("data/bse.duckdb")


@app.command()
def dump(
    from_: str = typer.Option("2023-09-01", "--from"),
    to: str | None = typer.Option(None, "--to"),
    db: Path = typer.Option(BSE_DB, "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    out: Path = typer.Option(DUMP, "--out"),
) -> None:
    """Run the BSE backtest once and dump every triggered signal's entry/stop/date."""
    from datetime import datetime

    from tradedesk.backtest import prepare_market, run_backtest
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.cli import _reference_code
    from tradedesk.engine.lifecycle import SignalState
    from tradedesk.markets import bse_market
    from tradedesk.prediction.train import _fill_from_note
    from tradedesk.scan import scan_config

    settings = load_config(root)
    mkt = bse_market(settings)
    start = datetime.strptime(from_, "%Y-%m-%d").date()
    end = datetime.strptime(to, "%Y-%m-%d").date() if to else datetime.now(IST).date()
    cfg = scan_config(settings, end, None, market=mkt)
    cfg.start = start

    with CandleStore(db) as store:
        ref = _reference_code(store, root, mkt.benchmark_name, exch="BSE")
        cfg.vix_code = None  # bse_market.vix_required is False - no VIX loaded for BSE
        codes = [c for c in store.codes(Interval.D1) if c != ref]
        typer.echo(f"BSE: backtesting {len(codes)} codes {start} -> {end}")
        md = prepare_market(store, codes, ref, cfg)
    result = run_backtest(md, cfg)

    rows: list[dict[str, Any]] = []
    for ts in result.signals:
        trig = next((h for h in ts.history if h.to_state is SignalState.TRIGGERED), None)
        if trig is None:
            continue
        sig = ts.signal
        fill = _fill_from_note(trig.note) or sig.trigger
        if fill <= sig.stop:
            continue
        rows.append(
            {
                "signal_id": sig.id, "scrip_code": sig.scrip_code, "setup": sig.setup.value,
                "armed_on": sig.armed_on, "entry_date": trig.on, "entry": fill,
                "stop": sig.stop, "t1": sig.t1, "t2": sig.t2, "atr": sig.atr,
            }
        )  # fmt: skip
    df = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.echo(f"{len(df)} triggered BSE signals -> {out}")
    if len(df):
        typer.echo(f"by setup:\n{df['setup'].value_counts().to_string()}")


@app.command()
def test(
    dump_path: Path = typer.Option(DUMP, "--dump"),
    db: Path = typer.Option(BSE_DB, "--db"),
    seed: int = typer.Option(20260101, "--seed"),
    target_r: float = typer.Option(2.0, "--target-r"),
    hold: int = typer.Option(10, "--hold"),
) -> None:
    """Same method as barrier_sweep.py's null_test: hold stock, window and stop-width
    fraction fixed, randomise only WHEN you enter, at the LIVE 2R/10-session config."""
    import numpy as np

    from tradedesk.broker.indstocks.models import Interval

    rng = np.random.default_rng(seed)
    sig = pd.read_csv(dump_path, parse_dates=["entry_date"])
    if not len(sig):
        typer.secho("no dumped BSE signals - run `dump` first", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    lo, hi = sig["entry_date"].min().date(), sig["entry_date"].max().date()
    typer.echo(
        f"BSE null test: {len(sig)} signals, window {lo} -> {hi}, "
        f"target {target_r}R, hold {hold}\n"
    )

    # Load each code's full daily bars ONCE, with a positional date index for both the real
    # (entry_date-anchored) and random (window-anchored) resolutions below.
    bars_by_code: dict[str, Any] = {}
    with CandleStore(db) as store:
        for code in sorted(sig["scrip_code"].unique()):
            df = store.load(code, Interval.D1)
            if df is None or not len(df):
                continue
            dates = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").date
            w_start, w_end = int(dates.searchsorted(lo)), int(dates.searchsorted(hi))
            bars_by_code[code] = (
                dates,
                df["open"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), df["close"].to_numpy(float), w_start, w_end,
            )  # fmt: skip

    real_rows: list[dict[str, Any]] = []
    null_rows: list[dict[str, Any]] = []
    for _, s in sig.iterrows():
        arr = bars_by_code.get(s["scrip_code"])
        if arr is None:
            continue
        dates, o, h, low_, c, w_start, w_end = arr
        if w_end - w_start <= hold + 2:
            continue
        entry_real, stop_real = float(s["entry"]), float(s["stop"])
        if entry_real <= 0 or entry_real <= stop_real:
            continue
        risk_frac = (entry_real - stop_real) / entry_real  # stop width as a fraction of price

        # Real: resolve forward from this signal's OWN entry date.
        pos = int(dates.searchsorted(s["entry_date"].date()))
        if pos < len(c):
            target_real = entry_real + target_r * (entry_real - stop_real)
            label, outcome, _, exit_price = _fast_barrier(
                o[pos:], h[pos:], low_[pos:], c[pos:],
                entry=entry_real, stop=stop_real, target=target_real, max_hold=hold,
            )
            if outcome != "insufficient" and exit_price is not None:
                real_rows.append(
                    {
                        "setup": s["setup"], "hit": label,
                        "gross_r": (exit_price - entry_real) / (entry_real - stop_real),
                    }
                )

        # Null: same stock, same window, same stop-width FRACTION, but a random entry day.
        i = int(rng.integers(w_start, w_end))
        entry_n = float(c[i])
        if entry_n <= 0:
            continue
        risk_n = entry_n * risk_frac
        stop_n = entry_n - risk_n
        target_n = entry_n + target_r * risk_n
        label, outcome, _, exit_price = _fast_barrier(
            o[i + 1 :], h[i + 1 :], low_[i + 1 :], c[i + 1 :],
            entry=entry_n, stop=stop_n, target=target_n, max_hold=hold,
        )
        if outcome == "insufficient" or exit_price is None:
            continue
        null_rows.append({"hit": label, "gross_r": (exit_price - entry_n) / risk_n})

    real = pd.DataFrame(real_rows)
    null = pd.DataFrame(null_rows)

    typer.echo(f"{'':>12} {'n':>6} {'hit_rate':>9} {'gross_r':>9}")
    typer.echo(
        f"{'real setups':>12} {len(real):>6} {real.hit.mean():>9.4f} "
        f"{real.gross_r.mean():>9.4f}"
    )
    typer.echo(
        f"{'random null':>12} {len(null):>6} {null.hit.mean():>9.4f} "
        f"{null.gross_r.mean():>9.4f}"
    )
    edge = real.gross_r.mean() - null.gross_r.mean()
    typer.echo(f"\nedge (real - random): {edge:+.4f}R")
    if edge > 0:
        typer.secho(
            "BSE setups BEAT random timing here - worth digging further.", fg=typer.colors.GREEN
        )
    else:
        typer.secho("BSE setups do NOT beat random timing either.", fg=typer.colors.RED)

    typer.echo("\nby setup (real trades only):")
    typer.echo(real.groupby("setup")["gross_r"].agg(["size", "mean"]).to_string())


if __name__ == "__main__":
    app()
