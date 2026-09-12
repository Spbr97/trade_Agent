"""Crypto signal tracker (M13 follow-up): "test a few coins and track to see if you're
right" - crypto trades 24/7, but paper/book.py::PaperBook is deliberately NSE-only
(CLAUDE.md M13 note: Portfolio.costs would need CryptoCostModel wiring through the paper
book's sizing too, and risk/sizing.py's whole-unit assumption already breaks on BTC/ETH -
see docs/signoff-crypto-phase4.md). Rather than force crypto through that, this is a
lighter, honest tool: log every real signal the crypto scan produces on a fixed watchlist
of liquid pairs, then later grade each one with the SAME triple-barrier rule the
prediction layer trains on (prediction/labeling.py) - hit target before stop, or not,
gap-aware. It answers "was the setup right" without needing position sizing, a cost
model wired into a portfolio, or fractional quantities at all.

The generic log/resolve/render machinery lives in tradedesk/signal_tracker.py, shared with
scripts/bse_signal_tracker.py (same underlying problem: no paper book). This script supplies
only what's crypto-specific: which client refreshes candles, and the watchlist.

Run daily (scheduled via Task Scheduler, crypto_daily task) - crypto's daily candle is a
UTC-midnight bar, settled well before this runs at 07:00 IST.

Log: data/reports/crypto_signal_tracking.jsonl, one row per signal, updated in place as
outcomes resolve (rewrite-the-file style, small enough not to need anything fancier).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradedesk.backtest.runner import prepare_market
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.history_loader import default_start, load_history
from tradedesk.markets import crypto_market
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
    setup_hit_rate,  # noqa: F401  (re-exported for anything importing it from here still)
)

DB = Path("data/crypto.duckdb")
LOG = Path("data/reports/crypto_signal_tracking.jsonl")
SESSIONS_DIR = Path("data/reports/crypto_sessions")
DASHBOARD = Path("data/reports/crypto_dashboard.html")
WATCHLIST = [
    "CDX_BTCINR", "CDX_ETHINR", "CDX_SOLINR", "CDX_XRPINR", "CDX_DOGEINR",
    "CDX_ADAINR", "CDX_TRXINR", "CDX_XLMINR", "CDX_HBARINR", "CDX_BNBINR",
]  # fmt: skip
MAX_HOLD = 10  # sessions -> calendar days for a 24/7 market; matches config/risk.yaml


def main() -> None:
    settings = load_config(".")
    market = crypto_market(settings)
    with CandleStore(DB) as store:
        # incremental load, watchlist coins only - keeps this fast and independent of
        # the full 338-pair Task Scheduler load
        from tradedesk.broker.coindcx import CoinDcxClient
        from tradedesk.broker.coindcx.rest import CoinDcxError

        start = default_start(Interval.D1, datetime.now(IST))

        async def refresh() -> None:
            async with CoinDcxClient() as c:
                c.register_pairs(store.custom_symbols(WATCHLIST))
                await load_history(
                    c, store, WATCHLIST, Interval.D1, start=start,
                    max_codes_per_call=1, error_types=(CoinDcxError,),
                )  # fmt: skip

        asyncio.run(refresh())

        today = store.last_ts(WATCHLIST[0], Interval.D1)
        if today is None:
            print("no data for the watchlist; aborting")
            return
        day = today.date()

        cfg = scan_config(settings, day, market=market)
        md = prepare_market(store, WATCHLIST, WATCHLIST[0], cfg)
        wl = build_watchlist(md, cfg, settings, day, market=market)

        rows = load_log(LOG)
        new_rows = log_new_signals(wl, rows)
        newly_resolved = resolve_outcomes(store, rows, MAX_HOLD)
        save_log(rows, LOG)
        save_dashboard("crypto", rows, DASHBOARD)
        run_at = datetime.now(IST).strftime("%H:%M IST")
        report = render_session_report("Crypto", day, new_rows, newly_resolved, rows, run_at=run_at)  # noqa: E501
        report_path = save_session_report(day, report, SESSIONS_DIR, append=True)
        flagged = flag_setup_failures("crypto", rows)
        print(
            f"{day} run at {run_at}: {len(wl.entries)} signals detected "
            f"({len(wl.active)} would be tradeable, {len(new_rows)} new this run), "
            f"{len(newly_resolved)} newly resolved"
        )
        print(scoreboard(rows))
        print(f"session report: {report_path}")
        print(f"dashboard: {DASHBOARD}")
        if flagged:
            print(f"flagged for review: {', '.join(flagged)}")


if __name__ == "__main__":
    main()
