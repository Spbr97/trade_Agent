"""Read-only async REST client for CoinDCX's public endpoints (models.py has the full set
of verified facts this file relies on - read that first).

Scope: instruments and historical candles only. No auth, no order placement (CLAUDE.md
hard rule; M12 only, and this adapter has no order module at all, not even a stub).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

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
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = 2.0
# What is still failing after the retries: the loader records it against that pair and moves on
# (data/history_loader.py `error_types`) instead of crashing the whole load.
CoinDcxNetworkErrors: tuple[type[Exception], ...] = (httpx.TransportError,)


class CoinDcxError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"CoinDCX API error {status_code}: {message}")
        self.status_code = status_code


class CoinDcxClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
        pair_for_code: Mapping[str, str] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
        self.limiter = limiter or RateLimiter()
        self.pair_for_code: dict[str, str] = dict(pair_for_code or {})
        self._sleep = sleep or asyncio.sleep

    def register_pairs(self, mapping: Mapping[str, str]) -> None:
        """scrip_code -> CoinDCX pair (e.g. "CDX_BTCINR" -> "I-BTC_INR"), needed by
        candles_history() - see its docstring. Typically populated from
        CandleStore.custom_symbols(codes) once sync-instruments has run, since that
        already has every pair on hand without another API call."""
        self.pair_for_code.update(mapping)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> CoinDcxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get_json(
        self, path: str, params: dict[str, str | int | float | bool | None]
    ) -> object:
        """A timeout, a dropped connection, 429 or a 5xx is retried (RETRY_ATTEMPTS in all,
        waits of 2 s then 4 s, each attempt through the rate limiter): on 2026-10-18 one slow
        reply failed the whole crypto tracker twice. Any other error status raises at once."""

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            await self.limiter.acquire()
            try:
                resp = await self.http.get(path, params=params)
            except httpx.TransportError:
                if attempt == RETRY_ATTEMPTS:
                    raise
                await self._sleep(RETRY_BACKOFF_S * attempt)
                continue
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if retryable and attempt < RETRY_ATTEMPTS:
                await self._sleep(RETRY_BACKOFF_S * attempt)
                continue
            if resp.status_code // 100 != 2:
                raise CoinDcxError(resp.status_code, resp.text[:300])
            return resp.json()
        raise AssertionError("unreachable")  # pragma: no cover

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
        body = await self.candle_rows(interval, pair, start, end)
        scrip_code = scrip_code_for_pair(pair)
        candles = [candle_from_api(scrip_code, interval, row) for row in body]
        candles.sort(key=lambda c: c.ts)  # API returns newest-first; store expects ascending
        return candles

    async def candle_rows(
        self, interval: Interval, pair: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Return validated raw rows without rounding CoinDCX's fractional volume.

        The shared :class:`Candle` contract intentionally keeps whole-unit volume for
        equities. M21 needs the exchange's precise base-asset volume for causal relative-
        volume features, so this additive view preserves the public payload while using
        the same endpoint, time bounds, and rate limiter as :meth:`candles`.
        """

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
        if not all(isinstance(row, dict) for row in body):
            raise CoinDcxError(200, f"unexpected candle row: {str(body)[:200]}")
        return [dict(row) for row in body]

    async def candle_rows_history_by_pair(
        self, interval: Interval, pairs: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        """Page raw rows by pair while preserving fractional base-asset volume."""

        window = timedelta(seconds=interval.seconds * MAX_CANDLES_PER_CALL)
        result: dict[str, list[dict[str, Any]]] = {}
        for pair in pairs:
            bucket: dict[int, dict[str, Any]] = {}
            win_end = end
            while win_end > start:
                win_start = max(start, win_end - window)
                got = await self.candle_rows(interval, pair, win_start, win_end)
                if not got:
                    break
                for row in got:
                    timestamp_ms = int(row["time"])
                    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
                    if start <= timestamp < end:
                        bucket[timestamp_ms] = row
                win_end = win_start
            result[pair] = [bucket[key] for key in sorted(bucket)]
        return result

    async def candles_history_by_pair(
        self, interval: Interval, pairs: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Candle]]:
        """Any span, one pair at a time: pages backwards from `end` in windows of up to
        MAX_CANDLES_PER_CALL bars, stopping at `start` or the first empty page (the real
        edge of that pair's history - CoinDCX has no documented listing-date field to
        check in advance). Keyed by `pair` (CoinDCX's own identifier, e.g. "I-BTC_INR") -
        see candles_history() for the tradedesk-scrip-code-keyed version every other
        market's client (and data/history_loader.py) actually uses.

        Accepted limitation: an empty page is treated as "reached the start of history",
        which would also stop paging early on a genuine mid-history gap (a pair with zero
        trades for an entire multi-hundred-day window). Not expected for the 338 already
        liquidity-filtered INR pairs this targets; revisit if Phase 3's quality report
        shows truncated history for a pair that should have more."""
        rows = await self.candle_rows_history_by_pair(interval, pairs, start, end)
        return {
            pair: [candle_from_api(scrip_code_for_pair(pair), interval, row) for row in values]
            for pair, values in rows.items()
        }

    async def candle_rows_history(
        self, interval: Interval, codes: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        """Scrip-code-keyed precise rows for research features that need float volume."""

        resolved = {code: self.pair_for_code[code] for code in codes if code in self.pair_for_code}
        pair_to_code = {pair: code for code, pair in resolved.items()}
        by_pair = await self.candle_rows_history_by_pair(
            interval, list(resolved.values()), start, end
        )
        output = {pair_to_code[pair]: values for pair, values in by_pair.items()}
        for code in codes:
            output.setdefault(code, [])
        return output

    async def candles_history(
        self, interval: Interval, codes: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Candle]]:
        """The data/history_loader.py::MarketDataClient protocol: `codes` are tradedesk
        scrip codes ("CDX_BTCINR"), NOT CoinDCX's own `pair` identifiers - unlike
        IndstocksClient, whose scrip_code already IS its native id, CoinDCX's isn't, so
        this needs register_pairs() (or the pair_for_code constructor arg) called first
        for every code - typically from CandleStore.custom_symbols(codes). A code with no
        registered pair returns an empty list rather than raising, the same as an unknown
        or delisted code."""
        resolved = {c: self.pair_for_code[c] for c in codes if c in self.pair_for_code}
        pair_to_code = {p: c for c, p in resolved.items()}
        by_pair = await self.candles_history_by_pair(interval, list(resolved.values()), start, end)
        out = {pair_to_code[p]: v for p, v in by_pair.items()}
        for c in codes:
            out.setdefault(c, [])
        return out


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)
