"""Read-only async REST client for CoinDCX's public endpoints (models.py has the full set
of verified facts this file relies on - read that first).

Scope: instruments and historical candles only. No auth, no order placement (CLAUDE.md
hard rule; M12 only, and this adapter has no order module at all, not even a stub).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import httpx

from tradedesk.broker.coindcx.models import (
    BASE_URL,
    candle_from_api,
    coindcx_interval,
    scrip_code_for_pair,
)
from tradedesk.broker.coindcx.ratelimit import RateLimiter
from tradedesk.broker.indstocks.models import Candle, Instrument, Interval

MARKETS_PATH = "/exchange/v1/markets_details"
CANDLES_PATH = "/market_data/candles"
MAX_CANDLES_PER_CALL = 1000  # documented ceiling; see models.py


class CoinDcxError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"CoinDCX API error {status_code}: {message}")
        self.status_code = status_code


class CoinDcxClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
        self.limiter = limiter or RateLimiter()

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> CoinDcxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get_json(
        self, path: str, params: dict[str, str | int | float | bool | None]
    ) -> object:
        await self.limiter.acquire()
        resp = await self.http.get(path, params=params)
        if resp.status_code // 100 != 2:
            raise CoinDcxError(resp.status_code, resp.text[:300])
        return resp.json()

    # -------------------------------------------------------------- instruments

    async def markets_details(self) -> list[dict[str, object]]:
        """Raw rows from /exchange/v1/markets_details, every quote currency and status -
        callers filter (see broker/coindcx/instruments.py::parse_markets_details)."""
        body = await self._get_json(MARKETS_PATH, {})
        if not isinstance(body, list):
            raise CoinDcxError(200, f"unexpected markets_details body: {str(body)[:200]}")
        return body

    async def instruments(self) -> list[Instrument]:
        from tradedesk.broker.coindcx.instruments import parse_markets_details

        return parse_markets_details(await self.markets_details())

    # ------------------------------------------------------------------ candles

    async def candles(
        self, interval: Interval, pair: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """One call, one pair (CoinDCX's candles endpoint has no batching, unlike
        INDstocks' 5-codes-per-call). Both startTime and endTime are ALWAYS sent
        together, ms epoch - see models.py: passing only one is silently ignored and
        returns the latest bars regardless of what was asked for."""
        params: dict[str, str | int | float | bool | None] = {
            "pair": pair,
            "interval": coindcx_interval(interval),
            "startTime": _epoch_ms(start),
            "endTime": _epoch_ms(end),
            "limit": MAX_CANDLES_PER_CALL,
        }
        body = await self._get_json(CANDLES_PATH, params)
        if not isinstance(body, list):
            raise CoinDcxError(200, f"unexpected candles body: {str(body)[:200]}")
        scrip_code = scrip_code_for_pair(pair)
        candles = [candle_from_api(scrip_code, interval, row) for row in body]
        candles.sort(key=lambda c: c.ts)  # API returns newest-first; store expects ascending
        return candles

    async def candles_history(
        self, interval: Interval, pairs: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Candle]]:
        """Any span, one pair at a time: pages backwards from `end` in windows of up to
        MAX_CANDLES_PER_CALL bars, stopping at `start` or the first empty page (the real
        edge of that pair's history - CoinDCX has no documented listing-date field to
        check in advance).

        Accepted limitation: an empty page is treated as "reached the start of history",
        which would also stop paging early on a genuine mid-history gap (a pair with zero
        trades for an entire multi-hundred-day window). Not expected for the 338 already
        liquidity-filtered INR pairs this targets; revisit if Phase 3's quality report
        shows truncated history for a pair that should have more."""
        window = timedelta(seconds=interval.seconds * MAX_CANDLES_PER_CALL)
        result: dict[str, list[Candle]] = {}
        for pair in pairs:
            bucket: dict[datetime, Candle] = {}
            win_end = end
            while win_end > start:
                win_start = max(start, win_end - window)
                got = await self.candles(interval, pair, win_start, win_end)
                if not got:
                    break  # reached the real start of this pair's history
                for c in got:
                    if start <= c.ts < end:
                        bucket[c.ts] = c
                win_end = win_start
            result[pair] = [bucket[k] for k in sorted(bucket)]
        return result


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)
