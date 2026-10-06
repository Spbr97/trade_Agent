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

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk.data.candle_store import CandleStore  # noqa: E402
from tradedesk.scan import load_watchlist  # noqa: E402
from tradedesk.self_learning_workflow import run_scheduled_challenger  # noqa: E402
from tradedesk.signal_tracker import (  # noqa: E402
    flag_setup_failures,
    load_log,
    log_exit_contract_signals,
    render_session_report,
    resolve_outcomes,
    save_dashboard,
    save_log,
    save_session_report,
    scoreboard,
)

# Preserve the script's long-standing integration-test patch seam; this alias now logs
# both versioned exit contracts.
log_new_signals = log_exit_contract_signals
refresh_learning_status = run_scheduled_challenger

DB = Path("data/tradedesk.duckdb")
LOG = Path("data/reports/nse_signal_tracking.jsonl")
SESSIONS_DIR = Path("data/reports/nse_sessions")
DASHBOARD = Path("data/reports/nse_dashboard.html")
WATCHLIST_DIR = Path("data/watchlists")
MAX_HOLD = 10  # sessions; matches config/risk.yaml's default


def main(*, strict_accuracy_refresh: bool = False) -> None:
    accuracy_failures: list[str] = []
    candidates = sorted(WATCHLIST_DIR.glob("*.json"))
    if not candidates:
        detail = f"no watchlist found in {WATCHLIST_DIR}; run `tradedesk scan` first; aborting"
        print(detail)
        if strict_accuracy_refresh:
            raise RuntimeError(detail)
        return
    watchlist_path = candidates[-1]
    wl = load_watchlist(watchlist_path)
    day: date = wl.on

    with CandleStore(DB) as store:
        rows = load_log(LOG)
        new_rows = log_new_signals(wl, rows, market="nse")
        newly_resolved = resolve_outcomes(store, rows, MAX_HOLD)
        save_log(rows, LOG)
        refresh_learning_status(
            "nse", rows, Path("data/reports/nse_self_learning_status.json")
        )
        save_dashboard("nse", rows, DASHBOARD)
        report = render_session_report("NSE", day, new_rows, newly_resolved, rows)
        report_path = save_session_report(day, report, SESSIONS_DIR)
        flagged = flag_setup_failures("nse", rows)
        print(
            f"{day}: {len(wl.entries)} candidates evaluated "
            f"({len(wl.active)} would be tradeable, {len(new_rows)} new contract rows from "
            f"{watchlist_path.name}), {len(newly_resolved)} newly resolved"
        )
        print(scoreboard(rows))
        print(f"session report: {report_path}")
        print(f"dashboard: {DASHBOARD}")
        if flagged:
            print(f"flagged for review: {', '.join(flagged)}")

    # M8 is an additive research observer.  It reads the saved watchlist and candles only;
    # any collector failure is reported but cannot take down the established NSE tracker.
    try:
        from tradedesk_lab.accuracy_prospective_shadow import collect

        shadow = collect()
        summary = shadow["summary"]
        print(
            "M8 prospective shadow: "
            f"{summary['selected_calls']} selected, {summary['resolved_calls']} resolved, "
            f"status={summary['status']}"
        )
        if shadow.get("current_errors"):
            accuracy_failures.append(
                f"M8 current errors: {len(shadow['current_errors'])}"
            )
    except FileNotFoundError:
        print("M8 prospective shadow: not activated")
        accuracy_failures.append("M8 prospective shadow is not activated")
    except Exception as exc:
        print(f"M8 prospective shadow degraded: {type(exc).__name__}: {exc}")
        accuracy_failures.append(f"M8: {type(exc).__name__}: {exc}")
    else:
        try:
            from tradedesk_lab.accuracy_prospective_monitor import run_monitor

            monitor = run_monitor()
            print(
                "M9 evidence monitor: "
                f"integrity={monitor['integrity']['status']}, "
                f"audit_events={monitor['audit']['events']}, "
                f"review_ready={monitor['review_ready']}"
            )
            if monitor["integrity"].get("passed") is not True:
                accuracy_failures.append(
                    "M9 integrity: "
                    + ", ".join(monitor["integrity"].get("failures") or ["failed"])
                )
            if (monitor.get("double_slippage_stress") or {}).get("status") == "degraded":
                accuracy_failures.append("M9 double-slippage stress degraded")
        except Exception as exc:
            print(f"M9 evidence monitor degraded: {type(exc).__name__}: {exc}")
            accuracy_failures.append(f"M9: {type(exc).__name__}: {exc}")
        try:
            from tradedesk_lab.accuracy_prospective_control import collect_control

            control = collect_control()
            control_summary = control["summary"]
            print(
                "M10 matched-random selection control: "
                f"{control_summary['resolved_model_calls']} paired model calls, "
                f"{control_summary['mature_sessions']} mature sessions, "
                f"status={control_summary['status']}"
            )
            if control_summary.get("status") == "degraded":
                accuracy_failures.append("M10 selection control degraded")
        except Exception as exc:
            print(f"M10 selection control degraded: {type(exc).__name__}: {exc}")
            accuracy_failures.append(f"M10: {type(exc).__name__}: {exc}")
        try:
            from tradedesk_lab.accuracy_prospective_timing import collect_timing

            timing = collect_timing()
            timing_summary = timing["summary"]
            print(
                "M11 same-stock random-timing control: "
                f"{timing_summary['paired_resolved_calls']} paired calls, "
                f"{timing_summary['active_sessions']} active sessions, "
                f"status={timing_summary['status']}"
            )
            if timing_summary.get("status") == "degraded":
                accuracy_failures.append("M11 random-timing control degraded")
        except Exception as exc:
            print(f"M11 random-timing control degraded: {type(exc).__name__}: {exc}")
            accuracy_failures.append(f"M11: {type(exc).__name__}: {exc}")
        try:
            from tradedesk_lab.accuracy_prospective_qualification import run_qualification

            qualification = run_qualification()
            print(
                "NSE prospective qualification: "
                f"status={qualification['status']}, "
                f"review_authorized={qualification['review_authorized']}, "
                f"live_eligible={qualification['eligible_for_live']}"
            )
            if qualification.get("status") in {"not_available", "degraded"}:
                accuracy_failures.append(
                    f"qualification status: {qualification.get('status')}"
                )
        except Exception as exc:
            print(f"NSE prospective qualification degraded: {type(exc).__name__}: {exc}")
            accuracy_failures.append(f"qualification: {type(exc).__name__}: {exc}")
    if strict_accuracy_refresh and accuracy_failures:
        raise RuntimeError("; ".join(accuracy_failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-accuracy-refresh", action="store_true")
    arguments = parser.parse_args()
    main(strict_accuracy_refresh=arguments.strict_accuracy_refresh)
