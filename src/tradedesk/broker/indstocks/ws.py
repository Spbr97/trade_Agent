"""WebSocket feeds (docs/indstocks-api.md, "WebSocket Streaming").

- Price feed  wss://ws-prices.indstocks.com/api/v1/ws/prices
  subscribe: {"action": "subscribe", "mode": "ltp"|"quote", "instruments": ["NSE:2885", ...]}
  tick:      {"mode", "instrument", "timestamp" (epoch ms), "data": {...}}
- Order updates  wss://ws-order-updates.indstocks.com/api/v1/ws/trades
  subscribe: {"action": "subscribe", "mode": "order_update"}  (listen-only; no orders here)

Both authenticate with an `Authorization: <token>` header on the handshake. Limits: 3
connections per user, 3,000 instruments per connection. Heartbeats are ignored.

Reliability (PLAN.md 5.3): auto-reconnect with capped exponential backoff, resubscribe
everything after reconnecting, and expose `seconds_since_last_message()` so the caller can
declare the feed stale and fail closed. Reconciliation of triggers/stops after a gap is the
caller's job (live/ in M7) - this module only reports `on_reconnect`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, Protocol

import websockets

from tradedesk.broker.indstocks.auth import TokenProvider
from tradedesk.broker.indstocks.models import Tick

PRICE_WS_URL = "wss://ws-prices.indstocks.com/api/v1/ws/prices"
ORDER_WS_URL = "wss://ws-order-updates.indstocks.com/api/v1/ws/trades"
MAX_INSTRUMENTS_PER_CONNECTION = 3000
SUBSCRIBE_BATCH = 500  # payload size hygiene; well under the per-connection cap
BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30)


class Connection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


ConnectFn = Callable[[str, dict[str, str]], Awaitable[Connection]]


async def _default_connect(url: str, headers: dict[str, str]) -> Connection:
    return await websockets.connect(url, additional_headers=headers, ping_interval=20)


def _is_heartbeat(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return True
    t = str(msg.get("type", "")).lower()
    return t in {"ping", "pong", "heartbeat"} or ("mode" not in msg and "type" not in msg)


class _Feed:
    def __init__(
        self,
        tokens: TokenProvider,
        url: str,
        *,
        connect: ConnectFn = _default_connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tokens = tokens
        self.url = url
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self._conn: Connection | None = None
        self.connected = False
        self.last_message_at: float | None = None
        self.reconnects = 0
        self.on_disconnect: Callable[[], Awaitable[None] | None] | None = None
        self.on_reconnect: Callable[[], Awaitable[None] | None] | None = None

    def seconds_since_last_message(self) -> float | None:
        return None if self.last_message_at is None else self._clock() - self.last_message_at

    async def _on_open(self, conn: Connection) -> None:  # subclass: (re)subscribe
        raise NotImplementedError

    async def _on_message(self, msg: dict[str, Any]) -> None:  # subclass: dispatch
        raise NotImplementedError

    async def run(self, stop: asyncio.Event) -> None:
        """Connect, stream until `stop` is set, reconnect on any failure."""
        attempt = 0
        first = True
        while not stop.is_set():
            try:
                token = await self.tokens.get_token()
                conn = await self._connect(self.url, {"Authorization": token})
                self._conn = conn
                self.connected = True
                await self._on_open(conn)
                if not first:
                    self.reconnects += 1
                    await _maybe_await(self.on_reconnect)
                first = False
                if await self._recv_loop(conn, stop):
                    attempt = 0  # a link that delivered data resets the backoff
            except (TimeoutError, websockets.ConnectionClosed, OSError):
                pass
            finally:
                if self.connected:
                    self.connected = False
                    await _maybe_await(self.on_disconnect)
                if self._conn is not None:
                    try:
                        await self._conn.close()
                    except Exception:  # noqa: BLE001 - closing a dead socket
                        pass
                    self._conn = None
            if stop.is_set():
                break
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            attempt += 1
            await self._sleep(delay)

    async def _recv_loop(self, conn: Connection, stop: asyncio.Event) -> bool:
        """Stream until stop or failure. Returns True if at least one message arrived."""
        stop_task = asyncio.ensure_future(stop.wait())
        healthy = False
        try:
            while not stop.is_set():
                recv_task = asyncio.ensure_future(conn.recv())
                done, _ = await asyncio.wait(
                    {recv_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if recv_task not in done:
                    recv_task.cancel()
                    return healthy
                raw = recv_task.result()
                self.last_message_at = self._clock()
                healthy = True
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if _is_heartbeat(msg):
                    continue
                await self._on_message(msg)
            return healthy
        finally:
            stop_task.cancel()


class PriceFeed(_Feed):
    """Streams ticks for `SEGMENT:TOKEN` instruments to `on_tick`."""

    def __init__(
        self,
        tokens: TokenProvider,
        on_tick: Callable[[Tick], Awaitable[None] | None],
        *,
        mode: str = "ltp",
        url: str = PRICE_WS_URL,
        **kw: Any,
    ) -> None:
        super().__init__(tokens, url, **kw)
        if mode not in {"ltp", "quote"}:
            raise ValueError("mode must be 'ltp' or 'quote'")
        self.mode = mode
        self.on_tick = on_tick
        self.instruments: set[str] = set()

    async def subscribe(self, instruments: Iterable[str]) -> None:
        new = [i for i in instruments if i not in self.instruments]
        if len(self.instruments) + len(new) > MAX_INSTRUMENTS_PER_CONNECTION:
            raise ValueError(
                f"more than {MAX_INSTRUMENTS_PER_CONNECTION} instruments per connection"
            )
        self.instruments.update(new)
        if self.connected and self._conn is not None and new:
            await self._send_subscription(self._conn, "subscribe", new)

    async def unsubscribe(self, instruments: Iterable[str]) -> None:
        gone = [i for i in instruments if i in self.instruments]
        self.instruments.difference_update(gone)
        if self.connected and self._conn is not None and gone:
            await self._send_subscription(self._conn, "unsubscribe", gone)

    async def _send_subscription(
        self, conn: Connection, action: str, instruments: Sequence[str]
    ) -> None:
        for i in range(0, len(instruments), SUBSCRIBE_BATCH):
            batch = list(instruments[i : i + SUBSCRIBE_BATCH])
            await conn.send(json.dumps({"action": action, "mode": self.mode, "instruments": batch}))

    async def _on_open(self, conn: Connection) -> None:
        if self.instruments:
            await self._send_subscription(conn, "subscribe", sorted(self.instruments))

    async def _on_message(self, msg: dict[str, Any]) -> None:
        if "instrument" not in msg or "timestamp" not in msg:
            return
        await _maybe_await(self.on_tick, Tick.from_api(msg))


class OrderUpdatesFeed(_Feed):
    """Listen-only stream of the account's order events (used by the journal in M9)."""

    def __init__(
        self,
        tokens: TokenProvider,
        on_update: Callable[[dict[str, Any]], Awaitable[None] | None],
        *,
        url: str = ORDER_WS_URL,
        **kw: Any,
    ) -> None:
        super().__init__(tokens, url, **kw)
        self.on_update = on_update

    async def _on_open(self, conn: Connection) -> None:
        await conn.send(json.dumps({"action": "subscribe", "mode": "order_update"}))

    async def _on_message(self, msg: dict[str, Any]) -> None:
        await _maybe_await(self.on_update, msg)


async def _maybe_await(fn: Callable[..., Any] | None, *args: Any) -> None:
    if fn is None:
        return
    result = fn(*args)
    if asyncio.iscoroutine(result):
        await result
