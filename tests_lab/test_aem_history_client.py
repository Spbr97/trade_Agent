from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from tradedesk_lab.aem_history_client import (
    CachedTokenProvider,
    HistoryApiError,
    HistoryAuthenticationError,
    HistoryBudgetExceeded,
    HistoryClient,
    HistoryClientError,
    HistoryResponseError,
)

from tradedesk.broker.indstocks.auth import KEY_TOKEN, KEY_TOKEN_ISSUED_AT, TOKEN_TTL_SECONDS
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.broker.indstocks.ratelimit import Category, RateLimiter
from tradedesk.broker.indstocks.rest import BASE_URL

START = datetime(2026, 9, 10, 9, 15, tzinfo=IST)
END = START + timedelta(days=7)
NOW = START.timestamp()
TOKEN = "test-secret-never-echo"


class ReadOnlyStore:
    def __init__(self, token=TOKEN, issued=str(NOW - 60)):
        self.values = {KEY_TOKEN: token, KEY_TOKEN_ISSUED_AT: issued}
        self.reads: list[str] = []

    def get(self, key):
        self.reads.append(key)
        assert key in {KEY_TOKEN, KEY_TOKEN_ISSUED_AT}, "generation credential was read"
        return self.values[key]

    def set(self, *args):
        pytest.fail("credential write attempted")

    def delete(self, *args):
        pytest.fail("credential deletion attempted")


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration
        await asyncio.sleep(0)


def raw_candle(**changes):
    return {"ts": int(START.timestamp()), "o": 100, "h": 101, "l": 99, "c": 100, "v": 10} | changes


def payload(candles=None):
    return {
        "success": True,
        "data": {"NSE_3045": {"candles": [raw_candle()] if candles is None else candles}},
    }


def client_for(handler, *, store=None, budget=3, limiter=None, fake=None, follow_redirects=False):
    fake = FakeClock() if fake is None else fake
    http = httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
        follow_redirects=follow_redirects,
    )
    client = HistoryClient(
        max_requests=budget,
        store=ReadOnlyStore() if store is None else store,
        http=http,
        clock=lambda: NOW,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        limiter=limiter,
    )
    return client, http, fake


async def test_reuses_production_candle_contract_and_limiter():
    requests = []
    store, limiter = ReadOnlyStore(), RateLimiter()

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    client, http, _ = client_for(handler, store=store, limiter=limiter)
    async with http, client:
        result = await client.candles(["NSE_3045", "NSE_2885"], START, END)
    assert result["NSE_3045"][0].ts == START
    assert result["NSE_3045"][0].interval == Interval.M1
    assert result["NSE_2885"] == []
    assert requests[0].url.path == "/market/historical/1minute"
    assert requests[0].method == "GET"
    assert requests[0].headers["Authorization"] == TOKEN
    assert dict(requests[0].url.params) == {
        "scrip-codes": "NSE_3045,NSE_2885",
        "start_time": str(int(START.timestamp() * 1000)),
        "end_time": str(int(END.timestamp() * 1000)),
    }
    assert client.request_count == limiter.used_today(Category.DATA) == 1
    assert client.remaining_requests == 2
    assert store.reads == [KEY_TOKEN, KEY_TOKEN_ISSUED_AT]


@pytest.mark.parametrize(
    "issued",
    [
        None,
        "",
        "bad",
        "nan",
        "inf",
        "-inf",
        "-1",
        "0",
        True,
        str(NOW + 1),
        str(NOW - TOKEN_TTL_SECONDS),
    ],
)
async def test_cached_provider_rejects_unusable_issued_time_without_writes(issued):
    provider = CachedTokenProvider(store=ReadOnlyStore(issued=issued), clock=lambda: NOW)
    with pytest.raises(HistoryAuthenticationError) as caught:
        await provider.get_token()
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("token", [None, "", " ", "secret\nheader", "secret\rheader", "sécret"])
async def test_cached_provider_rejects_missing_or_invalid_token(token):
    with pytest.raises(HistoryAuthenticationError):
        await CachedTokenProvider(store=ReadOnlyStore(token=token), clock=lambda: NOW).get_token()


async def test_cached_provider_never_refreshes_and_reloads_current_cached_token():
    store = ReadOnlyStore()
    provider = CachedTokenProvider(store=store, clock=lambda: NOW)
    assert await provider.get_token() == TOKEN
    store.values[KEY_TOKEN] = "new-cached-token"
    assert await provider.get_token() == "new-cached-token"
    with pytest.raises(HistoryAuthenticationError, match="refresh is disabled"):
        await provider.refresh()
    assert set(store.reads) == {KEY_TOKEN, KEY_TOKEN_ISSUED_AT}


