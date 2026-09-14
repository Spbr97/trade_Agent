"""Run VWAP Reclaim's real signals through the null-timing gate (step 6 of the intraday
plan). This is the step that decides whether the setup built in step 5 may ever be trusted -
per CLAUDE.md's own finding that all three live DAILY setups post plausible-looking win
rates while being worse than random, a mechanical setup that merely "fires sometimes" is not
evidence of anything; only this comparison is.

Pipeline: sweep VwapReclaim.arm() across each backfilled code's full M5 history -> simulate
a simple continuation fill (the first later bar, within a short confirmation window, whose
high reaches the trigger) -> resolve with the session-bounded triple barrier -> compare the
realised population against tradedesk.backtest.null_baseline.run_null_baseline.

No config, CLI command or live path is touched by this script - it only reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from tradedesk.backtest.null_baseline import (
    NullBaselineResult,
    RealisedTrade,
    run_null_baseline,
    sessions_in,
)
from tradedesk.broker.indstocks.models import Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import intraday_features
from tradedesk.markets.costs import EquityCostModel
from tradedesk.setups.intraday.base import IntradaySetupContext
from tradedesk.setups.intraday.vwap_reclaim import VwapReclaim

app = typer.Typer(add_completion=False)


@app.callback()
def _cli() -> None:
    """A Typer app with only one @app.command() collapses into a single-command CLI (bare
    `script.py [ARGS]` works, but `script.py run [ARGS]` then treats "run" itself as an
    ARG) - this empty callback forces normal subcommand mode instead, so `run` behaves as
    a real subcommand name rather than a trap for anyone reading the other scripts here
    (entry_search.py) and expecting the same `script.py <verb> ...`
    pattern to work identically."""


DEFAULT_CODES = ["NSE_2885", "NSE_1333", "NSE_11536", "NSE_1594", "NSE_3045"]
CONFIRM_WITHIN_BARS = 5  # how long a signal stays valid awaiting continuation, in bars


def _sweep_and_resolve(
    code: str, f: pd.DataFrame, sessions: list[tuple[int, int]], params: dict[str, Any]
) -> list[RealisedTrade]:
    """Arm on every bar, simulate a continuation fill, resolve within the session."""
    setup = VwapReclaim()
    ctx = IntradaySetupContext(scrip_code=code, symbol=code, interval=Interval.M5)
    o = f["open"].to_numpy(float)
    h = f["high"].to_numpy(float)
    low_ = f["low"].to_numpy(float)
    c = f["close"].to_numpy(float)
    sess_end_at = np.zeros(len(f), dtype=int)
    for start, end in sessions:
        sess_end_at[start : end + 1] = end

    out: list[RealisedTrade] = []
    last_confirmed_end = -1  # avoid overlapping trades on the same stock at once
    for i in range(2, len(f) - 1):
        if i <= last_confirmed_end:
            continue
        session_end = int(sess_end_at[i])
        sig = setup.arm(f.iloc[: i + 1], ctx, params)
        if sig is None:
            continue
        confirm_end = min(i + CONFIRM_WITHIN_BARS, session_end)
        fill_bar = None
        for k in range(i + 1, confirm_end + 1):
            if h[k] >= sig.trigger:
                fill_bar = k
                break
        if fill_bar is None:
            continue  # expired unconfirmed - the "retest + continuation" never happened
        entry = float(max(o[fill_bar], sig.trigger)) if o[fill_bar] >= sig.trigger else float(sig.trigger)  # noqa: E501
        risk = entry - sig.stop
        if risk <= 0:
            continue
        target = entry + (sig.t1 - sig.trigger)  # preserve the partial target's R multiple
        gross_r = _resolve(o, h, low_, c, fill_bar, session_end, entry=entry, stop=sig.stop, target=target)  # noqa: E501
        out.append(
            RealisedTrade(
                scrip_code=code, entry_bar=fill_bar,
                session_start=next(s for s, e in sessions if s <= fill_bar <= e),
                session_end=session_end, entry=entry, stop=sig.stop, target=target, gross_r=gross_r,  # noqa: E501
            )
        )
        last_confirmed_end = session_end  # one trade per session per code, like a real desk
    return out


def _resolve(
    o: Any, h: Any, low_: Any, c: Any, start: int, end: int, *, entry: float, stop: float,
    target: float,
) -> float:
    risk = entry - stop
    for i in range(start, end + 1):
        if i > start and o[i] <= stop:
            return (float(o[i]) - entry) / risk
        if low_[i] <= stop:
            return -1.0
        if h[i] >= target:
            return (target - entry) / risk
    return (float(c[end]) - entry) / risk


@app.command()
def run(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    codes: list[str] = typer.Argument(None),
    n_cohorts: int = typer.Option(1000, "--n-cohorts"),
    alpha: float = typer.Option(0.05, "--alpha"),
) -> None:
    targets = list(codes) if codes else DEFAULT_CODES
    settings = load_config(root)
    cost_model = EquityCostModel(settings.risk.costs)

    trades: list[RealisedTrade] = []
    bars_by_code: dict[str, tuple[Any, Any, Any, Any]] = {}
    with CandleStore(db) as store:
        for code in targets:
            raw = store.load(code, Interval.M5)
            if raw is None or raw.empty:
                typer.echo(f"  {code}: no M5 bars stored, skipping")
                continue
            f = intraday_features(raw)
            sessions = sessions_in(pd.DatetimeIndex(f.index))
            bars_by_code[code] = (
                f["open"].to_numpy(float), f["high"].to_numpy(float),
                f["low"].to_numpy(float), f["close"].to_numpy(float),
            )  # fmt: skip
            code_trades = _sweep_and_resolve(code, f, sessions, {})
            typer.echo(f"  {code}: {len(f)} bars, {len(sessions)} sessions -> {len(code_trades)} realised trades")  # noqa: E501
            trades.extend(code_trades)

    typer.echo(f"\n{len(trades)} realised VWAP Reclaim trades across {len(targets)} codes\n")
    if not trades:
        typer.secho("no trades produced - nothing to validate", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    result: NullBaselineResult = run_null_baseline(
        trades, bars_by_code, n_cohorts=n_cohorts, alpha=alpha, cost_model=cost_model,
    )  # fmt: skip
    typer.echo(f"setup mean gross R:  {result.setup_mean_gross_r:+.4f}")
    typer.echo(f"setup mean net R:    {result.setup_mean_net_r:+.4f}  (intraday costs)")
    typer.echo(f"null mean gross R:   {result.null_mean_gross_r:+.4f}  (std {result.null_std_gross_r:.4f}, {result.n_cohorts} cohorts)")  # noqa: E501
    typer.echo(f"edge vs random:      {result.setup_mean_gross_r - result.null_mean_gross_r:+.4f}R")  # noqa: E501
    typer.echo(f"p-value:             {result.p_value:.4f}  (alpha {result.alpha})")
    typer.echo()
    if result.passes:
        typer.secho(
            f"PASSES the null-timing gate (p={result.p_value:.4f} < {result.alpha}) - "
            "still needs the Phase 1 evidence thresholds (trade count, OOS) before it is "
            "eligible to alert.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"DOES NOT beat random timing (p={result.p_value:.4f} >= {result.alpha}). "
            "Per the SDD, this means NO TRADE, not a lowered bar.",
            fg=typer.colors.RED,
        )


if __name__ == "__main__":
    app()
