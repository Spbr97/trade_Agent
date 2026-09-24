"""Bounded, cached-token-only M1 history access for isolated research collection.

Uses production's candle parser and RateLimiter without changing either. No automatic
retry, token generation, refresh, credential writes, or non-history endpoint is allowed.
The one-request-per-second pacing and budget are per instance, not account-wide: the
caller must separately check for competing broker jobs before using this client.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from tradedesk.broker.indstocks.auth import (
    KEY_TOKEN,
    KEY_TOKEN_ISSUED_AT,
    TOKEN_TTL_SECONDS,
    KeyringStore,
)
from tradedesk.broker.indstocks.models import Candle, Interval
from tradedesk.broker.indstocks.ratelimit import DAILY_CAP, Category, RateLimiter
from tradedesk.broker.indstocks.rest import BASE_URL, IndstocksClient

HISTORY_PATH = "/market/historical/1minute"
MIN_REQUEST_GAP_SECONDS = 1.0


class HistoryClientError(RuntimeError):
    """A sanitized collector failure; no broker response or credentials are included."""


class HistoryAuthenticationError(HistoryClientError):
    """Cached authentication is unavailable, expired, or rejected; nothing is refreshed."""


class HistoryBudgetExceeded(HistoryClientError):
    """This run's exact outbound-request budget is exhausted."""


class HistoryResponseError(HistoryClientError):
    """The history payload is malformed or contains invalid uncoerced candle values."""


class HistoryApiError(HistoryClientError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"historical request failed with HTTP {status}; no automatic retry")


class TokenStore(Protocol):
    """Deliberately read-only, unlike production's broader SecretStore protocol."""

    def get(self, key: str) -> str | None: ...


