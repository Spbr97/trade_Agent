from __future__ import annotations

import httpx
import pytest

from tests.broker.fakes import TOTP_SECRET, FakeClock, MemoryStore
from tradedesk.broker.indstocks.auth import (
    KEY_CLIENT_ID,
    KEY_MPIN,
    KEY_TOTP_SECRET,
    TokenProvider,
)
from tradedesk.broker.indstocks.ratelimit import RateLimiter
from tradedesk.broker.indstocks.rest import BASE_URL, IndstocksClient


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore(
        **{KEY_CLIENT_ID: "client-123", KEY_MPIN: "1234", KEY_TOTP_SECRET: TOTP_SECRET}
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def http() -> httpx.AsyncClient:
    async with httpx.AsyncClient(base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def tokens(http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock) -> TokenProvider:
    tp = TokenProvider(http=http, store=store, clock=clock)
    tp.set_token("tok-0")
    return tp


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(tokens: TokenProvider, http: httpx.AsyncClient, sleeps: list[float]) -> IndstocksClient:
    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    return IndstocksClient(tokens, http=http, limiter=RateLimiter(), sleep=fake_sleep)
