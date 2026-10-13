"""Phase 0 live smoke test: fetch real MCX historical candles via Groww, print the raw
response so docs/groww-api.md's PROVISIONAL endpoint/param/response notes can be corrected
against reality. Not production code - deleted or promoted into broker/groww/rest.py once the
real shape is confirmed."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import httpx

from tradedesk.broker.groww.auth import BASE_URL, TokenProvider


async def try_one(http: httpx.AsyncClient, token: str, label: str, path: str, params: dict) -> None:  # noqa: E501
    print(f"\n=== {label}: {path} params={params} ===")
    resp = await http.get(
        path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        },
        params=params,
    )
    print("status:", resp.status_code)
    try:
        print(json.dumps(resp.json(), indent=2)[:2500])
    except ValueError:
        print(resp.text[:1500])


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as http:
        token = await TokenProvider(http=http).get_token()
        end = date.today()
        start = end - timedelta(days=14)
        common = {
            "start_time": start.isoformat() + " 00:00:00",
            "end_time": end.isoformat() + " 23:59:59",
        }
        for interval in ("1day", "day", "1d"):
            await try_one(
                http, token, f"NSE control (RELIANCE) interval={interval}",
                "/v1/historical/candles",
                {**common, "exchange": "NSE", "segment": "CASH",
                 "groww_symbol": "NSE-RELIANCE", "candle_interval": interval},
            )
        await try_one(
            http, token, "MCX (CRUDEOIL fut) interval=1day", "/v1/historical/candles",
            {**common, "exchange": "MCX", "segment": "COMMODITY",
             "groww_symbol": "MCX-CRUDEOIL-19Oct26-FUT", "candle_interval": "1day"},
        )


if __name__ == "__main__":
    asyncio.run(main())
