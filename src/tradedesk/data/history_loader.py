"""Pull candle history into the CandleStore, incrementally, from any market's client
(M13 Phase 3: this used to be INDstocks-only).

For each code the request starts at the last stored open time (so the most recent bar,
which may have been partial when fetched, is refreshed) or at `start` for a fresh code.
Codes with the same start are batched `max_codes_per_call` per call; batches run with
bounded concurrency and the client's own rate limiter does the pacing.

Budget check (PLAN.md 5.4): 500 stocks × 10 y daily ≈ 1,000 calls; × 2 y of 15-minute
candles ≈ 10,500 calls; × 2 y hourly ≈ 4,900 - about 16k calls, one evening at 5/s.

Market differences this now has to absorb:
- INDstocks batches up to 5 codes into one candles_history call (`MAX_CANDLE_CODES_PER_CALL`).
  CoinDCX's candles endpoint takes exactly one pair per call - `max_codes_per_call=1` gets
  the same "one call per code, several codes in flight at once" behaviour by way of the
  concurrency semaphore instead of server-side batching. Either way,
  `client.candles_history(interval, codes, start, end)` is the same shape.
- The two clients raise different exception types on a bad call (ApiError vs
  CoinDcxError) - `error_types` says which ones this call is allowed to record per-batch
  and continue past, rather than letting the whole load crash. Anything not in that tuple
  still propagates, same as before this file supported more than one market.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.broker.indstocks.rest import MAX_CANDLE_CODES_PER_CALL, ApiError
from tradedesk.data.candle_store import CandleStore


class MarketDataClient(Protocol):
    """The one method this module needs from a market's REST client. Both
    IndstocksClient and CoinDcxClient already satisfy this structurally - no explicit
    inheritance needed."""

    async def candles_history(
        self, interval: Interval, codes: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Candle]]: ...


@dataclass
class LoadResult:
    scrip_code: str
    start: datetime
    end: datetime
    fetched: int = 0
    error: str | None = None


@dataclass
class LoadSummary:
    results: list[LoadResult] = field(default_factory=list)

    @property
    def fetched(self) -> int:
        return sum(r.fetched for r in self.results)

    @property
    def errors(self) -> list[LoadResult]:
        return [r for r in self.results if r.error]


def plan_starts(
    store: CandleStore, codes: Sequence[str], interval: Interval, start: datetime
) -> dict[str, datetime]:
    """Per-code request start: the last stored bar's open time, or `start` when empty."""
    out: dict[str, datetime] = {}
    for code in codes:
        last = store.last_ts(code, interval)
        out[code] = max(start, last) if last is not None else start
    return out


async def load_history(
    client: MarketDataClient,
    store: CandleStore,
    codes: Sequence[str],
    interval: Interval,
    *,
    start: datetime,
    end: datetime | None = None,
    concurrency: int = 4,
    max_codes_per_call: int = MAX_CANDLE_CODES_PER_CALL,
    error_types: tuple[type[Exception], ...] = (ApiError,),
    progress: Callable[[LoadResult], None] | None = None,
) -> LoadSummary:
    end = end or datetime.now(IST)
    if start.tzinfo is None:
        start = start.replace(tzinfo=IST)
    starts = plan_starts(store, codes, interval, start)

    # Group by start *date* so incremental and fresh codes form separate batches.
    groups: dict[datetime, list[str]] = defaultdict(list)
    for code, s in starts.items():
        groups[s.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)].append(code)

    batches: list[tuple[datetime, list[str]]] = []
    for s, group in groups.items():
        for i in range(0, len(group), max_codes_per_call):
            batches.append((s, group[i : i + max_codes_per_call]))

    summary = LoadSummary()
    sem = asyncio.Semaphore(concurrency)

    async def run(batch_start: datetime, batch: list[str]) -> None:
        async with sem:
            results = [LoadResult(c, batch_start, end) for c in batch]
            try:
                if batch_start >= end:
                    got: dict[str, list[Candle]] = {c: [] for c in batch}
                else:
                    got = await client.candles_history(interval, batch, batch_start, end)
                for r in results:
                    candles = got.get(r.scrip_code, [])
                    r.fetched = store.upsert_candles(candles)
            except error_types as exc:
                for r in results:
                    r.error = str(exc)
            summary.results.extend(results)
            if progress:
                for r in results:
                    progress(r)

    await asyncio.gather(*(run(s, b) for s, b in batches))
    summary.results.sort(key=lambda r: r.scrip_code)
    return summary


def default_start(interval: Interval, now: datetime | None = None) -> datetime:
    """PLAN.md 5.4 depth: all available daily history (10 y), 2 y of intraday."""
    now = now or datetime.now(IST)
    years = 10 if interval in (Interval.D1, Interval.W1, Interval.MO1) else 2
    return now - timedelta(days=365 * years)
