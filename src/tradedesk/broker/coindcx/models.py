"""CoinDCX facts verified live, 2026-09-12 (docs.coindcx.com + direct probing - the docs
page for the candles endpoint is thin, so behaviour was checked against the real API):

- Base URL https://api.coindcx.com (public.coindcx.com serves the identical response;
  the docs name api.coindcx.com so that's what the client uses).
- No API key, no User-Agent spoofing needed for any endpoint used here (unlike NSE's
  website - see docs/indstocks-api.md's login gotcha; CoinDCX's plain default httpx UA
  works for both markets_details and candles).
- GET /exchange/v1/markets_details: all markets (active and inactive, every quote
  currency). 997 rows observed; 338 active INR pairs. Field `pair` (e.g. "I-BTC_INR") is
  what /market_data/candles wants; `coindcx_name` (e.g. "BTCINR") is the short form used
  for display and as this market's scrip-code suffix.
- GET /market_data/candles?pair=...&interval=...&limit=...&startTime=...&endTime=...:
    * `interval`: confirmed working values "1m", "15m", "1h", "1d" (per docs; matches
      the four our own Interval enum can represent - other tradedesk intervals have no
      CoinDCX equivalent).
    * `limit`: max 1000, default 500 if omitted.
    * CRITICAL, undocumented: startTime/endTime are silently IGNORED unless BOTH are
      given together. Passing only one (or neither) always returns the most recent
      `limit` candles regardless of what was asked for - not an error, no signal that
      anything was ignored. Passing both together pages correctly (verified: I-BTC_INR
      history goes back to at least Feb 2019 once paged properly with both parameters -
      a `limit=1000, no time params` call alone only reaches back to 2023-12-18 and
      looks like a hard depth limit, but isn't one).
    * `time` in the response is the candle OPEN time, epoch MILLISECONDS (INDstocks used
      epoch seconds - do not mix the two up across adapters).
    * Rows are returned newest-first (descending by time); the codebase's Candle/
      CandleStore expect ascending, so the adapter must reverse.
    * `volume` is in the BASE currency (BTC for I-BTC_INR), not INR - matches how
      data/universe.py's turnover already treats it (`close * volume` = INR notional).
    * No analog of the INDstocks "narrow window near now" data-loss quirk (commit
      67f39c9) was found: a same-day [00:00 UTC, now) window correctly returns today's
      candle when both startTime and endTime are passed. No padding workaround needed
      here - but only when BOTH parameters are given; the "both or neither is honoured"
      rule above still applies.
    * Some early history for thin pairs contains clearly bad prints (e.g. a close of
      0.002771 in early I-BTC_INR days no real BTC price was ever near) - a real feed
      quality issue to catch in the Phase 3 quality report, not something to paper over
      here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tradedesk.broker.indstocks.models import Candle, Interval

BASE_URL = "https://api.coindcx.com"

# Only these four of tradedesk's Interval values have a CoinDCX equivalent.
INTERVAL_MAP: dict[Interval, str] = {
    Interval.M1: "1m",
    Interval.M15: "15m",
    Interval.H1: "1h",
    Interval.D1: "1d",
}


def coindcx_name_from_pair(pair: str) -> str:
    """ "I-BTC_INR" -> "BTCINR": the short form (`coindcx_name` in markets_details, also
    used as our Instrument.security_id / scrip_code suffix) from the candles-endpoint
    `pair` identifier. Every observed active INR pair (338 checked 2026-09-12) is
    "{ecode}-{TARGET}_{BASE}" - one hyphen, one underscore - so this is a heuristic on
    that shape, not a documented CoinDCX guarantee. `tests/broker/test_coindcx.py`
    checks it against real observed pairs; if CoinDCX ever lists a pair that breaks this
    shape, prefer a coindcx_name looked up from markets_details() over parsing `pair`."""
    tail = pair.split("-", 1)[-1]
    return tail.replace("_", "")


def scrip_code_for_pair(pair: str) -> str:
    return f"CDX_{coindcx_name_from_pair(pair)}"


def coindcx_interval(interval: Interval) -> str:
    try:
        return INTERVAL_MAP[interval]
    except KeyError:
        supported = ", ".join(i.value for i in INTERVAL_MAP)
        raise ValueError(
            f"CoinDCX has no equivalent of {interval.value!r}; supported: {supported}"
        ) from None


def candle_from_api(scrip_code: str, interval: Interval, raw: dict[str, Any]) -> Candle:
    """CoinDCX candle JSON -> the shared Candle model. `time` is epoch MILLISECONDS (open
    time), unlike INDstocks' epoch seconds - see the module docstring.

    KNOWN LIMITATION: `Candle.volume` is an int (NSE whole-share counts; the DB column is
    BIGINT too - see data/candle_store.py). CoinDCX volume is base-currency and often
    fractional (BTC-INR's daily volume is single-digit BTC). Rounded rather than
    truncated, so a sub-0.5-unit day reads as 0 rather than being silently floored every
    time. Safe failure mode: a pair that rounds to 0 most days just never clears
    universe.py's turnover floor, it does not corrupt anything. Revisit if Phase 3's
    quality report shows this biting real candidates (a rescaled/notional volume column
    would fix it, at the cost of every consumer's units changing)."""
    return Candle(
        scrip_code=scrip_code,
        interval=interval,
        ts=datetime.fromtimestamp(int(raw["time"]) / 1000, tz=UTC),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        volume=round(float(raw["volume"])),
    )
