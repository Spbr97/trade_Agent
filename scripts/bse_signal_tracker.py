"""BSE signal tracker: the same "log every call, grade it later" workaround
scripts/crypto_signal_tracker.py uses, for the same reason - BSE has no paper book either
(paper/book.py stays NSE-only). Unlike crypto, BSE shares NSE's broker (INDstocks), cost
structure and cash-market hours - see markets/market.py::bse_market - so this script is
mostly just "point the same machinery at a different exchange prefix and watchlist."

The generic log/resolve/render/flag machinery lives in tradedesk/signal_tracker.py.

Run daily after the BSE close (mirrors tradedesk-after-close's NSE timing - 16:00 IST,
after the 15:30 close), AFTER `tradedesk scan --market bse` (so today's watchlist file
exists at data/watchlists/bse/).

Rewritten 2026-09-27 (explicit request: BSE should track its full universe, "not just 5/10
stocks", same as NSE): this used to hardcode a 5-stock WATCHLIST and rebuild it itself via a
second, redundant full history refresh + build_watchlist() call - a second, separate universe
from the one the full-universe `tradedesk scan --market bse` evening scan and
`tradedesk live --market bse` live session already use, and a second full-universe candle
refresh (thousands of INDstocks calls) on top of whatever `data load --market bse` already
did that same run. Now mirrors scripts/nse_signal_tracker.py exactly: it does NOT rebuild a
watchlist from scratch - it just loads the newest saved data/watchlists/bse/<date>.json (same
"newest saved watchlist" discovery `tradedesk live --market bse` uses) rather than
re-scanning or re-fetching anything, so every candidate the full-universe evening scan
evaluated - not a hardcoded handful - gets logged and resolved forward.

Log: data/reports/bse_signal_tracking.jsonl. Starts from an EMPTY log (forward-only, like
NSE's) - the old 5-stock history in that log predates this rewrite and stays as-is; nothing
here re-tags or discards it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradedesk.data.candle_store import CandleStore
from tradedesk.scan import load_watchlist
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
WATCHLIST_DIR = Path("data/watchlists/bse")
MAX_HOLD = 10  # sessions; matches config/risk.yaml's default


def main(*, strict_accuracy_refresh: bool = False) -> None:
    candidates = sorted(WATCHLIST_DIR.glob("*.json"))
    if not candidates:
        detail = (
            f"no watchlist found in {WATCHLIST_DIR}; "
            "run `tradedesk scan --market bse` first; aborting"
        )
        print(detail)
        if strict_accuracy_refresh:
            raise RuntimeError(detail)
        return
    watchlist_path = candidates[-1]
    wl = load_watchlist(watchlist_path)
    day: date = wl.on

    with CandleStore(DB) as store:
        rows = load_log(LOG)
        new_rows = log_new_signals(wl, rows, market="bse")
        newly_resolved = resolve_outcomes(store, rows, MAX_HOLD)
        save_log(rows, LOG)
        save_dashboard("bse", rows, DASHBOARD)
        report = render_session_report("BSE", day, new_rows, newly_resolved, rows)
        report_path = save_session_report(day, report, SESSIONS_DIR)
        flagged = flag_setup_failures("bse", rows)
        print(
            f"{day}: {len(wl.entries)} candidates evaluated "
            f"({len(wl.active)} would be tradeable, {len(new_rows)} new today from "
            f"{watchlist_path.name}), {len(newly_resolved)} newly resolved"
        )
        print(scoreboard(rows))
        print(f"session report: {report_path}")
        print(f"dashboard: {DASHBOARD}")
        if flagged:
            print(f"flagged for review: {', '.join(flagged)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-accuracy-refresh", action="store_true")
    arguments = parser.parse_args()
    main(strict_accuracy_refresh=arguments.strict_accuracy_refresh)
