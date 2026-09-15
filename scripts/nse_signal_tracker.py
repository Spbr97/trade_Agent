"""NSE signal tracker: the same "log every call, grade it later via triple-barrier"
machinery crypto/BSE already use (tradedesk/signal_tracker.py), extended to NSE for the
first time (2026-09-15 request: "is [a potential call] actually hitting the expected value
... is the agent dissecting how come it got evaluated as a potential call ... and why it
couldn't hold the ground").

Why NSE never had this: paper/book.py::PaperBook already exists for NSE and was assumed to
cover "does the agent's track record get measured" - but PaperBook only ever simulates a
signal AFTER it triggers live. Since the eligibility gate (engine/scoring.py) currently
blocks every setup from ever triggering, the paper book has stayed permanently empty and
NOTHING has tracked what happens to the ~100+ candidates evaluated and rejected each day.
This closes that gap using the exact log_new_signals(wl.entries)-not-just-wl.active pattern
crypto/BSE already validated - every evaluated candidate gets logged and later resolved,
tradeable or not, so "would this potential call have hit its target" is answered for real
instead of the call just vanishing after one day's watchlist.

Unlike crypto/BSE's scripts, this does NOT rebuild a watchlist from scratch - NSE's own
`tradedesk-after-close` already runs a full-universe `scan` daily and saves
data/watchlists/<date>.json; this just loads that file (same "newest saved watchlist"
discovery `tradedesk live` uses) rather than re-scanning ~2,600 codes a second time.

Run as a step of tradedesk-after-close, AFTER `scan` (so today's watchlist file exists).
Log: data/reports/nse_signal_tracking.jsonl. Starts from an EMPTY log (forward-only) -
see the separate question of whether to backfill a historical track record the way
crypto/BSE's logs were, flagged rather than done automatically here since it would replay
the SAME 2023-2026 window entry_search.py/null_baseline.py already exhaustively mined and
closed to further testing (CLAUDE.md: "the setups themselves are the problem, and that
question is now closed") - a backfill here would still be useful for DISSECTING which
score/grade/probability values misled on which candidates, but would not be new evidence of
an edge, and is a real compute cost (~2,600 codes x ~750 sessions) worth a deliberate choice.
"""

from __future__ import annotations

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

DB = Path("data/tradedesk.duckdb")
LOG = Path("data/reports/nse_signal_tracking.jsonl")
SESSIONS_DIR = Path("data/reports/nse_sessions")
DASHBOARD = Path("data/reports/nse_dashboard.html")
WATCHLIST_DIR = Path("data/watchlists")
MAX_HOLD = 10  # sessions; matches config/risk.yaml's default


def main() -> None:
    candidates = sorted(WATCHLIST_DIR.glob("*.json"))
    if not candidates:
        print(f"no watchlist found in {WATCHLIST_DIR}; run `tradedesk scan` first; aborting")
        return
    watchlist_path = candidates[-1]
    wl = load_watchlist(watchlist_path)
    day: date = wl.on

    with CandleStore(DB) as store:
        rows = load_log(LOG)
        new_rows = log_new_signals(wl, rows)
        newly_resolved = resolve_outcomes(store, rows, MAX_HOLD)
        save_log(rows, LOG)
        save_dashboard("nse", rows, DASHBOARD)
        report = render_session_report("NSE", day, new_rows, newly_resolved, rows)
        report_path = save_session_report(day, report, SESSIONS_DIR)
        flagged = flag_setup_failures("nse", rows)
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
    main()
