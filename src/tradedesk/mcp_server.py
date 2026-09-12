"""MCP server (PLAN.md 9): lets Claude Code ask the running system questions over stdio -
"why is XYZ on the list?", "how is the paper book doing?", "backtest base breakouts since
2024". Read-only: every tool returns JSON built from the store, the journal and the same
engine code the scanner runs. Nothing here can place orders or change a level.

Register in Claude Code via .mcp.json (see repo root) or run `tradedesk mcp`.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from tradedesk.config import load_config

ROOT = Path(__file__).resolve().parents[2]
DB = Path("data/tradedesk.duckdb")
JOURNAL = Path("data/journal.sqlite")
WATCHLISTS = Path("data/watchlists")

server = MCPServer(
    "tradedesk",
    instructions=(
        "Read-only view of the tradedesk swing scanner: watchlists, positions, paper book "
        "stats, ad-hoc analysis and backtests. It never places orders."
    ),
)


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


@server.tool()
def get_watchlist(on: str | None = None) -> str:
    """The saved evening watchlist for a date (YYYY-MM-DD; default the newest), with each
    entry's grade, score, trigger/stop/targets, sizing, costs and rejection reasons."""
    files = sorted(WATCHLISTS.glob("*.json"))
    if on:
        files = [f for f in files if f.stem == on]
    if not files:
        return _dump({"error": "no watchlist found", "dir": str(WATCHLISTS)})
    from tradedesk.scan import load_watchlist

    wl = load_watchlist(files[-1])
    return _dump(
        {
            "on": wl.on,
            "regime": wl.regime.model_dump(mode="json") if wl.regime else None,
            "entries": [
                {
                    "symbol": e.signal.symbol,
                    "code": e.signal.scrip_code,
                    "setup": e.signal.setup.value,
                    "grade": e.grade.value,
                    "score": e.score,
                    "components": e.score_components,
                    "notes": e.score_notes,
                    "trigger": e.signal.trigger,
                    "stop": e.signal.stop,
                    "t1": e.signal.t1,
                    "t2": e.signal.t2,
                    "qty": e.qty,
                    "risk_pct": e.risk_pct,
                    "net_rr_t2": e.net_rr_t2,
                    "results_in_sessions": e.results_in_sessions,
                    "reasons": e.signal.reasons,
                    "rejected_for": e.rejected_for,
                }
                for e in wl.entries
            ],
        }
    )


@server.tool()
def get_position(code: str) -> str:
    """Open live/paper position for a scrip code (e.g. NSE_3045) with stop, R and sessions held."""
    from tradedesk.journal import Journal

    with Journal(JOURNAL) as jn:
        out = {}
        for source in ("live", "paper"):
            for p in jn.open_positions(source=source):
                if p.signal.scrip_code == code:
                    out[source] = {
                        "symbol": p.signal.symbol,
                        "entry_date": p.entry_date,
                        "entry": p.entry_price,
                        "qty_open": p.qty_open,
                        "stop": p.stop,
                        "partial_done": p.partial_done,
                        "sessions_held": p.sessions_held,
                        "t1": p.signal.t1,
                        "t2": p.signal.t2,
                    }
    return _dump(out or {"info": f"no open position in {code}"})


@server.tool()
def analyze(code: str, on: str | None = None, market: str = "nse") -> str:
    """Run the setups and indicators on one scrip as of a date (default: latest stored bar):
    trend, RS, ATR, patterns found and any signal that would arm, with its score, its
    recommended hold (sessions), and that setup's historical hit rate. `market` is "nse"
    (default) or "crypto"; crypto scrip codes look like CDX_BTCINR."""
    from tradedesk.analysis import analyze_symbol

    settings = load_config(ROOT)
    return _dump(analyze_symbol(settings, market, code, on))


@server.tool()
def journal_stats() -> str:
    """Paper vs live expectancy, per-setup track records (auto-bench), rule adherence."""
    from tradedesk.journal import Journal
    from tradedesk.journal.stats import summary

    with Journal(JOURNAL) as jn:
        return _dump(summary(jn))


@server.tool()
def backtest(
    setup: str = "base_breakout", start: str = "2023-01-01", end: str | None = None
) -> str:
    """Run the event-driven backtest for one setup over stored history; returns the
    per-setup report (expectancy R, profit factor, drawdown, gap damage, exit reasons)."""
    from tradedesk.backtest import BacktestConfig, build_report, prepare_market, run_backtest
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.data.candle_store import CandleStore
    from tradedesk.data.universe import UniverseRules
    from tradedesk.engine.signals import SetupKind

    settings = load_config(ROOT)
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end) if end else datetime.now(IST).date()
    cfg = BacktestConfig(
        setups=[SetupKind(setup)],
        start=start_d,
        end=end_d,
        capital=float(settings.risk.trading_capital),
        risk=settings.risk,
        engine=settings.engine,
        setup_params={k: v.model_dump() for k, v in settings.setups.setups.items()},
        universe_rules=UniverseRules(
            min_avg_turnover_inr=float(settings.universe.min_avg_daily_turnover_inr),
            min_price=float(settings.universe.min_price),
        ),
        slippage_pct=float(settings.risk.costs.slippage_pct),
    )
    with CandleStore(DB) as store:
        ref = store.index_code(settings.universe.benchmark)
        if ref is None:
            return _dump({"error": "benchmark not in instruments table"})
        codes = [c for c in store.codes(Interval.D1) if c != ref]
        md = prepare_market(store, codes, ref, cfg)
    rep = build_report(run_backtest(md, cfg))
    return _dump(
        {
            "setup": setup,
            "start": start_d,
            "end": end_d,
            "overall": rep.overall.to_row(),
            "max_drawdown_pct": rep.max_drawdown_pct,
            "signal_states": rep.signal_states,
            "exit_reasons": rep.overall.exit_reasons,
            "caveat": "universe built from candles on hand: survivorship bias, treat as optimistic",
        }
    )


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
