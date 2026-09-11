"""Triple-barrier labels (PLAN.md 10.2).

For a triggered signal, look forward from the entry session and record which barrier is
hit first: the upper barrier (+partial_at_r, i.e. T1) -> 1; the lower barrier (the stop,
gap-aware: an OPEN below the stop counts as hit) -> 0; the vertical barrier (max hold
sessions) without either -> 0. A bar touching both stop and T1 counts as the stop (the
same intrabar rule the backtester uses).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class Label:
    label: int  # 1 = reached T1 before the stop inside the window
    outcome: str  # "target" | "stop" | "gap_stop" | "timeout" | "insufficient"
    sessions: int  # sessions until the outcome (0 = entry day)
    exit_price: float | None


def triple_barrier(
    bars: pd.DataFrame,
    *,
    entry: float,
    stop: float,
    target: float,
    max_hold: int,
    entry_day_included: bool = True,
) -> Label:
    """`bars` = daily OHLC from the entry session onward (entry day first). Only the bars
    inside the window are examined, so labels can never see beyond max_hold sessions."""
    if bars.empty:
        return Label(0, "insufficient", 0, None)
    window = bars.iloc[: max_hold + 1]
    opens = window["open"].to_numpy(dtype=float)
    highs = window["high"].to_numpy(dtype=float)
    lows = window["low"].to_numpy(dtype=float)
    closes = window["close"].to_numpy(dtype=float)
    for i in range(len(window)):
        first = i == 0 and entry_day_included
        if not first and opens[i] <= stop:
            return Label(0, "gap_stop", i, float(opens[i]))
        if lows[i] <= stop:
            return Label(0, "stop", i, float(stop))
        if highs[i] >= target:
            return Label(1, "target", i, float(target))
    if len(window) < max_hold + 1:
        return Label(0, "insufficient", len(window) - 1, None)
    return Label(0, "timeout", max_hold, float(closes[-1]))


def label_signal(
    feats: pd.DataFrame,
    *,
    entry_date: date,
    entry: float,
    stop: float,
    target: float,
    max_hold: int,
) -> Label:
    """Convenience: slice the daily frame from `entry_date` and label."""
    dates = pd.DatetimeIndex(feats.index).tz_convert("Asia/Kolkata").date
    start = next((i for i, d in enumerate(dates) if d >= entry_date), None)
    if start is None:
        return Label(0, "insufficient", 0, None)
    return triple_barrier(
        feats.iloc[start:], entry=entry, stop=stop, target=target, max_hold=max_hold
    )
