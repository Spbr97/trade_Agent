"""CoinDCX adapter tests (M13 plan Phase 2). Fixtures mirror real responses observed
2026-09-12 - see broker/coindcx/models.py for the verified facts these encode:
- markets_details rows use real observed field names/values (one INR pair, one non-INR,
  one inactive, to prove the filter).
- candles rows use the millisecond `time` field, newest-first order, fractional volume.
- the "startTime+endTime together or both ignored" quirk is the most important thing
  tested here: test_candles_history_always_sends_both_time_params_together locks it in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from tradedesk.broker.coindcx.instruments import parse_markets_details
from tradedesk.broker.coindcx.models import (
    BASE_URL,
    candle_from_api,
    coindcx_interval,
    coindcx_name_from_pair,
    scrip_code_for_pair,
)
from tradedesk.broker.coindcx.rest import CoinDcxClient, CoinDcxError
from tradedesk.broker.indstocks.models import Interval

CANDLES = f"{BASE_URL}/market_data/candles"
MARKETS = f"{BASE_URL}/exchange/v1/markets_details"


@pytest.fixture
async def client() -> CoinDcxClient:
    async with CoinDcxClient(httpx.AsyncClient(base_url=BASE_URL, timeout=5.0)) as c:
        yield c


def candle_row(ts: datetime, px: float = 5_000_000.0, volume: float = 4.3) -> dict[str, Any]:
    return {
        "open": px, "high": px + 10000, "low": px - 10000, "close": px,
        "volume": volume, "time": int(ts.timestamp() * 1000),
    }  # fmt: skip


# ------------------------------------------------------------------------- models


@pytest.mark.parametrize(
    ("pair", "name"),
    [
        ("I-BTC_INR", "BTCINR"),
        ("I-DOT_INR", "DOTINR"),
        ("I-IMX_INR", "IMXINR"),
        ("I-NKN_INR", "NKNINR"),
    ],
)
def test_coindcx_name_from_pair_matches_real_observed_pairs(pair: str, name: str) -> None:
    assert coindcx_name_from_pair(pair) == name
    assert scrip_code_for_pair(pair) == f"CDX_{name}"


def test_coindcx_interval_maps_only_supported_intervals() -> None:
    assert coindcx_interval(Interval.D1) == "1d"
    assert coindcx_interval(Interval.M15) == "15m"
    assert coindcx_interval(Interval.H1) == "1h"
    assert coindcx_interval(Interval.M1) == "1m"
    with pytest.raises(ValueError, match="no equivalent"):
        coindcx_interval(Interval.W1)


def test_candle_from_api_parses_ms_time_and_rounds_fractional_volume() -> None:
    ts = datetime(2026, 9, 11, tzinfo=UTC)
    row = candle_row(ts, px=7_700_000.0, volume=4.6)
    c = candle_from_api("CDX_BTCINR", Interval.D1, row)
    assert c.ts == ts and c.close == 7_700_000.0
    assert c.volume == 5  # rounded, not truncated - 4.6 must not become 4


# --------------------------------------------------------------------- instruments


def test_parse_markets_details_filters_active_inr_pairs() -> None:
    raw = [
        {"coindcx_name": "BTCINR", "pair": "I-BTC_INR", "base_currency_short_name": "INR",
         "target_currency_name": "Bitcoin", "step": 0.01, "status": "active"},
        {"coindcx_name": "BTCUSDT", "pair": "B-BTC_USDT", "base_currency_short_name": "USDT",
         "target_currency_name": "Bitcoin", "step": 0.01, "status": "active"},
        {"coindcx_name": "OLDINR", "pair": "I-OLD_INR", "base_currency_short_name": "INR",
         "target_currency_name": "Old Coin", "step": 1.0, "status": "inactive"},
    ]  # fmt: skip
    out = parse_markets_details(raw)
    assert [i.trading_symbol for i in out] == ["BTCINR"]
    inst = out[0]
    assert inst.scrip_code == "CDX_BTCINR"
    assert inst.custom_symbol == "I-BTC_INR"
    assert inst.tick_size == 0.01
    assert inst.symbol_name == "Bitcoin"


def test_parse_markets_details_skips_rows_missing_identifiers() -> None:
    raw = [{"base_currency_short_name": "INR", "status": "active"}]  # no coindcx_name/pair
    assert parse_markets_details(raw) == []


# --------------------------------------------------------------------------- rest


@respx.mock
async def test_candles_sorts_ascending_and_labels_scrip_code(client: CoinDcxClient) -> None:
    """The API returns newest-first; the store needs ascending."""
    t0 = datetime(2026, 9, 9, tzinfo=UTC)
    t1 = datetime(2026, 9, 10, tzinfo=UTC)
    t2 = datetime(2026, 9, 11, tzinfo=UTC)
    respx.get(CANDLES).mock(
        return_value=httpx.Response(
            200,
            json=[candle_row(t2), candle_row(t1), candle_row(t0)],  # newest first
        )
    )
    got = await client.candles(Interval.D1, "I-BTC_INR", t0, t2 + timedelta(days=1))
    assert [c.ts for c in got] == [t0, t1, t2]
    assert all(c.scrip_code == "CDX_BTCINR" for c in got)


@respx.mock
async def test_candles_sends_pair_interval_limit_and_ms_times(client: CoinDcxClient) -> None:
    route = respx.get(CANDLES).mock(return_value=httpx.Response(200, json=[]))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    await client.candles(Interval.D1, "I-BTC_INR", start, end)
    params = dict(route.calls[0].request.url.params)
    assert params["pair"] == "I-BTC_INR"
    assert params["interval"] == "1d"
    assert params["limit"] == "1000"
    assert params["startTime"] == str(int(start.timestamp() * 1000))
    assert params["endTime"] == str(int(end.timestamp() * 1000))


@respx.mock
async def test_candles_raises_on_non_2xx(client: CoinDcxClient) -> None:
    respx.get(CANDLES).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(CoinDcxError, match="500"):
        await client.candles(
            Interval.D1,
            "I-BTC_INR",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )


@respx.mock
async def test_candles_history_always_sends_both_time_params_together(
    client: CoinDcxClient,
) -> None:
    """The critical CoinDCX quirk (models.py): startTime/endTime are silently ignored
    unless BOTH are present. Every call this makes, across every page, must carry both."""
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        seen.append(p)
        return httpx.Response(200, json=[])

    respx.get(CANDLES).mock(side_effect=handler)
    end = datetime(2026, 9, 11, tzinfo=UTC)
    start = end - timedelta(days=5)
    await client.candles_history_by_pair(Interval.D1, ["I-BTC_INR"], start, end)
    assert seen  # at least one call happened
    for p in seen:
        assert "startTime" in p and "endTime" in p


@respx.mock
async def test_candles_history_pages_backwards_and_stops_on_empty_page(
    client: CoinDcxClient,
) -> None:
    """1000-bar windows for a daily interval; a pair with 5 days of real history should
    take one page and then stop at the first empty response, not loop until `start`."""
    end = datetime(2026, 9, 11, tzinfo=UTC)
    start = end - timedelta(days=3650)  # far more than this fake pair actually has
    real_days = [end - timedelta(days=i) for i in range(1, 4)]  # 3 real days, `end` itself excluded
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        p = request.url.params
        win_start = datetime.fromtimestamp(int(p["startTime"]) / 1000, tz=UTC)
        win_end = datetime.fromtimestamp(int(p["endTime"]) / 1000, tz=UTC)
        rows = [candle_row(d) for d in real_days if win_start <= d < win_end]
        return httpx.Response(200, json=rows)

    respx.get(CANDLES).mock(side_effect=handler)
    got = await client.candles_history_by_pair(Interval.D1, ["I-BTC_INR"], start, end)
    assert calls["n"] == 2  # one page with data, one empty page that stops the loop
    ts = [c.ts for c in got["I-BTC_INR"]]
    assert len(ts) == 3 and ts == sorted(ts)


@respx.mock
async def test_candles_history_dedups_overlapping_boundary_bars(client: CoinDcxClient) -> None:
    """Consecutive pages share their boundary bar (win_end of page N == win_start of page
    N-1); the fake server here echoes that boundary candle into BOTH pages (deliberately
    wider than [win_start, win_end) by one bar), matching INDstocks' candles_history
    de-dup-by-open-time contract and test shape."""
    end = datetime(2026, 9, 11, tzinfo=UTC)
    start = end - timedelta(days=4000)  # forces multiple 1000-bar pages
    real_start = end - timedelta(days=1500)

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        win_start = datetime.fromtimestamp(int(p["startTime"]) / 1000, tz=UTC)
        win_end = datetime.fromtimestamp(int(p["endTime"]) / 1000, tz=UTC)
        lo = max(win_start, real_start)
        rows = []
        d = lo
        while d <= win_end:  # <= : one bar past win_end, duplicating the next page's edge
            rows.append(candle_row(d))
            d += timedelta(days=1)
        return httpx.Response(200, json=rows)

    respx.get(CANDLES).mock(side_effect=handler)
    got = await client.candles_history_by_pair(Interval.D1, ["I-BTC_INR"], start, end)
    ts = [c.ts for c in got["I-BTC_INR"]]
    assert ts == sorted(ts) and len(ts) == len(set(ts))
    assert ts[0] >= real_start
    assert all(start <= t < end for t in ts)  # the extra echoed bar never leaks past `end`


@respx.mock
async def test_instruments_end_to_end(client: CoinDcxClient) -> None:
    respx.get(MARKETS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "coindcx_name": "BTCINR",
                    "pair": "I-BTC_INR",
                    "base_currency_short_name": "INR",
                    "target_currency_name": "Bitcoin",
                    "step": 0.01,
                    "status": "active",
                },
            ],
        )  # fmt: skip
    )
    instruments = await client.instruments()
    assert [i.scrip_code for i in instruments] == ["CDX_BTCINR"]


# --------------------------------------------- candles_history (scrip-code keyed)


@respx.mock
async def test_candles_history_resolves_scrip_codes_to_pairs(client: CoinDcxClient) -> None:
    """The bug this test exists to catch: data/history_loader.py (and the `data load
    --market crypto` CLI path) call candles_history() with tradedesk scrip codes, not
    CoinDCX pairs - a real bug found running `tradedesk data load --market crypto`
    against the live API (every call 422'd, "Invalid pair CDX_BTCINR"), not caught by
    the pair-keyed tests above."""
    t0 = datetime(2026, 9, 10, tzinfo=UTC)
    t1 = datetime(2026, 9, 11, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["pair"]
        assert pair in ("I-BTC_INR", "I-DOT_INR")  # never the raw scrip code
        d = t0 if pair == "I-BTC_INR" else t1
        return httpx.Response(200, json=[candle_row(d)])

    respx.get(CANDLES).mock(side_effect=handler)
    client.register_pairs({"CDX_BTCINR": "I-BTC_INR", "CDX_DOTINR": "I-DOT_INR"})
    got = await client.candles_history(
        Interval.D1, ["CDX_BTCINR", "CDX_DOTINR"], t0, t1 + timedelta(days=1)
    )
    assert set(got) == {"CDX_BTCINR", "CDX_DOTINR"}
    assert [c.ts for c in got["CDX_BTCINR"]] == [t0]
    assert [c.ts for c in got["CDX_DOTINR"]] == [t1]


async def test_candles_history_unregistered_code_returns_empty_not_an_error(
    client: CoinDcxClient,
) -> None:
    """A code with no registered pair (e.g. delisted, or sync-instruments never ran)
    degrades the same way an unknown code does elsewhere - empty history, no crash."""
    got = await client.candles_history(
        Interval.D1,
        ["CDX_NOPE"],
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert got == {"CDX_NOPE": []}


def test_register_pairs_constructor_arg_and_method_both_populate_the_map() -> None:
    c1 = CoinDcxClient(pair_for_code={"CDX_BTCINR": "I-BTC_INR"})
    assert c1.pair_for_code == {"CDX_BTCINR": "I-BTC_INR"}
    c1.register_pairs({"CDX_DOTINR": "I-DOT_INR"})
    assert c1.pair_for_code == {"CDX_BTCINR": "I-BTC_INR", "CDX_DOTINR": "I-DOT_INR"}
