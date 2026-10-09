"""Full-active-universe crypto signal tracker.

Crypto trades 24/7, but paper/book.py::PaperBook is deliberately NSE-only
(CLAUDE.md M13 note: Portfolio.costs would need CryptoCostModel wiring through the paper
book's sizing too, and risk/sizing.py's whole-unit assumption already breaks on BTC/ETH -
see docs/signoff-crypto-phase4.md). Rather than force crypto through that, this is a
lighter, honest tool: discover every currently active CoinDCX INR pair, scan the whole
universe, then log and grade every real signal with the SAME triple-barrier rule the
prediction layer trains on (prediction/labeling.py) - hit target before stop, or not,
gap-aware. It answers "was the setup right" without needing position sizing, a cost
model wired into a portfolio, or fractional quantities at all.

The generic log/resolve/render machinery lives in tradedesk/signal_tracker.py, shared with
scripts/bse_signal_tracker.py (same underlying problem: no paper book). This script supplies
only what's crypto-specific: which client refreshes candles and the dynamic universe.

Run daily (scheduled via Task Scheduler, crypto_daily task) - crypto's daily candle is a
UTC-midnight bar, settled well before this runs at 07:00 IST.

Log: data/reports/crypto_signal_tracking.jsonl, one row per signal, updated in place as
outcomes resolve (rewrite-the-file style, small enough not to need anything fancier).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradedesk.backtest.runner import prepare_market
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.history_loader import default_start, load_history
from tradedesk.markets import crypto_market
from tradedesk.markets.crypto_universe import (
    active_crypto_codes,
    crypto_codes_needing_daily_refresh,
    stored_crypto_codes,
)
from tradedesk.scan import build_watchlist, scan_config
from tradedesk.self_learning_workflow import run_scheduled_challenger
from tradedesk.signal_tracker import (
    flag_setup_failures,
    load_log,
    log_exit_contract_signals,
    render_session_report,
    resolve_outcomes,
    save_dashboard,
    save_log,
    save_session_report,
    scoreboard,
    setup_hit_rate,  # noqa: F401  (re-exported for anything importing it from here still)
)

log_new_signals = log_exit_contract_signals
refresh_learning_status = run_scheduled_challenger

DB = Path("data/crypto.duckdb")
LOG = Path("data/reports/crypto_signal_tracking.jsonl")
SESSIONS_DIR = Path("data/reports/crypto_sessions")
DASHBOARD = Path("data/reports/crypto_dashboard.html")
UNIVERSE_REPORT = Path("data/reports/crypto_universe_latest.json")
MAX_HOLD = 10  # sessions -> calendar days for a 24/7 market; matches config/risk.yaml


def main(*, strict_accuracy_refresh: bool = False) -> None:
    accuracy_failures: list[str] = []
    settings = load_config(".")
    market = crypto_market(settings)
    with CandleStore(DB) as store:
        # Refresh the public instrument master on every run. This is one lightweight
        # request and means new/delisted pairs change the monitored population explicitly
        # instead of leaving a hard-coded ten-coin sample in place forever.
        from tradedesk.broker.coindcx import CoinDcxClient
        from tradedesk.broker.coindcx.rest import CoinDcxError

        now = datetime.now(IST)
        start = default_start(Interval.D1, now)

        async def refresh() -> tuple[list[str], int, int]:
            async with CoinDcxClient() as c:
                instruments = await c.instruments()
                universe = active_crypto_codes(instruments)
                if not universe:
                    raise RuntimeError("CoinDCX returned no active INR pairs")
                store.upsert_instruments(instruments)
                c.register_pairs(
                    {
                        instrument.scrip_code: str(instrument.custom_symbol)
                        for instrument in instruments
                        if instrument.custom_symbol
                    }
                )
                refresh_codes = crypto_codes_needing_daily_refresh(store, universe, now)
                summary = await load_history(
                    c, store, refresh_codes, Interval.D1, start=start, end=now,
                    max_codes_per_call=1, error_types=(CoinDcxError,),
                )  # fmt: skip
                return universe, len(refresh_codes), len(summary.errors)

        universe, refreshed_pairs, fetch_errors = asyncio.run(refresh())
        universe_event: dict | None = None
        universe_history_error: str | None = None
        known_inactive = sorted(set(stored_crypto_codes(store)) - set(universe))
        try:
            from tradedesk_lab.crypto_accuracy_dataset import record_universe_observation

            universe_event = record_universe_observation(
                active_codes=universe,
                inactive_codes=known_inactive,
                observed_at=datetime.now(IST),
            )
        except Exception as exc:
            universe_history_error = f"{type(exc).__name__}: {exc}"
        reference = f"{market.code_prefix}{market.benchmark_name}"
        if reference not in universe:
            raise RuntimeError(f"crypto benchmark {reference} is not active")

        # last_closed_ts, not last_ts: CoinDCX continuously updates the currently-forming
        # UTC day's bar rather than only publishing it once closed (confirmed 2026-09-27 -
        # see CandleStore.last_closed_ts's own docstring), so evaluating against last_ts()
        # here would arm signals off a not-yet-final close.
        today = store.last_closed_ts(reference, Interval.D1)
        if today is None:
            detail = "no fully-closed bar for the watchlist yet; aborting"
            print(detail)
            if strict_accuracy_refresh:
                raise RuntimeError(detail)
            return
        day = today.date()

        cfg = scan_config(settings, day, market=market, include_retired=True)
        # BTC is both the market reference and a tradeable pair. Keep it in the candidate
        # list as the original ten-coin tracker did; using it as a benchmark must not make
        # "all active pairs" silently mean "all except BTC".
        scan_codes = list(universe)
        md = prepare_market(store, scan_codes, reference, cfg)
        wl = build_watchlist(md, cfg, settings, day, market=market)

        closed_on_day = sum(
            1
            for code in universe
            if (closed := store.last_closed_ts(code, Interval.D1)) is not None
            and closed.date() == day
        )
        UNIVERSE_REPORT.parent.mkdir(parents=True, exist_ok=True)
        UNIVERSE_REPORT.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(IST).isoformat(),
                    "session": day.isoformat(),
                    "active_inr_pairs": len(universe),
                    "active_codes": universe,
                    "known_inactive_codes": known_inactive,
                    "universe_event_sha256": (
                        universe_event["event_sha256"] if universe_event else None
                    ),
                    "universe_history_error": universe_history_error,
                    "scanned_pairs": len(scan_codes),
                    "pairs_with_closed_session": closed_on_day,
                    "configured_exclusions": sorted(market.universe_rules.exclude_codes),
                    "tracker_refreshed_pairs": refreshed_pairs,
                    "fetch_errors": fetch_errors,
                    "signals_detected": len(wl.entries),
                    "tradeable_signals": len(wl.active),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        rows = load_log(LOG)
        new_rows = log_new_signals(wl, rows, market="crypto")
        newly_resolved = resolve_outcomes(store, rows, MAX_HOLD)
        save_log(rows, LOG)
        refresh_learning_status(
            "crypto",
            rows,
            Path("data/reports/crypto_self_learning_status.json"),
            candle_store=store,
        )
        save_dashboard("crypto", rows, DASHBOARD)
        run_at = datetime.now(IST).strftime("%H:%M IST")
        report = render_session_report("Crypto", day, new_rows, newly_resolved, rows, run_at=run_at)  # noqa: E501
        report_path = save_session_report(day, report, SESSIONS_DIR, append=True)
        flagged = flag_setup_failures("crypto", rows)
        print(
            f"{day} run at {run_at}: monitored {len(universe)} active INR pairs "
            f"({closed_on_day} with the closed session, {refreshed_pairs} needed refresh, "
            f"{fetch_errors} fetch errors); "
            f"{len(wl.entries)} signals detected "
            f"({len(wl.active)} would be tradeable, {len(new_rows)} new contract rows), "
            f"{len(newly_resolved)} newly resolved"
        )
        print(scoreboard(rows))
        print(f"session report: {report_path}")
        print(f"dashboard: {DASHBOARD}")
        if flagged:
            print(f"flagged for review: {', '.join(flagged)}")
        if universe_history_error:
            print(f"Crypto universe history degraded: {universe_history_error}")
            accuracy_failures.append(f"universe history: {universe_history_error}")
        if fetch_errors:
            accuracy_failures.append(f"daily candle refresh errors: {fetch_errors}")
        if closed_on_day != len(universe):
            accuracy_failures.append(
                f"closed-session coverage: {closed_on_day}/{len(universe)}"
            )

    try:
        from tradedesk.leader_discovery import run_missed_leader_audit

        leader_audit = run_missed_leader_audit(
            market="crypto", db_path=DB, tracker_path=LOG
        )
        leader_latest = leader_audit["latest_mature_audit"]
        print(
            "M14 missed-leader audit: "
            f"session={leader_latest['session']}, "
            f"leaders={leader_latest['leader_count']}, "
            f"candidate_recall={leader_latest['candidate_recall']}, "
            f"status={leader_audit['status']} (diagnostic only)"
        )
    except Exception as exc:
        print(f"M14 missed-leader audit unavailable — not a pass: {type(exc).__name__}: {exc}")
        accuracy_failures.append(f"M14: {type(exc).__name__}: {exc}")

    # Independent forward-only accuracy control.  It reads the saved crypto log and
    # candles after the established tracker closes them; failures cannot stop tracking.
    try:
        from tradedesk_lab.crypto_accuracy_timing import collect_crypto_timing

        timing = collect_crypto_timing()
        summary = timing["summary"]
        print(
            "Crypto same-coin timing control: "
            f"{summary['registered_calls']} forward calls across "
            f"{summary['setups_monitored']} setups, status={summary['status']}"
        )
    except Exception as exc:
        print(f"Crypto timing control degraded: {type(exc).__name__}: {exc}")
        accuracy_failures.append(f"timing: {type(exc).__name__}: {exc}")


    try:
        from tradedesk_lab.crypto_accuracy_pipeline import refresh_crypto_accuracy

        accuracy = refresh_crypto_accuracy()
        print(
            "Crypto accuracy evidence refresh: "
            f"C1 status={accuracy['c1']['status']}, "
            f"C2 status={accuracy['c2']['status']}"
        )
        if str(accuracy["c2"].get("status", "")).startswith("blocked"):
            accuracy_failures.append(
                f"C2 status: {accuracy['c2'].get('status')}"
            )
    except Exception as exc:
        # The point-in-time collection above is already durable. Keep the
        # tracker successful and let the next run retry these derived outputs.
        print(f"Crypto accuracy evidence degraded: {type(exc).__name__}: {exc}")
        accuracy_failures.append(f"C1/C2: {type(exc).__name__}: {exc}")

    if strict_accuracy_refresh and accuracy_failures:
        raise RuntimeError("; ".join(accuracy_failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-accuracy-refresh", action="store_true")
    arguments = parser.parse_args()
    main(strict_accuracy_refresh=arguments.strict_accuracy_refresh)
