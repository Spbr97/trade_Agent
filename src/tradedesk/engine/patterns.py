"""Pattern library (PLAN.md 6.4): code-detected chart structures with explicit geometry.

Every detector evaluates the *last* bar of the frame it is given and returns geometry
(bar positions, levels, targets) so the setup layer can build triggers/stops, the alert
chart can draw it, and unit tests can assert on it. Detectors use only the bars they are
given - slicing the frame to the evaluation date is what makes them look-ahead free.

Pivots are confirmed `bars` sessions after they form; a detector never uses a pivot that
would not have been known on the last bar.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from tradedesk.config.models import PatternConfig
from tradedesk.engine.indicators import ema, rolling_high


class Geometry(BaseModel):
    model_config = ConfigDict(frozen=True)


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class Pivot(Geometry):
    index: int  # positional index in the frame
    price: float
    kind: PivotKind
    confirmed_at: int  # positional index at which this pivot became known


def swing_pivots(df: pd.DataFrame, bars: int = 3) -> list[Pivot]:
    """Fractal pivots: a high greater than the `bars` highs on each side (lows mirrored).
    Only pivots whose right side has fully printed by the last bar are returned."""
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(df)
    out: list[Pivot] = []
    for i in range(bars, n - bars):
        left = slice(i - bars, i)
        right = slice(i + 1, i + bars + 1)
        if highs[i] > highs[left].max() and highs[i] > highs[right].max():
            out.append(
                Pivot(index=i, price=float(highs[i]), kind=PivotKind.HIGH, confirmed_at=i + bars)
            )
        if lows[i] < lows[left].min() and lows[i] < lows[right].min():
            out.append(
                Pivot(index=i, price=float(lows[i]), kind=PivotKind.LOW, confirmed_at=i + bars)
            )
    return out


# --------------------------------------------------------------------- base


class Base(Geometry):
    start: int
    end: int
    length: int
    high: float
    low: float
    depth_pct: float  # (high - low) / high
    close_within_pct: float  # (high - last close) / high
    contraction_ratio: float  # mean range of last 5 bars / mean range of the base
    volume_dryup: float  # mean volume of last 5 bars / mean volume of the trailing 50 bars
    tests_of_high: int  # bars whose high came within 1% of the base high
    breakout_level: float  # = high
    measured_target: float  # high + (high - low)


def find_base(df: pd.DataFrame, cfg: PatternConfig) -> Base | None:
    """Consolidation under a high: the base starts at the first bar (within the last
    `base_len.max` bars) whose high came within 1% of the window high, and runs to the last
    bar. Qualifies when its length is in range, its depth is within limit and the last close
    sits within `base_close_within_pct` of the high."""
    n = len(df)
    if n < cfg.base_len.min:
        return None
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    vols = df["volume"].to_numpy(dtype=float)
    rng = highs - lows
    window_start = max(0, n - cfg.base_len.max)
    hi = highs[window_start:].max()
    touches = np.flatnonzero(highs[window_start:] >= hi * 0.99)
    start = window_start + int(touches[0])
    length = n - start
    if length < cfg.base_len.min:
        return None
    lo = lows[start:].min()
    depth = (hi - lo) / hi
    within = (hi - closes[-1]) / hi
    if depth > float(cfg.base_max_depth_pct) or not 0 <= within <= float(cfg.base_close_within_pct):
        return None
    vol_prior = vols[max(0, n - 50) : n]
    base_rng = rng[start:].mean()
    return Base(
        start=start,
        end=n - 1,
        length=length,
        high=float(hi),
        low=float(lo),
        depth_pct=float(depth),
        close_within_pct=float(within),
        contraction_ratio=float(rng[-5:].mean() / base_rng) if base_rng > 0 else 1.0,
        volume_dryup=float(vols[-5:].mean() / vol_prior.mean()) if vol_prior.mean() > 0 else 1.0,
        tests_of_high=int(len(touches)),
        breakout_level=float(hi),
        measured_target=float(hi + (hi - lo)),
    )


# --------------------------------------------------------------------- flag


class Flag(Geometry):
    pole_start: int
    pole_end: int
    flag_start: int
    flag_end: int
    pole_low: float
    pole_high: float
    pole_gain_pct: float
    flag_high: float
    flag_low: float
    retrace: float  # (pole_high - flag_low) / (pole_high - pole_low)
    breakout_level: float  # = flag_high
    measured_target: float  # flag_high + (pole_high - pole_low)


def find_flag(df: pd.DataFrame, cfg: PatternConfig) -> Flag | None:
    """A strong pole (>= min gain within pole_max_len bars into the peak) followed by a
    shallow, quieter drift that has not exceeded the peak. The pole ends at the highest
    high of the recent window; the flag is everything after it."""
    n = len(df)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    rng = highs - lows
    window_start = max(0, n - (cfg.flag_len.max + cfg.flag_pole_max_len + 1))
    pole_end = window_start + int(np.argmax(highs[window_start:]))
    f = n - 1 - pole_end
    if not cfg.flag_len.min <= f <= cfg.flag_len.max:
        return None
    pole_window_start = max(0, pole_end - cfg.flag_pole_max_len)
    pole_start = pole_window_start + int(np.argmin(lows[pole_window_start : pole_end + 1]))
    if pole_start >= pole_end:
        return None
    pole_high, pole_low = highs[pole_end], lows[pole_start]
    if pole_low <= 0:
        return None
    gain = pole_high / pole_low - 1
    if gain < float(cfg.flag_pole_min_gain_pct):
        return None
    flag_high = highs[pole_end + 1 :].max()
    flag_low = lows[pole_end + 1 :].min()
    pole_height = pole_high - pole_low
    retrace = (pole_high - flag_low) / pole_height
    if not 0 <= retrace <= float(cfg.flag_max_retrace):
        return None
    if rng[pole_end + 1 :].mean() >= rng[pole_start : pole_end + 1].mean():
        return None  # the flag must be quieter than the pole
    return Flag(
        pole_start=pole_start,
        pole_end=pole_end,
        flag_start=pole_end + 1,
        flag_end=n - 1,
        pole_low=float(pole_low),
        pole_high=float(pole_high),
        pole_gain_pct=float(gain),
        flag_high=float(flag_high),
        flag_low=float(flag_low),
        retrace=float(retrace),
        breakout_level=float(flag_high),
        measured_target=float(flag_high + pole_height),
    )


# ----------------------------------------------------------------- pullback


class Pullback(Geometry):
    swing_high_index: int
    start: int
    end: int
    length: int
    swing_high: float
    low: float
    ema_value: float
    distance_to_ema_pct: float  # (low - ema) / ema, negative if it dipped below
    volume_ratio: float  # mean volume in pullback / mean volume of the 10 bars before it
    trigger_level: float  # previous day's high == last bar's high


def find_pullback(df: pd.DataFrame, cfg: PatternConfig) -> Pullback | None:
    """2-5 bars of lower highs from a swing high, touching the rising EMA on lower volume."""
    n = len(df)
    e = ema(df["close"], cfg.pullback_ema).to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    vols = df["volume"].to_numpy(dtype=float)
    touch = float(cfg.pullback_touch_pct)
    for length in range(cfg.pullback_len.max, cfg.pullback_len.min - 1, -1):
        sh = n - length - 1  # swing-high bar
        if sh < 10:
            continue
        pb_highs = highs[sh + 1 :]
        if not (pb_highs < highs[sh]).all():
            continue
        if not np.all(np.diff(pb_highs) <= 0):
            continue  # highs must step down (or hold), not bounce back up
        ema_now = e[-1]
        low = lows[sh + 1 :].min()
        if low > ema_now * (1 + touch):
            continue  # never reached the EMA
        if df["close"].iloc[-1] < ema_now * (1 - 2 * touch):
            continue  # broke well below it: not a pullback, a breakdown
        if e[-1] <= e[-1 - length]:
            continue  # EMA must be rising
        prior = vols[max(0, sh - 9) : sh + 1]
        ratio = float(vols[sh + 1 :].mean() / prior.mean()) if prior.mean() > 0 else 1.0
        if ratio >= 1.0:
            continue
        return Pullback(
            swing_high_index=sh,
            start=sh + 1,
            end=n - 1,
            length=length,
            swing_high=float(highs[sh]),
            low=float(low),
            ema_value=float(ema_now),
            distance_to_ema_pct=float((low - ema_now) / ema_now),
            volume_ratio=ratio,
            trigger_level=float(highs[-1]),
        )
    return None


# ------------------------------------------------------- volatility squeeze


class Squeeze(Geometry):
    nr7: bool
    inside_day: bool
    near_high: bool
    pct_from_high20: float
    day_high: float
    day_low: float


def volatility_squeeze(df: pd.DataFrame, cfg: PatternConfig) -> Squeeze:
    highs = df["high"]
    lows = df["low"]
    rng = highs - lows
    n = len(df)
    is_nr7 = bool(n >= 7 and rng.iloc[-1] <= rng.iloc[-7:].min())
    inside = bool(n >= 2 and highs.iloc[-1] < highs.iloc[-2] and lows.iloc[-1] > lows.iloc[-2])
    h20 = float(rolling_high(highs, 20).iloc[-1])
    close = float(df["close"].iloc[-1])
    pct_from_high = (h20 - close) / h20 if h20 > 0 else 1.0
    return Squeeze(
        nr7=is_nr7,
        inside_day=inside,
        near_high=pct_from_high <= float(cfg.near_high_pct),
        pct_from_high20=float(pct_from_high),
        day_high=float(highs.iloc[-1]),
        day_low=float(lows.iloc[-1]),
    )


# ------------------------------------------------------------ support forms


class SupportKind(StrEnum):
    HIGHER_LOW = "higher_low"
    DOUBLE_BOTTOM = "double_bottom"


class Support(Geometry):
    kind: SupportKind
    first: Pivot
    second: Pivot
    level: float  # the second low


def support_confirmation(df: pd.DataFrame, cfg: PatternConfig) -> Support | None:
    """From the last two confirmed pivot lows: a double bottom (within tolerance) or a
    higher low, provided price currently sits above the second low."""
    lows = [p for p in swing_pivots(df, cfg.pivot_bars) if p.kind is PivotKind.LOW]
    if len(lows) < 2:
        return None
    first, second = lows[-2], lows[-1]
    if float(df["close"].iloc[-1]) <= second.price:
        return None
    tol = float(cfg.double_bottom_tolerance_pct)
    if abs(second.price / first.price - 1) <= tol:
        kind = SupportKind.DOUBLE_BOTTOM
    elif second.price > first.price * (1 + tol):
        kind = SupportKind.HIGHER_LOW
    else:
        return None
    return Support(kind=kind, first=first, second=second, level=second.price)


# ---------------------------------------------------------- overhead supply


class OverheadSupply(Geometry):
    price: float
    nearest_level: float | None  # None = blue sky
    distance_pct: float | None
    source: str | None  # "pivot_high" | "52w_high"
    levels: list[float]


def overhead_supply(df: pd.DataFrame, price: float, cfg: PatternConfig) -> OverheadSupply:
    """Prior confirmed pivot highs and the 52-week high above `price`, nearest first."""
    window = df.iloc[-cfg.overhead_lookback :]
    levels: list[tuple[float, str]] = [
        (p.price, "pivot_high")
        for p in swing_pivots(window, cfg.pivot_bars)
        if p.kind is PivotKind.HIGH and p.price > price
    ]
    h52 = float(window["high"].max())
    if h52 > price:
        levels.append((h52, "52w_high"))
    levels.sort(key=lambda lv: lv[0])
    if not levels:
        return OverheadSupply(
            price=price, nearest_level=None, distance_pct=None, source=None, levels=[]
        )
    nearest, source = levels[0]
    return OverheadSupply(
        price=price,
        nearest_level=nearest,
        distance_pct=(nearest - price) / price,
        source=source,
        levels=[lv for lv, _ in levels],
    )
