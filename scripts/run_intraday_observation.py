"""Run the step-7 observation session against real backfilled data.

Demonstration scope only - the last few real sessions, not the full 2-year history (that
statistical question was already step 6's job via null_baseline.py). This proves the
end-to-end wiring (data -> MTF -> regime -> scan_bar -> Phase 1 eligibility) runs correctly
on live-shaped data and produces the right answer for a setup that has NOT earned alerting.
"""

from __future__ import annotations

from pathlib import Path

import typer

from tradedesk.broker.indstocks.models import Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import intraday_features
from tradedesk.engine.intraday_signals import IntradaySetupKind
from tradedesk.engine.mtf import HIERARCHY
from tradedesk.live.intraday_observation import SetupEvidence, observe

app = typer.Typer(add_completion=False)


@app.callback()
def _cli() -> None:
    """See scripts/validate_intraday_setup.py's callback docstring - same single-command
    Typer collapse this avoids."""


DEFAULT_CODES = ["NSE_2885", "NSE_1333", "NSE_11536", "NSE_1594", "NSE_3045"]

# The real, measured null-baseline result for VWAP Reclaim (scripts/validate_intraday_setup.py,  # noqa: E501
# 2026-09-13): 1,419 trades, win rate 37.56%, mean gross R -0.0051, random baseline -0.0088,
# p=0.438 (does not beat random). Passed in explicitly rather than recomputed here, so this
# script stays a fast demonstration of the WIRING and does not re-run the expensive sweep.
VWAP_RECLAIM_EVIDENCE = SetupEvidence(
    trades=1419, oos_trades=0, win_rate=0.3756, expectancy_r=-0.0051, random_baseline_r=-0.0088
)


@app.command()
def run(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    codes: list[str] = typer.Argument(None),
    n_sessions: int = typer.Option(3, "--n-sessions", help="most recent sessions to observe"),
) -> None:
    targets = list(codes) if codes else DEFAULT_CODES
    features_by_interval: dict[Interval, dict[str, object]] = {}
    with CandleStore(db) as store:
        for iv in HIERARCHY:
            features_by_interval[iv] = {}
            for code in targets:
                raw = store.load(code, iv)
                if raw is None or raw.empty:
                    continue
                f = intraday_features(raw)
                if iv is Interval.M5 and n_sessions:
                    # trim the arming interval to the last n_sessions - other timeframes
                    # keep full history so align() has real higher-timeframe context.
                    cutoff = f.index.normalize().unique()[-n_sessions:]
                    f = f[f.index.normalize().isin(cutoff)]
                features_by_interval[iv][code] = f

    typer.echo(f"observing {targets} over the last {n_sessions} real session(s)...\n")
    summary = observe(
        features_by_interval,  # type: ignore[arg-type]
        targets,
        Interval.M5,
        [IntradaySetupKind.VWAP_RECLAIM],
        {},
        {IntradaySetupKind.VWAP_RECLAIM: VWAP_RECLAIM_EVIDENCE},
    )
    typer.echo(summary.text())
    typer.echo(
        f"\n{summary.bars_scanned} bar-closes scanned, {len(summary.signals)} armed, "
        f"{summary.would_have_alerted} would have alerted (this script cannot alert)."
    )


if __name__ == "__main__":
    app()