async def test_keyring_exception_is_sanitized():
    class BrokenStore:
        def get(self, key):
            raise RuntimeError(TOKEN)

    with pytest.raises(HistoryAuthenticationError) as caught:
        await CachedTokenProvider(store=BrokenStore(), clock=lambda: NOW).get_token()
    assert TOKEN not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_missing_auth_sends_no_request_and_spends_no_budget():
    requests = []
    limiter = RateLimiter()
    client, http, _ = client_for(
        lambda r: requests.append(r), store=ReadOnlyStore(token=None), limiter=limiter
    )
    async with http, client:
        with pytest.raises(HistoryAuthenticationError):
            await client.candles(["NSE_3045"], START, END)
    assert requests == []
    assert client.request_count == limiter.used_today(Category.DATA) == 0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
async def test_http_failures_are_sanitized_counted_and_never_retried(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"message": TOKEN, "error_type": TOKEN})

    client, http, fake = client_for(handler)
    async with http, client:
        error = HistoryAuthenticationError if status in (401, 403) else HistoryApiError
        with pytest.raises(error) as caught:
            await client.candles(["NSE_3045"], START, END)
    assert TOKEN not in str(caught.value)
    assert len(requests) == client.request_count == 1
    assert fake.sleeps == []


async def test_transport_failure_consumes_budget_and_never_retries_or_echoes_request():
    def handler(request):
        raise httpx.ConnectError(TOKEN, request=request)

    client, http, _ = client_for(handler, budget=1)
    async with http, client:
        with pytest.raises(HistoryClientError) as caught:
            await client.candles(["NSE_3045"], START, END)
        assert TOKEN not in str(caught.value)
        with pytest.raises(HistoryBudgetExceeded):
            await client.candles(["NSE_3045"], START, END)
    assert client.request_count == 1 and client.remaining_requests == 0


async def test_each_attempt_is_serialized_paced_and_hard_capped():
    fake, observed = FakeClock(), []
    limiter = RateLimiter()

    def handler(request):
        observed.append(fake.now)
        return httpx.Response(200, json=payload())

    client, http, _ = client_for(handler, budget=3, fake=fake, limiter=limiter)
    async with http, client:
        await asyncio.gather(*(client.candles(["NSE_3045"], START, END) for _ in range(3)))
        with pytest.raises(HistoryBudgetExceeded):
            await client.candles(["NSE_3045"], START, END)
    assert observed == [100.0, 101.0, 102.0]
    assert fake.sleeps == [1.0, 1.0]
    assert limiter.used_today(Category.DATA) == client.request_count == 3


async def test_manual_retry_is_also_paced_and_counted():
    fake, starts = FakeClock(), []

    def handler(request):
        starts.append(fake.now)
        return httpx.Response(503 if len(starts) == 1 else 200, json=payload())

    client, http, _ = client_for(handler, fake=fake)
    async with http, client:
        with pytest.raises(HistoryApiError):
            await client.candles(["NSE_3045"], START, END)
        await client.candles(["NSE_3045"], START, END)
    assert starts == [100.0, 101.0] and client.request_count == 2


async def test_redirect_is_never_followed_even_when_injected_http_enables_it():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/generate/token"})

    client, http, _ = client_for(handler, follow_redirects=True)
    async with http, client:
        with pytest.raises(HistoryApiError):
            await client.candles(["NSE_3045"], START, END)
    assert len(requests) == client.request_count == 1


@pytest.mark.parametrize(
    "codes,start,end",
    [
        ([], START, END),
        (["NSE_1"] * 6, START, END),
        (["NSE_1", "NSE_1"], START, END),
        ("NSE_1", START, END),
        (["BSE_1"], START, END),
        (["NSE_bad"], START, END),
        (["NSE_1"], START, START),
        (["NSE_1"], END, START),
        (["NSE_1"], START, END + timedelta(seconds=1)),
        (["NSE_1"], START.replace(tzinfo=None), END),
        (["NSE_1"], START, "2026-09-20"),
    ],
)
async def test_invalid_request_never_reads_auth_or_calls_transport(codes, start, end):
    store, requests = ReadOnlyStore(), []
    client, http, _ = client_for(lambda r: requests.append(r), store=store)
    async with http, client:
        with pytest.raises(ValueError):
            await client.candles(codes, start, end)
    assert not requests and not store.reads and client.request_count == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"ts": START.timestamp() + 0.5},
        {"ts": START.timestamp() + 1},
        {"ts": int(START.timestamp() * 1000)},
        {"ts": "1789002900"},
        {"ts": float("nan")},
        {"ts": True},
        {"v": 10.5},
        {"v": "10"},
        {"v": -1},
        {"v": float("inf")},
        {"v": True},
        {"v": 2**63},
        {"v": 2**53 + 1},
        {"o": "100"},
        {"o": float("nan")},
        {"o": True},
        {"o": 0},
        {"h": 99},
        {"l": 101},
    ],
)
async def test_invalid_raw_numbers_rejected_before_parser_coercion(changes):
    # json.dumps permits non-finite literals so the decoder/validator is exercised.
    body = json.dumps(payload([raw_candle(**changes)]))
    client, http, _ = client_for(lambda _: httpx.Response(200, content=body))
    async with http, client:
        with pytest.raises(HistoryResponseError):
            await client.candles(["NSE_3045"], START, END)
    assert client.request_count == 1