class CachedTokenProvider:
    def __init__(
        self, store: TokenStore | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = KeyringStore() if store is None else store
        self._clock = clock

    async def get_token(self) -> str:
        # Reload each time so a token replaced by the normal production auth workflow
        # can be reused. Never read any of the generation credentials or modify keyring.
        try:
            token = self._store.get(KEY_TOKEN)
            issued_raw = self._store.get(KEY_TOKEN_ISSUED_AT)
            if not isinstance(token, str) or not token or not token.isascii():
                raise ValueError
            if any(ord(ch) <= 32 or ord(ch) == 127 for ch in token):
                raise ValueError
            if isinstance(issued_raw, bool) or issued_raw is None:
                raise ValueError
            issued = float(issued_raw)
            now = float(self._clock())
            if not math.isfinite(issued) or not math.isfinite(now) or issued <= 0:
                raise ValueError
            age = now - issued
            if not 0 <= age < TOKEN_TTL_SECONDS:
                raise ValueError
        except Exception:
            raise HistoryAuthenticationError(
                "valid cached token unavailable; research client will not generate or refresh it"
            ) from None
        return token

    async def refresh(self) -> str:
        raise HistoryAuthenticationError("token refresh is disabled for research collection")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _validate_payload(body: Any, codes: Sequence[str]) -> dict[str, Any]:
    """Validate before Candle.from_api can truncate timestamps/volumes via int()."""
    try:
        if not isinstance(body, dict) or body.get("success") is not True:
            raise ValueError
        data = body.get("data")
        if not isinstance(data, dict) or not set(data).issubset(codes):
            raise ValueError
        for entry in data.values():
            if not isinstance(entry, dict) or not isinstance(entry.get("candles"), list):
                raise ValueError
            seen: dict[int, tuple[Any, ...]] = {}
            for raw in entry["candles"]:
                if not isinstance(raw, dict):
                    raise ValueError
                ts, volume = _number(raw["ts"]), _number(raw["v"])
                if not ts.is_integer() or not 0 <= ts <= 253402300799 or ts % 60:
                    raise ValueError
                if not volume.is_integer() or not 0 <= volume <= 2**63 - 1:
                    raise ValueError
                # An integer larger than float's exact range must not be rounded into
                # another volume. DuckDB BIGINT receives the original exact integer.
                if int(volume) != raw["v"]:
                    raise ValueError
                o, h, low, c = (_number(raw[k]) for k in ("o", "h", "l", "c"))
                if min(o, h, low, c) <= 0 or h < max(o, c) or low > min(o, c) or h < low:
                    raise ValueError
                values = (o, h, low, c, raw["v"])
                key = int(ts)
                if key in seen and seen[key] != values:
                    raise ValueError
                seen[key] = values
    except (KeyError, TypeError, ValueError, OverflowError):
        raise HistoryResponseError("invalid historical candle payload; batch rejected") from None
    return body


class _HistoricalClient(IndstocksClient):
    """Reuse candles() unchanged while narrowing its transport and raw-data contract."""

    def __init__(
        self,
        *,
        max_requests: int,
        tokens: CachedTokenProvider,
        http: httpx.AsyncClient,
        limiter: RateLimiter,
        sleep: Callable[[float], Awaitable[None]],
        monotonic: Callable[[], float],
    ) -> None:
        super().__init__(tokens=tokens, http=http, limiter=limiter, sleep=sleep, max_attempts=1)
        self.max_requests = max_requests
        self.request_count = 0
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()
        self.closed = False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        category: Category,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if method != "GET" or path != HISTORY_PATH or category != Category.DATA:
            raise HistoryClientError("only the read-only M1 historical endpoint is allowed")
        async with self._request_lock:
            if self.closed:
                raise HistoryClientError("history client is closed")
            if self.request_count >= self.max_requests:
                raise HistoryBudgetExceeded("historical request budget exhausted")
            # Fail missing auth before spending a limiter slot or waiting on pacing.
            token = await self.tokens.get_token()
            await self.limiter.acquire(Category.DATA)
            if self._last_request_at is not None:
                delay = MIN_REQUEST_GAP_SECONDS - (self._monotonic() - self._last_request_at)
                if delay > 0:
                    await self._sleep(delay)
                    token = await self.tokens.get_token()
            self._last_request_at = self._monotonic()
            self.request_count += 1
            try:
                # Disable redirects even on an injected client: each attempt means one
                # HTTP request and credentials must not follow an unexpected redirect.
                response = await self.http.request(
                    method,
                    path,
                    params=params,
                    headers={"Authorization": token},
                    follow_redirects=False,
                )
            except Exception:
                raise HistoryClientError(
                    "historical transport failed; counted as one attempt; no automatic retry"
                ) from None
            if response.status_code in (401, 403):
                raise HistoryAuthenticationError(
                    "cached token rejected by broker; refresh is disabled for research collection"
                )
            if response.status_code // 100 != 2:
                raise HistoryApiError(response.status_code)
            return response

    async def _get_json(
        self, path: str, *, category: Category, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self._request("GET", path, category=category, params=params)
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            raise HistoryResponseError("historical response is not valid JSON") from None
        codes = str((params or {}).get("scrip-codes", "")).split(",")
        return _validate_payload(body, codes)


class HistoryClient:
    """One capped, serialized, non-refreshing research collection session.

    The caller owns an injected http client; otherwise aclose() closes our new client.
    Request count includes failed HTTP/transport attempts and excludes local rejections.
    """

    def __init__(
        self,
        *,
        max_requests: int,
        store: TokenStore | None = None,
        http: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(max_requests) is not int or not 1 <= max_requests <= DAILY_CAP:
            raise ValueError(f"max_requests must be an integer from 1 to {DAILY_CAP}")
        if http is not None and str(http.base_url).rstrip("/") != BASE_URL:
            raise ValueError("history client requires the documented INDstocks base URL")
        self._owns_http = http is None
        transport = http if http is not None else httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
        self._client = _HistoricalClient(
            max_requests=max_requests,
            tokens=CachedTokenProvider(store=store, clock=clock),
            http=transport,
            limiter=RateLimiter() if limiter is None else limiter,
            sleep=sleep,
            monotonic=monotonic,
        )

    @property
    def request_count(self) -> int:
        return self._client.request_count

    @property
    def remaining_requests(self) -> int:
        return self._client.max_requests - self.request_count

    async def candles(
        self, codes: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Candle]]:
        if isinstance(codes, (str, bytes)) or not 1 <= len(codes) <= 5:
            raise ValueError("one to five unique NSE scrip codes are required")
        # The request and parse phases must observe the same codes even if a caller
        # mutates its list while the HTTP request is in flight.
        codes = tuple(codes)
        if any(not isinstance(c, str) or not re.fullmatch(r"NSE_[0-9]+", c) for c in codes):
            raise ValueError("only NSE numeric scrip codes are allowed")
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate scrip codes are not allowed")
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise ValueError("start and end must be timezone-aware datetimes")
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("start and end must be timezone-aware datetimes")
        if not timedelta(0) < end.astimezone(UTC) - start.astimezone(UTC) <= timedelta(days=7):
            raise ValueError("M1 request window must be positive and at most seven days")
        if start.microsecond % 1000 or end.microsecond % 1000:
            raise ValueError("request boundaries must be aligned to epoch milliseconds")
        return await self._client.candles(Interval.M1, codes, start, end)

    async def aclose(self) -> None:
        async with self._client._request_lock:
            self._client.closed = True
            if self._owns_http:
                await self._client.aclose()

    async def __aenter__(self) -> HistoryClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()
