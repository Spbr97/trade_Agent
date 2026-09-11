"""M2 measurement (PLAN.md 5.2): how soon after 15:30 IST is the daily candle close final?

Standalone script, not part of the `tradedesk` package - it's a one-off measurement tool,
run once (or a few times) and then retired. Uses the same TOTP auth as the CLI, straight
from the keychain, so it needs no session/env beyond what `tradedesk auth setup` already
stored. Meant to be launched unattended (Windows Task Scheduler) so it does not depend on
an interactive session staying open until market close.

Polls the `1day` candle for a handful of liquid scrips every 60 s from 15:25 to 16:15 IST,
logs every poll, and reports the last timestamp each scrip's close changed - "final" is
defined operationally (the close stops moving), since there is no independent live feed
to compare against outside market hours.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from tradedesk.broker.indstocks import IndstocksClient, TokenProvider
from tradedesk.broker.indstocks.models import Interval
from tradedesk.broker.indstocks.rest import BASE_URL

IST = ZoneInfo("Asia/Kolkata")
SCRIPS = {"NSE_3045": "SBIN", "NSE_2885": "RELIANCE", "NSE_3063": "VEDL",
          "NSE_1333": "HDFCBANK", "NSE_11536": "TCS"}  # fmt: skip
POLL_EVERY_S = 60
START_AT = (15, 25)
STOP_AT = (16, 15)
OUT = Path(__file__).resolve().parent.parent / "data" / "reports"


def _today_window() -> tuple[datetime, datetime]:
    today = datetime.now(IST).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=IST).replace(
        hour=START_AT[0], minute=START_AT[1]
    )
    stop = datetime.combine(today, datetime.min.time(), tzinfo=IST).replace(
        hour=STOP_AT[0], minute=STOP_AT[1]
    )
    return start, stop


async def poll_once(client: IndstocksClient, on: date) -> dict[str, float | None]:
    """A window ending exactly at "today" drops today's own candle (observed live,
    2026-09-11) even though a wider window covering the same end boundary returns it -
    undocumented API quirk. Request several days back and pick out `on` client-side,
    same shape as the already-verified `tradedesk candles --days N` path."""
    day_start = datetime.combine(on, datetime.min.time(), tzinfo=IST)
    candles = await client.candles(
        Interval.D1, list(SCRIPS), day_start - timedelta(days=5), day_start + timedelta(days=1)
    )
    out: dict[str, float | None] = {}
    for code, bars in candles.items():
        match = [b for b in bars if b.ts.date() == on]
        out[code] = match[-1].close if match else None
    return out


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    on = datetime.now(IST).date()
    log_path = OUT / f"close_finality_{on.isoformat()}.jsonl"
    start, stop = _today_window()
    now = datetime.now(IST)
    if now < start:
        await asyncio.sleep((start - now).total_seconds())
    http = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
    client = IndstocksClient(TokenProvider(http=http))
    last: dict[str, float | None] = dict.fromkeys(SCRIPS)
    last_changed: dict[str, datetime] = {}
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            while datetime.now(IST) <= stop:
                t = datetime.now(IST)
                closes = await poll_once(client, on)
                fh.write(json.dumps({"at": t.isoformat(), "closes": closes}) + "\n")
                fh.flush()
                for code, c in closes.items():
                    if c != last[code]:
                        last_changed[code] = t
                        last[code] = c
                sleep_for = POLL_EVERY_S - (datetime.now(IST) - t).total_seconds()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
    finally:
        await client.aclose()
    summary = {
        "date": on.isoformat(),
        "closes": last,
        "last_changed_at": {k: v.isoformat() for k, v in last_changed.items()},
        "minutes_after_1530": {
            k: round((v - start.replace(hour=15, minute=30)).total_seconds() / 60, 1)
            for k, v in last_changed.items()
        },
    }
    (OUT / f"close_finality_{on.isoformat()}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
