"""The one scan the live evening job and the backtester both call (CLAUDE.md: no
live-only or backtest-only signal logic).

`scan_day` takes a `MarketSnapshot` - everything known at the close of one session - and
returns the signals armed at that close. It slices every frame to the snapshot date
itself, so passing a longer frame cannot leak the future (the backtest look-ahead test
asserts that the signals for a date are identical whether or not later bars exist).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from tradedesk.engine.regime import RegimeSnapshot
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.setups import REGISTRY, SetupContext


@dataclass
class MarketSnapshot:
    on: date
    features: Mapping[str, pd.DataFrame]  # scrip_code -> daily_features frame (any length)
    symbols: Mapping[str, str]  # scrip_code -> trading symbol
    rs_percentile: Mapping[str, float] = field(default_factory=dict)  # as of `on`
    regime: RegimeSnapshot | None = None
    results_in_sessions: Mapping[str, int | None] = field(default_factory=dict)
    universe: Sequence[str] | None = None  # codes eligible today; None = all in `features`


def slice_to(df: pd.DataFrame, on: date) -> pd.DataFrame:
    """Bars whose IST open date is <= `on`."""
    dates = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").date
    return df[dates <= on]


def scan_day(
    snapshot: MarketSnapshot,
    setups: Sequence[SetupKind],
    params: Mapping[str, Mapping[str, Any]],
    *,
    max_hold_sessions: int = 10,
) -> list[Signal]:
    codes = list(snapshot.universe) if snapshot.universe is not None else list(snapshot.features)
    regime_name = snapshot.regime.regime.value if snapshot.regime else None
    out: list[Signal] = []
    for code in codes:
        frame = snapshot.features.get(code)
        if frame is None or frame.empty:
            continue
        df = slice_to(frame, snapshot.on)
        if df.empty or pd.Timestamp(df.index[-1]).date() != snapshot.on:
            continue  # no bar on this session: not tradable today
        ctx = SetupContext(
            scrip_code=code,
            symbol=snapshot.symbols.get(code, code),
            rs_percentile=snapshot.rs_percentile.get(code),
            regime=regime_name,
            results_in_sessions=snapshot.results_in_sessions.get(code),
            max_hold_sessions=max_hold_sessions,
        )
        for kind in setups:
            setup = REGISTRY[kind]
            sig = setup.arm(df, ctx, dict(params.get(kind.value, {})))
            if sig is not None:
                out.append(sig)
    out.sort(key=lambda s: (s.setup.value, s.scrip_code))
    return out
