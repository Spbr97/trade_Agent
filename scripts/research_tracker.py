"""CLI over tradedesk.research_tracker (the shared module - see its docstring for the full
design rationale). Kept thin on purpose: dashboard/app.py's /api/research/* endpoints import
the same shared functions directly, so the CLI report and the dashboard's Research tab can
never disagree about a candidate's numbers.

Commands: `run` (nightly: resolve, then scan, then flag, then report), `report`, `scan`,
`resolve`.
"""

from __future__ import annotations

from pathlib import Path

import typer

from tradedesk.config import load_config
from tradedesk.research_tracker import (
    DEFAULT_COST_R,
    LOG,
    SESSIONS,
    build_report,
    flag_research_findings,
    load_log,
    resolve_calls,
    save_session_report,
    scan_universe,
)

app = typer.Typer(add_completion=False)


@app.callback()
def _cli() -> None:
    """See scripts/validate_intraday_setup.py's callback docstring."""


@app.command()
def scan(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    on: str = typer.Option("", "--on", help="scan date (default: newest stored session)"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    log: Path = typer.Option(LOG, "--log"),
) -> None:
    """Log every candidate's calls for one session. Safe to re-run: call ids are
    (rule, code, date), so an existing call is never duplicated or overwritten."""
    from datetime import date as date_cls

    settings = load_config(root)
    want = date_cls.fromisoformat(on) if on else None
    scan_date, new_count, fired = scan_universe(
        db, settings, on=want, max_codes=max_codes, log=log, echo=typer.echo
    )
    if scan_date is None:
        typer.echo("no session found to scan")
        return
    typer.echo(f"\n{scan_date}: {new_count} new calls logged ({len(load_log(log))} total)")
    for rule, count in fired.items():
        typer.echo(f"  {rule}: {count} fired")


@app.command()
def resolve(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    log: Path = typer.Option(LOG, "--log"),
) -> None:
    """Turn every ripe unresolved call into a real graded trade."""
    done, still_open = resolve_calls(db, log)
    if done == 0 and still_open == 0:
        typer.echo("nothing to resolve")
    else:
        typer.echo(f"resolved {done} calls ({still_open} still open)")


@app.command()
def report(
    log: Path = typer.Option(LOG, "--log"),
    cost_r: float = typer.Option(DEFAULT_COST_R, "--cost-r"),
    sessions: Path = typer.Option(SESSIONS, "--sessions"),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    rows = load_log(log)
    text = build_report(rows, cost_r)
    typer.echo(text)
    if save:
        p = save_session_report(text, sessions)
        typer.echo(f"\nsaved -> {p}")


@app.command()
def run(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    max_codes: int = typer.Option(0, "--max-codes"),
    log: Path = typer.Option(LOG, "--log"),
) -> None:
    """The nightly job: grade what's ripe, log tonight's calls, flag anything that has
    cleared the evidence bar, write the report."""
    resolve(db=db, log=log)
    scan(db=db, root=root, on="", max_codes=max_codes, log=log)
    flagged = flag_research_findings(load_log(log))
    if flagged:
        typer.echo(f"\nflagged for review: {flagged}")
    report(log=log, cost_r=DEFAULT_COST_R, sessions=SESSIONS, save=True)


if __name__ == "__main__":
    app()
