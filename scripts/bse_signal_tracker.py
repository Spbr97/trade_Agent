"""BSE signal tracker: the same "log every call, grade it later" workaround
scripts/crypto_signal_tracker.py uses, for the same reason - BSE has no paper book either
(paper/book.py stays NSE-only). Unlike crypto, BSE shares NSE's broker (INDstocks), cost
structure and cash-market hours - see markets/market.py::bse_market - so this script is
mostly just "point the same machinery at a different exchange prefix and watchlist."

The generic log/resolve/render/flag machinery lives in tradedesk/signal_tracker.py.

Run daily after the BSE close (mirrors tradedesk-after-close's NSE timing - 16:00 IST,
after the 15:30 close). Log: data/reports/bse_signal_tracking.jsonl.

Starts from an EMPTY log: unlike crypto (which had real history from day one), this only
has calls from the day it first runs onward, so expect "0 resolved yet" for the first
~10 sessions (config/risk.yaml's max_hold_sessions).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from tradedesk.backtest.runner import prepare_market
from tradedesk.broker.indstocks import IndstocksClient, TokenProvider
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.broker.indstocks.rest import BASE_URL
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.history_loader import default_start, load_history
from tradedesk.markets import bse_market
from tradedesk.scan import build_watchlist, scan_config
from tradedesk.signal_tracker import (
    flag_setup_failures,
    load_log,
    log_new_signals,
    render_session_report,
    resolve_outcomes,
    save_dashboard,
    save_log,
    save_session_report,
    scoreboard,
)

DB = Path("data/bse.duckdb")
LOG = Path("data/reports/bse_signal_tracking.jsonl")
SESSIONS_DIR = Path("data/reports/bse_sessions")
DASHBOARD = Path("data/reports/bse_dashboard.html")
# Liquid, well-known Group A large caps (also NSE-dual-listed, chosen for known liquidity
# rather than BSE-exclusive names, which skew thin - see bse_cash_equities()'s docstring).
WATCHLIST = [
    "BSE_500325", "BSE_500180", "BSE_532540", "BSE_500209", "BSE_500112",
]  # RELIANCE, HDFCBANK, TCS, INFY, SBIN  # fmt: skip
MAX_HOLD = 10  # sessions; matches config/risk.yaml's default


def main() -> None:
    settings = load_config(".")
    market = bse_market(settings)
    with CandleStore(DB) as store:
        ref = store.index_code(market.benchmark_name, exch="BSE")
        if ref is None:
            print(f"benchmark {market.benchmark_name!r} not in instruments table; aborting")
            return
        targets = [*WATCHLIST, ref]
        start = default_start(Interval.D1, datetime.now(IST))

        async def refresh() -> None:
            http = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
            client = IndstocksClient(TokenProvider(http=http))
            try:
                await load_history(client, store, targets, Interval.D1, start=start)
            finally:
                await client.aclose()

        asyncio.run(refresh())

        today = store.last_ts(WATCHLIST[0], Interval.D1)
        if today is None:
            print("no data for the watchlist; aborting")
            return
        day = today.date()

        cfg = scan_config(settings, day, market=market)
        codes = [c for c in WATCHLIST if c != ref]
        md = prepare_market(store, codes, ref, cfg)
        wl = build_watchlist(md, cfg, settings, day, market=market)

        rows = load_log(LOG)
        new_rows = log_new_signals(wl, rows)
        newly_resolved = resolve_outcomes(store, rows, MAX_HOLD)
        save_log(rows, LOG)
        save_dashboard("bse", rows, DASHBOARD)
        report = render_session_report("BSE", day, new_rows, newly_resolved, rows)
        report_path = save_session_report(day, report, SESSIONS_DIR)
        flagged = flag_setup_failures("bse", rows)
        print(
            f"{day}: {len(wl.entries)} signals detected "
            f"({len(wl.active)} would be tradeable, {len(new_rows)} new today), "
            f"{len(newly_resolved)} newly resolved"
        )
        print(scoreboard(rows))
        print(f"session report: {report_path}")
        print(f"dashboard: {DASHBOARD}")
        if flagged:
            print(f"flagged for review: {', '.join(flagged)}")


if __name__ == "__main__":
    main()