@pytest.mark.parametrize(
    "body",
    [
        {"success": "true", "data": {}},
        {"success": False, "message": TOKEN},
        {"success": True, "data": None},
        {"success": True},
        [],
        {"success": True, "data": {"NSE_999": {"candles": []}}},
        {"success": True, "data": {"NSE_3045": {"candles": None}}},
        payload([raw_candle(), raw_candle(c=100.5)]),
    ],
)
async def test_bad_envelope_and_conflicting_duplicates_rejected_without_echo(body):
    client, http, _ = client_for(lambda _: httpx.Response(200, json=body))
    async with http, client:
        with pytest.raises(HistoryResponseError) as caught:
            await client.candles(["NSE_3045"], START, END)
    assert TOKEN not in str(caught.value)


async def test_malformed_json_rejected_without_echo():
    client, http, _ = client_for(lambda _: httpx.Response(200, text=TOKEN))
    async with http, client:
        with pytest.raises(HistoryResponseError) as caught:
            await client.candles(["NSE_3045"], START, END)
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("budget", [0, -1, 100001, True, 1.5, "5"])
def test_budget_requires_a_positive_bounded_integer(budget):
    with pytest.raises(ValueError):
        HistoryClient(max_requests=budget)


async def test_closed_client_refuses_further_requests():
    client, http, _ = client_for(lambda _: httpx.Response(200, json=payload()))
    async with http:
        await client.aclose()
        with pytest.raises(HistoryClientError, match="closed"):
            await client.candles(["NSE_3045"], START, END)
    assert client.request_count == 0


async def test_request_codes_are_frozen_during_an_inflight_request():
    codes = ["NSE_3045"]

    async def handler(request):
        codes[:] = ["NSE_999"]
        return httpx.Response(200, json=payload())

    client, http, _ = client_for(handler)
    async with http, client:
        result = await client.candles(codes, START, END)
    assert list(result) == ["NSE_3045"]
    assert len(result["NSE_3045"]) == 1


async def test_window_limit_measures_actual_epoch_span_not_dst_wall_time():
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 10, 29, tzinfo=zone)
    end = start + timedelta(days=7)  # Clock rollback makes this 169 actual hours.
    store = ReadOnlyStore()
    client, http, _ = client_for(lambda _: pytest.fail("unexpected request"), store=store)
    async with http, client:
        with pytest.raises(ValueError, match="seven days"):
            await client.candles(["NSE_3045"], start, end)
    assert store.reads == [] and client.request_count == 0


async def test_submillisecond_window_is_rejected_before_truncating_to_epoch_ms():
    client, http, _ = client_for(lambda _: pytest.fail("unexpected request"))
    async with http, client:
        with pytest.raises(ValueError, match="epoch milliseconds"):
            await client.candles(["NSE_3045"], START, START + timedelta(microseconds=1))
    assert client.request_count == 0


async def test_token_expiring_during_pacing_is_not_sent():
    fake = FakeClock()
    store = ReadOnlyStore(issued=str(NOW - TOKEN_TTL_SECONDS + 0.5))
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    client = HistoryClient(
        max_requests=3,
        store=store,
        http=http,
        clock=lambda: NOW + fake.now - 100,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    async with http, client:
        await client.candles(["NSE_3045"], START, END)
        with pytest.raises(HistoryAuthenticationError):
            await client.candles(["NSE_3045"], START, END)
    assert fake.sleeps == [1.0]
    assert len(requests) == client.request_count == 1


async def test_owned_http_is_closed_and_injected_http_remains_callers_responsibility():
    owned = HistoryClient(max_requests=1, store=ReadOnlyStore())
    await owned.aclose()
    assert owned._client.http.is_closed
    borrowed, http, _ = client_for(lambda _: pytest.fail("unexpected request"))
    await borrowed.aclose()
    assert not http.is_closed
    await http.aclose()
