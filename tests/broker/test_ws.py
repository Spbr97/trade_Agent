from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tradedesk.broker.indstocks.auth import TokenProvider
from tradedesk.broker.indstocks.models import Tick
from tradedesk.broker.indstocks.ws import OrderUpdatesFeed, PriceFeed


class FakeConn:
    """Scripted server: yields `messages`, then raises `drop` (or blocks until closed)."""

    def __init__(self, messages: list[Any], drop: BaseException | None = None) -> None:
        self.messages = list(messages)
        self.drop = drop
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._closed_event = asyncio.Event()

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if self.messages:
            m = self.messages.pop(0)
            return m if isinstance(m, str) else json.dumps(m)
        if self.drop is not None:
            raise self.drop
        await self._closed_event.wait()
        raise OSError("closed")

    async def close(self) -> None:
        self.closed = True
        self._closed_event.set()


class FakeConnector:
    def __init__(self, conns: list[FakeConn]) -> None:
        self.conns = list(conns)
        self.headers: list[dict[str, str]] = []

    async def __call__(self, url: str, headers: dict[str, str]) -> FakeConn:
        self.headers.append(headers)
        if not self.conns:
            raise OSError("no more connections scripted")
        return self.conns.pop(0)


def tick(instrument: str, ltp: float, ts_ms: int = 1_750_138_351_089) -> dict[str, Any]:
    return {"mode": "ltp", "instrument": instrument, "timestamp": ts_ms, "data": {"ltp": ltp}}


async def run_until(feed: PriceFeed | OrderUpdatesFeed, cond: asyncio.Event) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(stop))
    await asyncio.wait_for(cond.wait(), timeout=2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_subscribes_on_open_and_delivers_ticks_ignoring_heartbeats(
    tokens: TokenProvider,
) -> None:
    got: list[Tick] = []
    done = asyncio.Event()

    def on_tick(t: Tick) -> None:
        got.append(t)
        if len(got) == 2:
            done.set()

    conn = FakeConn([{"type": "ping"}, tick("2885", 1426), "not json", tick("3045", 788.8)])
    connector = FakeConnector([conn])
    feed = PriceFeed(tokens, on_tick, connect=connector)
    await feed.subscribe(["NSE:2885", "NSE:3045"])

    await run_until(feed, done)

    assert connector.headers == [{"Authorization": "tok-0"}]
    assert conn.sent == [
        {"action": "subscribe", "mode": "ltp", "instruments": ["NSE:2885", "NSE:3045"]}
    ]
    assert [(t.instrument, t.ltp) for t in got] == [("2885", 1426.0), ("3045", 788.8)]
    assert got[0].timestamp.isoformat().startswith("2025-06-17")  # epoch ms → IST datetime
    assert feed.seconds_since_last_message() is not None


async def test_reconnects_with_backoff_and_resubscribes(tokens: TokenProvider) -> None:
    got: list[Tick] = []
    done = asyncio.Event()
    sleeps: list[float] = []
    events: list[str] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    def on_tick(t: Tick) -> None:
        got.append(t)
        done.set()

    c1 = FakeConn([], drop=OSError("dropped"))
    c2 = FakeConn([], drop=OSError("dropped again"))
    c3 = FakeConn([tick("2885", 1.0)])
    connector = FakeConnector([c1, c2, c3])
    feed = PriceFeed(tokens, on_tick, connect=connector, sleep=fake_sleep)
    feed.on_disconnect = lambda: events.append("down")
    feed.on_reconnect = lambda: events.append("up")
    await feed.subscribe(["NSE:2885"])

    await run_until(feed, done)

    assert sleeps == [1, 2]  # exponential backoff between attempts
    assert feed.reconnects == 2
    assert events == ["down", "up", "down", "up", "down"]  # final "down" is the stop
    for c in (c1, c2, c3):
        assert c.sent[0]["instruments"] == ["NSE:2885"]  # resubscribed each time
    assert got[0].ltp == 1.0


async def test_live_subscribe_and_unsubscribe_send_messages(tokens: TokenProvider) -> None:
    conn = FakeConn([])
    feed = PriceFeed(tokens, lambda t: None, mode="quote", connect=FakeConnector([conn]))
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(stop))
    await asyncio.sleep(0.05)
    assert feed.connected
    await feed.subscribe(["NSE:1", "NSE:2"])
    await feed.subscribe(["NSE:2"])  # already subscribed: no message
    await feed.unsubscribe(["NSE:1"])
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert conn.sent == [
        {"action": "subscribe", "mode": "quote", "instruments": ["NSE:1", "NSE:2"]},
        {"action": "unsubscribe", "mode": "quote", "instruments": ["NSE:1"]},
    ]
    assert feed.instruments == {"NSE:2"}


async def test_subscription_cap_per_connection(tokens: TokenProvider) -> None:
    feed = PriceFeed(tokens, lambda t: None, connect=FakeConnector([]))
    with pytest.raises(ValueError, match="3000"):
        await feed.subscribe([f"NSE:{i}" for i in range(3001)])


def test_mode_validated(tokens: TokenProvider) -> None:
    with pytest.raises(ValueError):
        PriceFeed(tokens, lambda t: None, mode="depth", connect=FakeConnector([]))


async def test_order_updates_feed_subscribes_and_forwards(tokens: TokenProvider) -> None:
    got: list[dict[str, Any]] = []
    done = asyncio.Event()

    def on_update(m: dict[str, Any]) -> None:
        got.append(m)
        done.set()

    update = {"type": "order", "order_id": "EQ-1", "order_status": "SUCCESS"}
    conn = FakeConn([{"type": "heartbeat"}, update])
    feed = OrderUpdatesFeed(tokens, on_update, connect=FakeConnector([conn]))
    await run_until(feed, done)
    assert conn.sent == [{"action": "subscribe", "mode": "order_update"}]
    assert got == [update]
