"""One-time (or occasional) backfill: build a real historical track record for crypto or
BSE from data already sitting on disk, instead of starting the daily tracker's log at zero
and waiting weeks of daily runs for anything to resolve.

Reuses signal_tracker.backfill_watchlists - the EXACT same build_watchlist step the daily
scripts (crypto_signal_tracker.py / bse_signal_tracker.py) run each day, just replayed
across every session the store already has history for, against one `prepare_market` call
(the features are computed once, not per day - cheap even over years of history). Safe to
re-run: only new signal ids get logged, so running this after the daily job has already
logged today's calls neither double-counts nor overwrites anything.

Usage:
  uv run python scripts/backfill_signal_tracker.py crypto
  uv run python scripts/backfill_signal_tracker.py bse

For BSE, run `data load --market bse --years 10 <watchlist codes + SENSEX>` first if the
store doesn't already have enough history - there is nothing to backfill from a store that
only has a few months loaded.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bse_signal_tracker as bse_mod  # noqa: E402
import crypto_signal_tracker as crypto_mod  # noqa: E402

from tradedesk.backtest.runner import prepare_market  # noqa: E402
from tradedesk.broker.indstocks.models import Interval  # noqa: E402
from tradedesk.config import load_config  # noqa: E402
from tradedesk.data.candle_store import CandleStore  # noqa: E402
from tradedesk.markets import bse_market, crypto_market  # noqa: E402
from tradedesk.scan import scan_config  # noqa: E402
from tradedesk.signal_tracker import (  # noqa: E402
    backfill_watchlists,
    flag_setup_failures,
    load_log,
    resolve_outcomes,
    save_dashboard,
    save_log,
    scoreboard,
)


def backfill(market: str) -> None:
    settings = load_config(".")
    if market == "crypto":
        mod, mkt = crypto_mod, crypto_market(settings)
    elif market == "bse":
        mod, mkt = bse_mod, bse_market(settings)
    else:
        raise SystemExit(f"unknown market {market!r}; use crypto or bse")

    with CandleStore(mod.DB) as store:
        if market == "crypto":
            ref = f"{mkt.code_prefix}{mkt.benchmark_name}"
        else:
            ref = store.index_code(mkt.benchmark_name, exch="BSE")
            if ref is None:
                raise SystemExit(f"benchmark {mkt.benchmark_name!r} not in instruments table")
        codes = [c for c in mod.WATCHLIST if c != ref]

        stamps = [t for c in mod.WATCHLIST if (t := store.first_ts(c, Interval.D1)) is not None]
        if not stamps:
            raise SystemExit(f"no history loaded for {market}'s watchlist - run `data load` first")
        first = min(stamps)
        last = max(t for c in mod.WATCHLIST if (t := store.last_ts(c, Interval.D1)) is not None)

        cfg = scan_config(settings, last.date(), market=mkt)
        cfg.start = first.date()
        md = prepare_market(store, codes, ref, cfg)

        rows = load_log(mod.LOG)
        before = len(rows)
        new_rows = backfill_watchlists(md, cfg, settings, mkt, rows)
        resolved = resolve_outcomes(store, rows, mod.MAX_HOLD)
        save_log(rows, mod.LOG)
        save_dashboard(market, rows, mod.DASHBOARD)
        flagged = flag_setup_failures(market, rows)

        print(
            f"{market}: {cfg.start} -> {cfg.end}, {len(new_rows)} new signals logged "
            f"({before} already existed), {len(resolved)} newly resolved"
        )
        print(scoreboard(rows))
        if flagged:
            print(f"flagged for review: {', '.join(flagged)}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("crypto", "bse"):
        raise SystemExit("usage: python scripts/backfill_signal_tracker.py crypto|bse")
    backfill(sys.argv[1])
