from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.broker.indstocks.rest import BASE_URL, ApiError, IndstocksClient

HIST = f"{BASE_URL}/market/historical"


def candle(ts: datetime, px: float = 100.0) -> dict[str, Any]:
    return {"ts": int(ts.timestamp()), "o": px, "h": px + 1, "l": px - 1, "c": px, "v": 10}


@respx.mock
async def test_profile_uses_authorization_header(client: IndstocksClient) -> None:
    route = respx.get(f"{BASE_URL}/user/profile").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"user_id": "u1", "ucc": "ABC"}}
        )
    )
    p = await client.profile()
    assert p.user_id == "u1" and p.ucc == "ABC"
    assert route.calls[0].request.headers["Authorization"] == "tok-0"


@respx.mock
async def test_candles_single_window_parses_ts_as_seconds(client: IndstocksClient) -> None:
    t = datetime(2026, 9, 10, 9, 15, tzinfo=IST)
    route = respx.get(f"{HIST}/15minute").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "data": {"NSE_3045": {"candles": [candle(t, 788.8)]}}},
        )
    )
    got = await client.candles(Interval.M15, ["NSE_3045", "NSE_2885"], t, t + timedelta(days=1))
    c = got["NSE_3045"][0]
    assert c.ts == t and c.close == 788.8 and c.volume == 10
    assert c.close_time == t + timedelta(minutes=15)
    assert got["NSE_2885"] == []  # absent from data => no candles, not an error
    params = dict(route.calls[0].request.url.params)
    assert params["scrip-codes"] == "NSE_3045,NSE_2885"
    assert params["start_time"] == str(int(t.timestamp() * 1000))  # request times are ms


async def test_candles_rejects_more_than_five_codes(client: IndstocksClient) -> None:
    t = datetime(2026, 9, 10, tzinfo=IST)
    with pytest.raises(ValueError, match="at most 5"):
        await client.candles(Interval.D1, [f"NSE_{i}" for i in range(6)], t, t)


@respx.mock
async def test_history_pages_backwards_in_max_windows_and_dedups(
    client: IndstocksClient,
) -> None:
    # 20 days of 15-minute candles → max window is 7 days → 3 calls, walking backwards.
    end = datetime(2026, 9, 11, tzinfo=IST)
    start = end - timedelta(days=20)
    seen_windows: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        s, e = int(p["start_time"]), int(p["end_time"])
        seen_windows.append((s, e))
        # Server returns one candle at the window start, plus a duplicate straddling
        # the boundary and one *outside* the window (doc: wider requests get max window).
        ts0 = datetime.fromtimestamp(s / 1000, tz=IST)
        candles = [candle(ts0), candle(ts0 + timedelta(days=1)), candle(end + timedelta(days=1))]
        return httpx.Response(200, json={"success": True, "data": {"NSE_1": {"candles": candles}}})

    respx.get(f"{HIST}/15minute").mock(side_effect=handler)
    got = await client.candles_history(Interval.M15, ["NSE_1"], start, end)

    assert len(seen_windows) == 3
    widths = [(e - s) / 86_400_000 for s, e in seen_windows]
    assert widths == [7, 7, 6]  # last window is the remainder
    assert seen_windows[0][1] == int(end.timestamp() * 1000)  # first call ends at `end`
    assert seen_windows[-1][0] == int(start.timestamp() * 1000)  # last call starts at `start`
    ts = [c.ts for c in got["NSE_1"]]
    assert ts == sorted(ts) and len(ts) == len(set(ts))
    assert all(start <= t < end for t in ts)  # out-of-window candle dropped


@respx.mock
async def test_history_batches_codes_five_at_a_time(client: IndstocksClient) -> None:
    end = datetime(2026, 9, 11, tzinfo=IST)
    codes = [f"NSE_{i}" for i in range(7)]
    route = respx.get(f"{HIST}/1day").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {}})
    )
    got = await client.candles_history(Interval.D1, codes, end - timedelta(days=30), end)
    assert route.call_count == 2
    assert set(got) == set(codes)
    first = route.calls[0].request.url.params["scrip-codes"].split(",")
    assert len(first) == 5


