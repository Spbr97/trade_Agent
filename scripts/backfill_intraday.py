"""Backfill intraday candles for a small watchlist, and VERIFY what actually landed.

Step 3 of the intraday plan. Two commands:
  `plan`   - what would be fetched and roughly how many API calls it costs. No network.
  `run`    - fetch, then run the coverage check below and report loudly.
  `verify` - coverage check alone, against whatever is already stored.

Why this exists rather than just calling `tradedesk data load --interval 15minute ...`:
that command fetches perfectly well (IndstocksClient.candles_history already pages backwards
in `interval.max_window_days` windows and filters to the requested span, so a too-wide
request cannot be built). What nothing does today is CHECK WHAT ARRIVED. A window that comes
back short returns HTTP 200 with fewer bars and no error - the documented trap - and
`load_history` cannot distinguish "no bar exists" from "the API returned nothing", the exact
failure already recorded in CLAUDE.md for the daily loader (739 codes silently left a session
behind, `fetched=0, error=None`). On daily bars a one-session gap is visible in `data status`;
on 1-minute bars a missing afternoon inside two years of history is invisible forever and
would quietly corrupt every VWAP, session high and multi-timeframe alignment computed over
it. So: fetch, then prove the coverage, per code per interval.

Scope is deliberately small. 1-minute uses 7-day windows, so ~104 calls per instrument for
two years; at 5 req/sec and 100,000 calls/day, 40-60 liquid instruments is ~20 minutes and a
few percent of the daily budget. Backfilling the full ~2,500-code universe at 1-minute would
be ~260,000 calls - over twice the daily cap - for instruments nobody would scalp.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import typer

from tradedesk.data.candle_store import CandleStore

app = typer.Typer(add_completion=False)

DB = Path("data/tradedesk.duckdb")

# Sub-daily intervals the intraday engine needs, coarsest first so a cheap one fails early.
INTERVALS = ["60minute", "15minute", "5minute", "3minute", "1minute"]

# A deliberately tiny, liquid starting set. These are the names where a scalping signal has
# any chance of being fillable at the modelled slippage; widening this is an explicit,
# budgeted decision, not a default.
DEFAULT_CODES = [
    "NSE_2885",   # RELIANCE
    "NSE_1333",   # HDFCBANK
    "NSE_11536",  # TCS
    "NSE_1594",   # INFY
    "NSE_3045",   # SBIN
]

# NSE cash session, used to work out how many bars a full session should contain.
SESSION_MINUTES = 375  # 09:15 -> 15:30


def _interval_minutes(iv: str) -> int:
    return int(iv.replace("minute", ""))


def _expected_bars_per_session(iv: str) -> int:
    m = _interval_minutes(iv)
    return max(1, SESSION_MINUTES // m)


@app.command()
def plan(
    codes: list[str] = typer.Argument(None),
    years: float = typer.Option(2.0, "--years"),
    intervals: str = typer.Option(",".join(INTERVALS), "--intervals"),
) -> None:
    """Estimate the API cost before spending any of it. Makes no network calls."""
    from tradedesk.broker.indstocks.models import Interval

    targets = list(codes) if codes else DEFAULT_CODES
    ivs = [s.strip() for s in intervals.split(",") if s.strip()]
    total = 0
    typer.echo(f"{len(targets)} codes x {years} years\n")
    typer.echo(f"{'interval':>10} {'window_d':>9} {'calls/code':>11} {'calls':>8}")
    for iv in ivs:
        window = Interval(iv).max_window_days
        per_code = -(-int(years * 365) // window)  # ceil
        calls = per_code * len(targets)
        total += calls
        typer.echo(f"{iv:>10} {window:>9} {per_code:>11} {calls:>8}")
    typer.echo(f"\ntotal ~{total} calls (daily cap 100,000, 5/sec)")
    typer.echo(f"at 5 req/sec that is ~{total / 5 / 60:.1f} minutes of API time")


@app.command()
def run(
    codes: list[str] = typer.Argument(None),
    years: float = typer.Option(2.0, "--years"),
    intervals: str = typer.Option(",".join(INTERVALS), "--intervals"),
    db: Path = typer.Option(DB, "--db"),
) -> None:
    """Fetch, then verify. A verification failure is reported, never swallowed."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.cli import _with_client
    from tradedesk.data.history_loader import load_history

    targets = list(codes) if codes else DEFAULT_CODES
    ivs = [s.strip() for s in intervals.split(",") if s.strip()]
    now = datetime.now(IST)
    start = now - timedelta(days=int(years * 365))

    async def go(client: Any) -> None:
        with CandleStore(db) as store:
            for iv in ivs:
                typer.echo(f"\n=== {iv}: {len(targets)} codes from {start:%Y-%m-%d} ===")
                summary = await load_history(
                    client, store, targets, Interval(iv), start=start, end=now
                )
                typer.echo(f"  fetched {summary.fetched} bars, {len(summary.errors)} errors")
                for r in summary.errors[:10]:
                    typer.echo(f"  ERROR {r.scrip_code}: {r.error}")

    asyncio.run(_with_client(go))
    _verify_impl(targets, ",".join(ivs), db, max_gap_sessions=3, min_session_fill=0.60)


