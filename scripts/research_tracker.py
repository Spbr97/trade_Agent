"""CLI over tradedesk.research_tracker (the shared module - see its docstring for the full
design rationale, including the 2026-09-14 multi-market generalisation). Kept thin on
purpose: dashboard/app.py's /api/research/* endpoints import the same shared functions
directly, so the CLI report and the dashboard's Research tab can never disagree about a
candidate's numbers.

`--market nse|crypto|bse` selects the universe, database and log/session files - each
market's log is its own dataset, never pooled (same discipline as everywhere else in this
project). NSE keeps its original (non-suffixed) log path since it predates this option and
is already scheduled; crypto/BSE get their own market-suffixed files automatically.

Commands: `run` (nightly: resolve, then scan, then flag, then report), `report`, `scan`,
`resolve`.
"""

from __future__ import annotations

from pathlib import Path

import typer

from tradedesk.analysis import db_for
from tradedesk.config import load_config
from tradedesk.research_tracker import (
    build_report,
    cost_r_for,
    flag_research_findings,
    load_log,
    log_path_for,
    resolve_calls,
    save_session_report,
    scan_universe,
    sessions_dir_for,
)

app = typer.Typer(add_completion=False)

MARKET_OPTION = typer.Option("nse", "--market", help="nse | crypto | bse")


@app.callback()
def _cli() -> None:
    """See scripts/validate_intraday_setup.py's callback docstring."""


@app.command()
def scan(
    market: str = MARKET_OPTION,
    db: Path | None = typer.Option(None, "--db", help="default: this market's own store"),
    root: Path = typer.Option(Path("."), "--root"),
    on: str = typer.Option("", "--on", help="scan date (default: newest stored session)"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    log: Path | None = typer.Option(None, "--log", help="default: this market's own log"),
) -> None:
    """Log every candidate's calls for one session. Safe to re-run: call ids are
    (rule, code, date), so an existing call is never duplicated or overwritten."""
    from datetime import date as date_cls

    settings = load_config(root)
    db = db or db_for(market)
    log = log or log_path_for(market)
    want = date_cls.fromisoformat(on) if on else None
    scan_date, new_count, fired = scan_universe(
        db, settings, market=market, on=want, max_codes=max_codes, log=log, echo=typer.echo
    )
    if scan_date is None:
        typer.echo("no session found to scan")
        return
    typer.echo(f"\n{scan_date}: {new_count} new calls logged ({len(load_log(log))} total)")
    for rule, count in fired.items():
        typer.echo(f"  {rule}: {count} fired")


@app.command()
def resolve(
    market: str = MARKET_OPTION,
    db: Path | None = typer.Option(None, "--db"),
    log: Path | None = typer.Option(None, "--log"),
) -> None:
    """Turn every ripe unresolved call into a real graded trade."""
    db = db or db_for(market)
    log = log or log_path_for(market)
    done, still_open = resolve_calls(db, log)
    if done == 0 and still_open == 0:
        typer.echo("nothing to resolve")
    else:
        typer.echo(f"resolved {done} calls ({still_open} still open)")


@app.command()
def report(
    market: str = MARKET_OPTION,
    log: Path | None = typer.Option(None, "--log"),
    cost_r: float | None = typer.Option(None, "--cost-r"),
    sessions: Path | None = typer.Option(None, "--sessions"),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    log = log or log_path_for(market)
    cost_r = cost_r if cost_r is not None else cost_r_for(market)
    sessions = sessions or sessions_dir_for(market)
    rows = load_log(log)
    text = build_report(rows, cost_r, market=market)
    typer.echo(text)
    if save:
        p = save_session_report(text, sessions)
        typer.echo(f"\nsaved -> {p}")


@app.command()
def run(
    market: str = MARKET_OPTION,
    db: Path | None = typer.Option(None, "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    max_codes: int = typer.Option(0, "--max-codes"),
    log: Path | None = typer.Option(None, "--log"),
) -> None:
    """The nightly job: grade what's ripe, log tonight's calls, flag anything that has
    cleared the evidence bar, write the report."""
    db = db or db_for(market)
    log = log or log_path_for(market)
    resolve(market=market, db=db, log=log)
    scan(market=market, db=db, root=root, on="", max_codes=max_codes, log=log)
    flagged = flag_research_findings(load_log(log), cost_r_for(market), market=market)
    if flagged:
        typer.echo(f"\nflagged for review: {flagged}")
    report(market=market, log=log, cost_r=cost_r_for(market), sessions=sessions_dir_for(market), save=True)  # noqa: E501


if __name__ == "__main__":
    app()
