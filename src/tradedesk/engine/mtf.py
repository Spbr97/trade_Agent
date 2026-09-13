"""Multi-timeframe alignment (SDD section 5): 1H structure -> 15M trend -> 5M setup ->
3M confirmation -> 1M entry timing. "The agent should reject trades when important
timeframes materially conflict" - `align()` is the pure function that decides that, given
one frame per timeframe.

Nothing here creates a signal; this only reports whether the timeframes agree, for a caller
(the eventual `scan_bar`) to act on. Same separation of concerns as `engine/regime.py`
(market regime) versus `engine/engine.py` (signal generation).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from tradedesk.broker.indstocks.models import Interval

# Coarsest to finest, exactly the SDD's stated hierarchy.
HIERARCHY: tuple[Interval, ...] = (
    Interval.H1,
    Interval.M15,
    Interval.M5,
    Interval.M3,
    Interval.M1,
)


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"  # a closed bar exists but its trend features do not stack either way
    UNKNOWN = "unknown"  # this timeframe was not supplied, or has no bar closed by `at` yet


@dataclass(frozen=True)
class Alignment:
    at: pd.Timestamp
    directions: dict[Interval, Direction]
    agreement: float  # fraction of the DEFINED (bullish/bearish) timeframes matching the majority
    conflict: bool
    reasons: tuple[str, ...] = ()


def last_closed_bar(frame: pd.DataFrame, interval: Interval, at: pd.Timestamp) -> pd.Series | None:
    """The most recent row of `frame` whose bar has FULLY CLOSED by `at`.

    `ts` (the frame's index) is the bar's OPEN time - the same convention every other caller
    in this project relies on (docs/indstocks-api.md; live/bars.py). A bar only exists in
    full once `ts + interval.seconds` has elapsed, so the naive `idx <= at` would let a bar
    that started AT `at` and is still forming count as known data - real look-ahead, since
    its close/high/low do not exist yet. `test_last_closed_bar_excludes_an_in_progress_bar`
    is the regression test for exactly this."""
    idx = pd.DatetimeIndex(frame.index)
    closes_at = idx + pd.Timedelta(seconds=interval.seconds)
    eligible = frame.loc[closes_at <= at]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _direction(row: pd.Series) -> Direction:
    """BULLISH/BEARISH only on a clean EMA stack (close above/below ema9 > ema20 > ema50 in
    order), NEUTRAL otherwise. The same stacking idea as the daily
    `engine/scoring.py::trend_strength` (EMA20/50/200), using the shorter EMAs
    `intraday_features` actually provides - see that function's docstring for why it has no
    ema200: a year-scale average is meaningless on intraday bars."""
    try:
        c, e9, e20, e50 = row["close"], row["ema9"], row["ema20"], row["ema50"]
    except KeyError:
        return Direction.NEUTRAL
    if any(pd.isna(v) for v in (c, e9, e20, e50)):
        return Direction.NEUTRAL
    if c > e9 > e20 > e50:
        return Direction.BULLISH
    if c < e9 < e20 < e50:
        return Direction.BEARISH
    return Direction.NEUTRAL


def align(frames: Mapping[Interval, pd.DataFrame], at: pd.Timestamp) -> Alignment:
    """One `Direction` per timeframe in `HIERARCHY` that appears in `frames`, read from the
    last bar that had actually closed by `at` - never an in-progress one. A timeframe with
    no frame supplied, or none of its bars closed yet, is UNKNOWN rather than assumed
    NEUTRAL, so a caller can tell "we checked and this timeframe disagrees" apart from
    "we never had this timeframe to check"."""
    directions: dict[Interval, Direction] = {}
    for iv in HIERARCHY:
        frame = frames.get(iv)
        if frame is None or frame.empty:
            directions[iv] = Direction.UNKNOWN
            continue
        row = last_closed_bar(frame, iv, at)
        directions[iv] = Direction.UNKNOWN if row is None else _direction(row)

    defined = {iv: d for iv, d in directions.items() if d in (Direction.BULLISH, Direction.BEARISH)}
    if not defined:
        return Alignment(
            at=at, directions=directions, agreement=0.0, conflict=True,
            reasons=("no timeframe has a defined (non-neutral) direction yet",),
        )  # fmt: skip

    bulls = [iv for iv, d in defined.items() if d is Direction.BULLISH]
    bears = [iv for iv, d in defined.items() if d is Direction.BEARISH]
    majority, against = (Direction.BULLISH, bears) if len(bulls) >= len(bears) else (Direction.BEARISH, bulls)  # noqa: E501
    agreement = max(len(bulls), len(bears)) / len(defined)
    conflict = bool(bulls) and bool(bears)
    reasons: tuple[str, ...] = ()
    if conflict:
        reasons = (
            f"timeframes disagree: majority {majority.value}, "
            f"against: {', '.join(iv.value for iv in against)}",
        )
    return Alignment(at=at, directions=directions, agreement=agreement, conflict=conflict, reasons=reasons)  # noqa: E501
