"""After the close: read today's order book (read-only) and record the owner's real fills.

See src/tradedesk/owner_trades.py. Scheduled task tradedesk-owner-trades, weekdays 15:50 (skipped
on exchange holidays), before the 16:00 close job so that evening's earnings-drift message knows
how many of the owner's slots are in use. One GET /order-book; nothing is ever placed.

    python scripts/owner_trades_sync.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk import earnings_drift  # noqa: E402
from tradedesk import owner_trades as ot  # noqa: E402


def slots() -> int:
    from tradedesk.config import load_config

    return int(float(load_config(ROOT).risk.trading_capital) // earnings_drift.AMOUNT_INR)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print, do not record or push")
    args = parser.parse_args()
    from tradedesk.cli import _with_client

    rows: list[dict[str, Any]] = []

    async def go(client: Any) -> None:
        rows.extend(await client.order_book())

    asyncio.run(_with_client(go))
    isins = ot.isin_map(ROOT / ot.INSTRUMENTS)
    fills = ot.executed(rows, isins)
    ledger = ROOT / ot.LEDGER
    seen = ot.read(ledger)
    calls = earnings_drift.positions(earnings_drift.read_ledger(ROOT / earnings_drift.LEDGER))
    events = ot.match(fills, calls, isins, seen)
    print(f"{len(rows)} orders today, {len(fills)} filled equity, {len(events)} new")
    if args.dry_run:
        for e in events:
            print(json.dumps(e, default=str))
        return
    if events:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, default=str) + "\n")
        from tradedesk.task_health import alert

        alert(*ot.message(events, ot.open_drift_positions([*seen, *events]), slots()))


if __name__ == "__main__":
    main()
