"""Synthetic candle builders for data-layer tests."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

from tradedesk.broker.indstocks.models import IST, Candle, Interval


def sessions(start: date, n: int) -> list[date]:
    """n weekdays from start (no holidays - the reference index defines the calendar)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def daily(
    code: str,
    days: Iterable[date],
    *,
    start_price: float = 100.0,
    drift: float = 0.0,
    volume: int = 1_000_000,
    interval: Interval = Interval.D1,
) -> list[Candle]:
    out: list[Candle] = []
    px = start_price
    for d in days:
        o = px
        c = px * (1 + drift)
        hi, lo = max(o, c) * 1.01, min(o, c) * 0.99
        out.append(
            Candle(
                scrip_code=code, interval=interval,
                ts=datetime.combine(d, time(9, 15), tzinfo=IST),
                open=round(o, 2), high=round(hi, 2), low=round(lo, 2), close=round(c, 2),
                volume=volume,
            )
        )  # fmt: skip
        px = c
    return out


def with_split(candles: list[Candle], ex_date: date, factor: float) -> list[Candle]:
    """Make an *unadjusted* series: every bar from ex_date on is scaled by `factor`."""
    out = []
    for c in candles:
        if c.ts.date() >= ex_date:
            c = c.model_copy(
                update={
                    "open": round(c.open * factor, 2),
                    "high": round(c.high * factor, 2),
                    "low": round(c.low * factor, 2),
                    "close": round(c.close * factor, 2),
                    "volume": int(c.volume / factor),
                }
            )
        out.append(c)
    return out
