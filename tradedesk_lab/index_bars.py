"""Index 15-minute history for the lab, kept in its own file. Research only.

Nifty and India VIX, as far back as the broker serves them, saved to
data/m14_m18/index_bars/<code>.csv by the rate-limited client. A bar's `ts` is its open time in IST.
`load` returns one session's bars in order, keeping only complete regular sessions.

    python -m tradedesk_lab.index_bars fetch
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk_lab.artifacts import ROOT

STORE = ROOT / "data" / "m14_m18" / "index_bars"
NIFTY = "NSE_40000001"
VIX = "NSE_40000107"
CODES = (NIFTY, VIX)
FIRST = datetime(2016, 1, 1, tzinfo=IST)
BARS_A_SESSION = 25  # 09:15 ... 15:15


async def _fetch(client, codes: tuple[str, ...], end: datetime) -> dict[str, int]:  # noqa: ANN001
    got = await client.candles_history(Interval.M15, list(codes), FIRST, end, skip_invalid=True)
    STORE.mkdir(parents=True, exist_ok=True)
    counts = {}
    for code, candles in got.items():
        with (STORE / f"{code}.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["ts", "open", "high", "low", "close"])
            for c in candles:
                writer.writerow([c.ts.isoformat(), c.open, c.high, c.low, c.close])
        counts[code] = len(candles)
    return counts


def fetch() -> dict[str, int]:
    from tradedesk.cli import _with_client

    end = datetime.now(IST)
    return asyncio.run(_with_client(lambda client: _fetch(client, CODES, end)))


def load(code: str = NIFTY, path: Path | None = None) -> pd.DataFrame:
    """Bars with `day` and `bar` (0 = 09:15), complete regular sessions only."""

    frame = pd.read_csv(path or STORE / f"{code}.csv")
    stamps = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    frame["day"] = stamps.dt.date
    frame["bar"] = (stamps.dt.hour * 60 + stamps.dt.minute - (9 * 60 + 15)) // 15
    frame = frame[(frame["bar"] >= 0) & (frame["bar"] < BARS_A_SESSION)]
    counts = frame.groupby("day")["bar"].transform("nunique")
    return frame[counts == BARS_A_SESSION].sort_values(["day", "bar"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=("fetch",))
    parser.parse_args()
    print(fetch())
    for code in CODES:
        bars = load(code)
        by_year = bars.groupby(bars["day"].map(lambda d: d.year))["day"].nunique()
        print(code, "complete sessions by year", by_year.to_dict())


if __name__ == "__main__":
    main()