@respx.mock
async def test_quotes_full_chunks_at_1000(client: IndstocksClient) -> None:
    codes = [f"NSE_{i}" for i in range(1001)]

    def handler(request: httpx.Request) -> httpx.Response:
        batch = request.url.params["scrip-codes"].split(",")
        data = {
            c: {"live_price": 1.5, "volume": 7, "52week_high": 9.0, "market_depth": {}}
            for c in batch
        }
        return httpx.Response(200, json={"status": "success", "data": data})

    route = respx.get(f"{BASE_URL}/market/quotes/full").mock(side_effect=handler)
    got = await client.quotes_full(codes)
    assert route.call_count == 2
    assert len(got) == 1001
    q = got["NSE_0"]
    assert q.live_price == 1.5 and q.volume == 7 and q.week52_high == 9.0


@respx.mock
async def test_429_backs_off_then_succeeds(client: IndstocksClient, sleeps: list[float]) -> None:
    route = respx.get(f"{BASE_URL}/market/quotes/ltp").mock(
        side_effect=[
            httpx.Response(429, json={"error": "Rate limit exceeded", "success": False}),
            httpx.Response(503, json={"status": "error", "error_type": "NetworkException"}),
            httpx.Response(200, json={"status": "success", "data": {"NSE_1": {"live_price": 2}}}),
        ]
    )
    got = await client.quotes_ltp(["NSE_1"])
    assert got["NSE_1"].live_price == 2.0
    assert route.call_count == 3
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]  # exponential


@respx.mock
async def test_400_is_not_retried(client: IndstocksClient, sleeps: list[float]) -> None:
    route = respx.get(f"{HIST}/1day").mock(
        return_value=httpx.Response(
            400, json={"debug_info": "invalid interval.", "message": "Bad request"}
        )
    )
    t = datetime(2026, 9, 10, tzinfo=IST)
    with pytest.raises(ApiError, match="invalid interval") as exc:
        await client.candles(Interval.D1, ["NSE_1"], t, t + timedelta(days=1))
    assert exc.value.status == 400
    assert route.call_count == 1 and sleeps == []


@respx.mock
async def test_403_refreshes_token_once(client: IndstocksClient) -> None:
    respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(200, json={"token": "tok-1"})
    )
    route = respx.get(f"{BASE_URL}/funds").mock(
        side_effect=[
            httpx.Response(403, json={"status": "error", "error_type": "TokenException"}),
            httpx.Response(200, json={"status": "success", "data": {"eq_charges": 12.5}}),
        ]
    )
    funds = await client.funds()
    assert funds.eq_charges == 12.5
    assert route.calls[0].request.headers["Authorization"] == "tok-0"
    assert route.calls[1].request.headers["Authorization"] == "tok-1"


@respx.mock
async def test_403_twice_raises(client: IndstocksClient) -> None:
    respx.post(f"{BASE_URL}/generate/token").mock(
        return_value=httpx.Response(200, json={"token": "tok-1"})
    )
    respx.get(f"{BASE_URL}/funds").mock(
        return_value=httpx.Response(403, json={"status": "error", "error_type": "TokenException"})
    )
    with pytest.raises(ApiError) as exc:
        await client.funds()
    assert exc.value.error_type == "TokenException"


@respx.mock
async def test_success_false_envelope_raises(client: IndstocksClient) -> None:
    respx.get(f"{BASE_URL}/market/quotes/ltp").mock(
        return_value=httpx.Response(200, json={"message": "invalid scrip", "success": False})
    )
    with pytest.raises(ApiError, match="invalid scrip"):
        await client.quotes_ltp(["NSE_X"])


@respx.mock
async def test_instruments_csv_is_returned_raw(client: IndstocksClient) -> None:
    csv = "EXCH,SEGMENT,SECURITY_ID\nNSE,NIFTY 50,40000001\n"
    respx.get(f"{BASE_URL}/market/instruments").mock(
        return_value=httpx.Response(200, text=csv, headers={"content-type": "text/csv"})
    )
    idx = await client.index_instruments()
    assert idx[0].name == "NIFTY 50" and idx[0].scrip_code == "NSE_40000001"
    with pytest.raises(ValueError):
        await client.instruments_csv("bonds")
