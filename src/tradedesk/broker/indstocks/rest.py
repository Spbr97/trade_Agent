"""Async REST client for the INDstocks read-only endpoints (docs/indstocks-api.md).

Scope: profile, funds, instruments, historical candles, quotes. Order placement is
deliberately absent (CLAUDE.md hard rule; Milestone M12 only).

Every call goes through `RateLimiter` and the retry policy from the doc's Error Bucket:
- 429, 5xx, NetworkException / ServiceUnavailable / GatewayTimeout → back off and retry;
- 401/403 TokenException → regenerate the token once, then retry;
- 400/404/405 and other 4xx → raise `ApiError`, no retry.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

import httpx

from tradedesk.broker.indstocks.auth import TokenProvider
from tradedesk.broker.indstocks.instruments import parse_index_csv, parse_instruments_csv
from tradedesk.broker.indstocks.models import (
    IST,
    Candle,
    FullQuote,
    Funds,
    IndexInstrument,
    Instrument,
    Interval,
    LtpQuote,
    Profile,
)
from tradedesk.broker.indstocks.ratelimit import Category, RateLimiter

BASE_URL = "https://api.indstocks.com"
MAX_CANDLE_CODES_PER_CALL = 5
MAX_QUOTE_CODES_PER_CALL = 1000
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0

_RETRYABLE_TYPES = {
    "NetworkException",
    "ServiceUnavailableException",
    "GatewayTimeoutException",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str, error_type: str | None = None) -> None:
        super().__init__(f"HTTP {status} {error_type or ''}: {message}".replace("  ", " "))
        self.status = status
        self.message = message
        self.error_type = error_type


def _error_details(resp: httpx.Response) -> tuple[str, str | None]:
    """Doc: check HTTP status first, then whichever of error_type/debug_info/message/error."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200], None
    if not isinstance(body, dict):
        return str(body)[:200], None
    error_type = body.get("error_type")
    for key in ("debug_info", "message", "error"):
        if body.get(key):
            return str(body[key]), error_type
    return resp.text[:200], error_type


