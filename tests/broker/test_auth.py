from __future__ import annotations

import asyncio
import json

import httpx
import pyotp
import pytest
import respx

from tests.broker.fakes import TOTP_SECRET, FakeClock, MemoryStore
from tradedesk.broker.indstocks.auth import (
    KEY_TOKEN,
    KEY_TOKEN_ISSUED_AT,
    KEY_TOTP_SECRET,
    Credentials,
    MissingSecret,
    TokenGenerationError,
    TokenProvider,
    token_generation_lock,
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


@respx.mock
async def test_token_is_shared_across_processes_via_the_store(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock
) -> None:
    """A second TokenProvider (= a second `tradedesk` process) must adopt the persisted
    token rather than generate its own: generation is 1/min and kills the previous token."""
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        side_effect=[
            httpx.Response(200, json={"token": "tok-A"}),
            httpx.Response(200, json={"token": "tok-B"}),
        ]
    )
    first = TokenProvider(http=http, store=store, clock=clock)
    assert await first.get_token() == "tok-A"
    clock.advance(5)
    second = TokenProvider(http=http, store=store, clock=clock)  # fresh process, same store
    assert await second.get_token() == "tok-A"
    assert route.call_count == 1
    # after 24 h the second process regenerates, and the first then adopts the new token
    clock.advance(24 * 3600)
    assert await second.get_token() == "tok-B"
    first.invalidate = lambda: None  # type: ignore[method-assign]
    first._token = None  # simulate the first process noticing its token died
    assert await first.get_token() == "tok-B"
    assert route.call_count == 2


@respx.mock
async def test_throttle_is_honoured_across_processes(
    http: httpx.AsyncClient, store: MemoryStore, clock: FakeClock
) -> None:
    respx.post(f"{BASE_URL}/generate/token").mock(
        side_effect=[
            httpx.Response(200, json={"token": "tok-A"}),
            httpx.Response(200, json={"token": "tok-B"}),
        ]
    )
    first = TokenProvider(http=http, store=store, clock=clock)
    await first.get_token()
    clock.advance(10)
    second = TokenProvider(http=http, store=store, clock=clock)
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    import tradedesk.broker.indstocks.auth as auth_mod

    orig = auth_mod.asyncio.sleep
    auth_mod.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        assert await second.refresh() == "tok-B"
    finally:
        auth_mod.asyncio.sleep = orig  # type: ignore[assignment]
    assert slept and 49 <= slept[0] <= 50  # waited out the remainder of the 60 s gap


@respx.mock
async def test_concurrent_providers_generate_once_and_share_the_winner(
    http: httpx.AsyncClient,
    store: MemoryStore,
    clock: FakeClock,
    tmp_path,
) -> None:
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(200, json={"token": "shared-token"})
    )
    lock_path = tmp_path / "token-generation.lock"

    def shared_lock():
        return token_generation_lock(lock_path)

    first = TokenProvider(
        http=http, store=store, clock=clock, generation_lock=shared_lock
    )
    second = TokenProvider(
        http=http, store=store, clock=clock, generation_lock=shared_lock
    )
    tokens = await asyncio.gather(first.get_token(), second.get_token())

    assert tokens == ["shared-token", "shared-token"]
    assert route.call_count == 1
    assert store.get(KEY_TOKEN) == "shared-token"
    assert store.get(KEY_TOKEN_ISSUED_AT) is not None


@respx.mock
async def test_generation_lock_releases_after_failure(
    http: httpx.AsyncClient,
    store: MemoryStore,
    clock: FakeClock,
    tmp_path,
) -> None:
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        side_effect=[
            httpx.Response(401, json={"message": "Invalid TOTP"}),
            httpx.Response(200, json={"token": "recovered-token"}),
        ]
    )
    lock_path = tmp_path / "token-generation.lock"

    def shared_lock():
        return token_generation_lock(lock_path)

    first = TokenProvider(
        http=http, store=store, clock=clock, generation_lock=shared_lock
    )
    with pytest.raises(TokenGenerationError, match="Invalid TOTP"):
        await first.get_token()

    clock.advance(61)
    second = TokenProvider(
        http=http, store=store, clock=clock, generation_lock=shared_lock
    )
    assert await second.get_token() == "recovered-token"
    assert route.call_count == 2


@respx.mock
async def test_concurrent_rejected_token_refresh_adopts_first_winner(
    http: httpx.AsyncClient,
    store: MemoryStore,
    clock: FakeClock,
    tmp_path,
) -> None:
    store.set(KEY_TOKEN, "rejected-token")
    store.set(KEY_TOKEN_ISSUED_AT, repr(clock()))
    route = respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(200, json={"token": "replacement-token"})
    )
    lock_path = tmp_path / "token-generation.lock"

    def shared_lock():
        return token_generation_lock(lock_path)

    first = TokenProvider(
        http=http, store=store, clock=clock, generation_lock=shared_lock
    )
    second = TokenProvider(
        http=http, store=store, clock=clock, generation_lock=shared_lock
    )
    tokens = await asyncio.gather(
        first.refresh(rejected_token="rejected-token"),
        second.refresh(rejected_token="rejected-token"),
    )

    assert tokens == ["replacement-token", "replacement-token"]
    assert route.call_count == 1
    assert store.get(KEY_TOKEN) == "replacement-token"