def _verify_impl(
    codes: list[str] | None,
    intervals: str,
    db: Path,
    max_gap_sessions: int,
    min_session_fill: float,
) -> None:
    """Prove what is actually in the store, per code per interval.

    Three questions, none of which `load_history` can answer:
      1. Does the stored span reach back to roughly where it should?
      2. Are there multi-session holes in the middle?
      3. Are the sessions that DO exist actually full, or only a few bars each?

    (3) is the one that matters most for intraday and the one nothing else would catch: a
    session present with 8 of 375 one-minute bars still shows up as "data exists" to every
    caller, and silently breaks session VWAP, session high/low and MTF alignment.

    Caveat confirmed on the first real run (2026-09-13, 5 codes x 5 intervals): every code
    reported exactly 2 "thin" sessions, always the SAME two calendar dates -
    2024-11-01 (Muhurat trading, NSE's one-hour Diwali session outside normal hours) and
    2025-10-21 (a genuinely shortened trading day). Both are real, correctly-fetched data,
    not coverage gaps - `min_session_fill` has no notion of special sessions, so a thin
    count clustered on one or two dates across every code is that, not a real problem.
    Investigate before trusting `coverage OK` if `thin` ever comes back scattered across
    many different dates instead.

    Plain function, not a `@app.command()`, so it takes REAL values - `verify()` below is
    the CLI wrapper with `typer.Option()` defaults, which only resolve when Typer itself
    invokes the command. Calling the decorated function directly (as `run()` needs to,
    right after a fetch) would pass `OptionInfo` objects through instead of `3`/`0.60` and
    crash on the first arithmetic - exactly what happened the first time this ran, caught
    by actually running it against real data rather than assumed to work."""
    from tradedesk.broker.indstocks.models import Interval

    ivs = [s.strip() for s in intervals.split(",") if s.strip()]
    targets = list(codes) if codes else DEFAULT_CODES
    problems: list[str] = []

    with CandleStore(db) as store:
        for iv in ivs:
            typer.echo(f"\n=== {iv} ===")
            expected_per_session = _expected_bars_per_session(iv)
            for code in targets:
                df = store.load(code, Interval(iv))
                if df is None or df.empty:
                    problems.append(f"{code} {iv}: NO BARS STORED")
                    typer.echo(f"  {code:<14} NO BARS")
                    continue
                idx = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata")
                sess = idx.normalize()
                per_session: dict[Any, int] = defaultdict(int)
                for s in sess:
                    per_session[s] += 1
                days = sorted(per_session)
                floor = expected_per_session * min_session_fill
                full = [d for d in days if per_session[d] >= floor]
                thin = len(days) - len(full)

                # multi-session holes, measured in trading days present in the store
                gaps = []
                for a, b in zip(days[:-1], days[1:], strict=True):
                    missing = (b - a).days - 1
                    if missing > max_gap_sessions:
                        gaps.append((a.date(), b.date(), missing))

                typer.echo(
                    f"  {code:<14} {len(df):>8} bars  {days[0].date()} -> {days[-1].date()}  "
                    f"sessions={len(days)} full={len(full)} thin={thin} gaps={len(gaps)}"
                )
                if thin > len(days) * 0.25:
                    problems.append(
                        f"{code} {iv}: {thin}/{len(days)} sessions under "
                        f"{min_session_fill:.0%} of {expected_per_session} bars"
                    )
                for a, b, missing in gaps[:3]:
                    problems.append(f"{code} {iv}: {missing} calendar days missing {a} -> {b}")

    typer.echo("")
    if problems:
        typer.secho(f"{len(problems)} COVERAGE PROBLEM(S):", fg=typer.colors.RED)
        for p in problems[:30]:
            typer.secho(f"  {p}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("coverage OK", fg=typer.colors.GREEN)


@app.command()
def verify(
    codes: list[str] = typer.Argument(None),
    intervals: str = typer.Option(",".join(INTERVALS), "--intervals"),
    db: Path = typer.Option(DB, "--db"),
    max_gap_sessions: int = typer.Option(3, "--max-gap-sessions"),
    min_session_fill: float = typer.Option(
        0.60, "--min-session-fill", help="fraction of a full session's bars before it counts"
    ),
) -> None:
    """CLI entry point - see `_verify_impl` for what this actually checks."""
    _verify_impl(codes, intervals, db, max_gap_sessions, min_session_fill)


if __name__ == "__main__":
    app()
