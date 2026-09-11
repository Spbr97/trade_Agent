"""Synthetic OHLCV frames for engine tests: build a daily chart from a close path."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST


def frame(
    closes: Sequence[float],
    *,
    ranges: Sequence[float] | None = None,
    volumes: Sequence[int] | None = None,
    start: date = date(2025, 1, 1),
    open_is_prev_close: bool = True,
) -> pd.DataFrame:
    """Bars where open = previous close (or = close), high/low = close +/- range/2
    widened to cover the open."""
    n = len(closes)
    ranges = list(ranges) if ranges is not None else [max(1.0, c * 0.02) for c in closes]
    volumes = list(volumes) if volumes is not None else [1_000_000] * n
    idx = []
    d = start
    while len(idx) < n:
        if d.weekday() < 5:
            idx.append(pd.Timestamp(datetime.combine(d, time(9, 15), tzinfo=IST)))
        d += timedelta(days=1)
    opens = [closes[0]] + list(closes[:-1]) if open_is_prev_close else list(closes)
    highs = [max(o, c) + r / 2 for o, c, r in zip(opens, closes, ranges, strict=True)]
    lows = [min(o, c) - r / 2 for o, c, r in zip(opens, closes, ranges, strict=True)]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": list(closes), "volume": volumes},
        index=pd.DatetimeIndex(idx, name="ts"),
    )


def trend(n: int, start: float = 100.0, step_pct: float = 0.01, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1 + step_pct + rng.normal(0, 0.004)))
    return out


def flat(n: int, level: float, wobble_pct: float = 0.01, seed: int = 1) -> list[float]:
    rng = np.random.default_rng(seed)
    return [level * (1 + rng.uniform(-wobble_pct, wobble_pct)) for _ in range(n)]
