from __future__ import annotations

import json

import httpx
import pyotp
import pytest
import respx

from tests.broker.fakes import TOTP_SECRET, FakeClock, MemoryStore
from tradedesk.broker.indstocks.auth import (
    KEY_TOTP_SECRET,
    Credentials,
    MissingSecret,
    TokenGenerationError,
    TokenProvider,
)
from tradedesk.broker.indstocks.rest import BASE_URL


def test_credentials_missing_secret_names_the_key(store: MemoryStore) -> None:
    store.delete(KEY_TOTP_SECRET)
    with pytest.raises(MissingSecret, match="totp_secret"):
        Credentials.from_store(store)


def test_totp_code_matches_pyotp(store: MemoryStore) -> None:
    creds = Credentials.from_store(store)
    assert creds.current_totp(at=1_700_000_000) == pyotp.TOTP(TOTP_SECRET).at(1_700_000_000)


@respx.mock
async def test_generate_sends_documented_request_and_caches(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock
) -> None:
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(200, json={"status": "success", "token": "tok-A"})
    )
    tp = TokenProvider(http=http, store=store, clock=clock)
    assert await tp.get_token() == "tok-A"
    assert await tp.get_token() == "tok-A"  # cached, no second call
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "client-123"
    body = json.loads(req.content)
    assert body["mpin"] == "1234"
    assert body["totp"] == pyotp.TOTP(TOTP_SECRET).at(int(clock()))
    assert "Authorization" not in req.headers  # doc: x-api-key replaces Authorization here


@respx.mock
async def test_token_regenerated_after_24h(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock
) -> None:
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        side_effect=[
            httpx.Response(200, json={"token": "tok-A"}),
            httpx.Response(200, json={"token": "tok-B"}),
        ]
    )
    tp = TokenProvider(http=http, store=store, clock=clock)
    assert await tp.get_token() == "tok-A"
    clock.advance(24 * 3600 + 1)
    assert await tp.get_token() == "tok-B"
    assert route.call_count == 2


@respx.mock
async def test_refresh_waits_for_60s_throttle(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/generate/token").mock(
        side_effect=[
            httpx.Response(200, json={"token": "tok-A"}),
            httpx.Response(200, json={"token": "tok-B"}),
        ]
    )
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    monkeypatch.setattr("tradedesk.broker.indstocks.auth.asyncio.sleep", fake_sleep)
    tp = TokenProvider(http=http, store=store, clock=clock)
    await tp.get_token()
    clock.advance(10)
    assert await tp.refresh() == "tok-B"
    assert slept == [pytest.approx(50.0)]


@respx.mock
async def test_accepts_nested_or_alternate_token_field(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock
) -> None:
    respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {"access_token": "x"}})
    )
    tp = TokenProvider(http=http, store=store, clock=clock)
    assert await tp.get_token() == "x"


@respx.mock
async def test_failure_raises_and_does_not_retry(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock
) -> None:
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(401, json={"status": "error", "message": "Invalid TOTP"})
    )
    tp = TokenProvider(http=http, store=store, clock=clock)
    with pytest.raises(TokenGenerationError, match="Invalid TOTP") as exc:
        await tp.get_token()
    assert exc.value.status == 401
    assert route.call_count == 1
    assert tp.token is None
