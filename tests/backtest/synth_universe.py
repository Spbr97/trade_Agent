"""Engineered stocks for end-to-end backtest tests: an advance, a tight base, a breakout
on a chosen session, then a scripted aftermath (win / gap-down / grind to the stop)."""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np

from tradedesk.broker.indstocks.models import IST, Candle, Interval


def _candle(code: str, d: date, o: float, h: float, lo: float, c: float, v: int) -> Candle:
    return Candle(
        scrip_code=code, interval=Interval.D1, ts=datetime.combine(d, time(9, 15), tzinfo=IST),
        open=round(o, 2), high=round(max(o, h, c), 2), low=round(min(o, lo, c), 2),
        close=round(c, 2), volume=v,
    )  # fmt: skip


def breakout_stock(
    code: str,
    calendar: list[date],
    *,
    breakout_index: int,
    aftermath: str = "win",
    base_len: int = 15,
    seed: int = 0,
    start_price: float = 100.0,
) -> list[Candle]:
    """Bars for every calendar date. The base ends the session before `breakout_index`."""
    rng = np.random.default_rng(seed)
    n = len(calendar)
    out: list[Candle] = []
    base_start = breakout_index - base_len
    assert base_start > 60, "need history before the base"
    px = start_price
    base_high = 0.0
    base_low = 0.0
    entry_stop = 0.0
    for i, d in enumerate(calendar):
        if i < base_start:  # steady advance with noise
            c = px * (1 + 0.006 + rng.normal(0, 0.006))
            r = c * 0.025
            out.append(_candle(code, d, px, max(px, c) + r / 2, min(px, c) - r / 2, c, 1_500_000))
            px = c
            if i == base_start - 1:
                base_high = px * 1.015
                base_low = px * 0.99
        elif i < breakout_index:  # tight base under base_high, volume drying up
            c = base_low + (base_high - base_low) * rng.uniform(0.35, 0.9)
            hi = min(base_high * 0.995, c * 1.008)  # never touches the base high itself
            lo = max(base_low, c * 0.992)
            out.append(_candle(code, d, px, hi, lo, c, 700_000))
            px = c
            entry_stop = base_low
        elif i == breakout_index:  # opens just above the base high, closes strong
            o = base_high * 1.004
            c = base_high * 1.03
            out.append(_candle(code, d, o, c * 1.005, o * 0.998, c, 3_000_000))
            px = c
        else:
            k = i - breakout_index
            risk = base_high - entry_stop
            if aftermath == "win":  # 2R by day 3, then drifts higher above the 10-EMA
                target = base_high + risk * (0.8 * k if k <= 3 else 2.4 + 0.1 * k)
                c = target
                out.append(_candle(code, d, px, c * 1.01, min(px, c) * 0.995, c, 1_500_000))
            elif aftermath == "gap":  # next session opens 8% below the stop
                c = entry_stop * 0.92 if k == 1 else px * (1 + rng.normal(0, 0.005))
                o = c if k == 1 else px
                out.append(_candle(code, d, o, max(o, c) * 1.005, min(o, c) * 0.995, c, 2_000_000))
            else:  # "stop": grinds down through the stop
                c = px * (1 - 0.02)
                out.append(_candle(code, d, px, px * 1.003, c * 0.997, c, 1_200_000))
            px = c
    assert len(out) == n
    return out


def benchmark(
    code: str, calendar: list[date], start: float = 20000.0, seed: int = 99
) -> list[Candle]:
    rng = np.random.default_rng(seed)
    out = []
    px = start
    for d in calendar:
        c = px * (1 + 0.0005 + rng.normal(0, 0.004))
        out.append(_candle(code, d, px, max(px, c) * 1.002, min(px, c) * 0.998, c, 0))
        px = c
    return out