def _to_epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return int(dt.timestamp() * 1000)


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class IndstocksClient:
    def __init__(
        self,
        tokens: TokenProvider,
        http: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.tokens = tokens
        self.http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
        self.limiter = limiter or RateLimiter()
        self._sleep = sleep
        self._max_attempts = max_attempts

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        category: Category,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        refreshed = False
        for attempt in range(1, self._max_attempts + 1):
            await self.limiter.acquire(category)
            token = await self.tokens.get_token()
            resp = await self.http.request(
                method, path, params=params, headers={"Authorization": token}
            )
            status = resp.status_code
            if status // 100 == 2:
                return resp
            message, error_type = _error_details(resp)

            if status in (401, 403) and not refreshed:
                # TokenException: regenerate once, then retry the same request.
                await self.tokens.refresh()
                refreshed = True
                continue
            retryable = status == 429 or status >= 500 or error_type in _RETRYABLE_TYPES
            if retryable and attempt < self._max_attempts:
                delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
                await self._sleep(delay + random.uniform(0, 0.25))
                continue
            raise ApiError(status, message, error_type)
        raise ApiError(0, "retry loop exhausted")  # pragma: no cover

    async def _get_json(
        self, path: str, *, category: Category, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resp = await self._request("GET", path, category=category, params=params)
        body = resp.json()
        if not isinstance(body, dict):
            raise ApiError(resp.status_code, f"unexpected body: {str(body)[:200]}")
        # Two envelopes exist: {"status": "success", ...} and {"success": true, ...}.
        ok = body.get("success", None)
        if ok is None:
            ok = body.get("status") == "success"
        if not ok:
            message, error_type = _error_details(resp)
            raise ApiError(resp.status_code, message, error_type)
        return body

    # ------------------------------------------------------------------- account

    async def profile(self) -> Profile:
        body = await self._get_json("/user/profile", category=Category.NON_TRADING)
        return Profile.model_validate(body["data"])

    async def funds(self) -> Funds:
        body = await self._get_json("/funds", category=Category.NON_TRADING)
        return Funds.model_validate(body["data"])

    # --------------------------------------------------------------- instruments

    async def instruments_csv(self, source: str) -> str:
        """Raw CSV for source in {equity, fno, index}."""
        if source not in {"equity", "fno", "index"}:
            raise ValueError(f"source must be equity|fno|index, got {source!r}")
        resp = await self._request(
            "GET", "/market/instruments", category=Category.DATA, params={"source": source}
        )
        return resp.text

    async def equity_instruments(self) -> list[Instrument]:
        return parse_instruments_csv(await self.instruments_csv("equity"))

    async def fno_instruments(self) -> list[Instrument]:
        return parse_instruments_csv(await self.instruments_csv("fno"))

    async def index_instruments(self) -> list[IndexInstrument]:
        return parse_index_csv(await self.instruments_csv("index"))

    # ------------------------------------------------------------------- candles

    async def candles(
        self,
        interval: Interval,
        scrip_codes: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[Candle]]:
        """One call: ≤5 codes, window ≤ the interval's maximum. `end` is exclusive."""
        if len(scrip_codes) > MAX_CANDLE_CODES_PER_CALL:
            raise ValueError(f"at most {MAX_CANDLE_CODES_PER_CALL} scrip codes per call")
        if start > end:
            raise ValueError("start must not be after end")
        body = await self._get_json(
            f"/market/historical/{interval.value}",
            category=Category.DATA,
            params={
                "scrip-codes": ",".join(scrip_codes),
                "start_time": _to_epoch_ms(start),
                "end_time": _to_epoch_ms(end),
            },
        )
        data = body.get("data") or {}
        out: dict[str, list[Candle]] = {}
        for code in scrip_codes:  # codes with no data are absent from `data`
            raw = data.get(code, {}).get("candles") or []
            out[code] = [Candle.from_api(code, interval, c) for c in raw]
        return out

    async def candles_history(
        self,
        interval: Interval,
        scrip_codes: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[Candle]]:
        """Any number of codes and any span: batches of 5, paging backwards from `end`
        in windows of the interval's maximum, de-duplicated by open time, sorted ascending."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=IST)
        if end.tzinfo is None:
            end = end.replace(tzinfo=IST)
        window = timedelta(days=interval.max_window_days)
        result: dict[str, dict[datetime, Candle]] = {c: {} for c in scrip_codes}
        for batch in _chunks(list(scrip_codes), MAX_CANDLE_CODES_PER_CALL):
            win_end = end
            while win_end > start:
                win_start = max(start, win_end - window)
                got = await self.candles(interval, batch, win_start, win_end)
                for code, candles in got.items():
                    bucket = result[code]
                    for c in candles:
                        if start <= c.ts < end:  # server may return more than asked
                            bucket[c.ts] = c
                win_end = win_start
        return {code: [b[k] for k in sorted(b)] for code, b in result.items()}

    # -------------------------------------------------------------------- quotes

    async def quotes_full(self, scrip_codes: Sequence[str]) -> dict[str, FullQuote]:
        out: dict[str, FullQuote] = {}
        for batch in _chunks(list(scrip_codes), MAX_QUOTE_CODES_PER_CALL):
            body = await self._get_json(
                "/market/quotes/full",
                category=Category.QUOTE,
                params={"scrip-codes": ",".join(batch)},
            )
            for code, raw in (body.get("data") or {}).items():
                out[code] = FullQuote.from_api(code, raw)
        return out

    async def quotes_ltp(self, scrip_codes: Sequence[str]) -> dict[str, LtpQuote]:
        out: dict[str, LtpQuote] = {}
        for batch in _chunks(list(scrip_codes), MAX_QUOTE_CODES_PER_CALL):
            body = await self._get_json(
                "/market/quotes/ltp",
                category=Category.QUOTE,
                params={"scrip-codes": ",".join(batch)},
            )
            for code, raw in (body.get("data") or {}).items():
                out[code] = LtpQuote(scrip_code=code, live_price=float(raw["live_price"]))
        return out
