"""The intraday sibling of `engine/engine.py::scan_day` (CLAUDE.md hard rule: signals are
created only by `scan_day` (daily) and `scan_bar` (intraday), nowhere else).

`scan_bar` takes an `IntradaySnapshot` - everything known at one bar-close `at`, on one
timeframe - and returns the signals armed at that close. It slices every frame to the last
CLOSED bar itself via `mtf.last_closed_bar`, the same safety-net philosophy `scan_day` uses
with `slice_to`: a caller passing a longer frame than it should have cannot leak the future,
because this function trusts nothing past what has actually closed by `at`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine.intraday_regime import IntradayRegime
from tradedesk.engine.intraday_signals import IntradaySetupKind, IntradaySignal
from tradedesk.engine.mtf import Alignment, last_closed_bar
from tradedesk.setups.intraday import INTRADAY_REGISTRY, IntradaySetupContext


@dataclass
class IntradaySnapshot:
    at: pd.Timestamp
    arming_interval: Interval
    features: Mapping[str, pd.DataFrame]  # scrip_code -> intraday_features frame, any length
    symbols: Mapping[str, str]
    alignment: Mapping[str, Alignment] = field(default_factory=dict)  # as of `at`
    regime: Mapping[str, IntradayRegime] = field(default_factory=dict)  # as of `at`
    universe: Sequence[str] | None = None  # codes eligible now; None = all in `features`


def scan_bar(
    snapshot: IntradaySnapshot,
    setups: Sequence[IntradaySetupKind],
    params: Mapping[str, Mapping[str, Any]],
    *,
    max_hold_bars: int = 60,
) -> list[IntradaySignal]:
    codes = list(snapshot.universe) if snapshot.universe is not None else list(snapshot.features)
    out: list[IntradaySignal] = []
    for code in codes:
        frame = snapshot.features.get(code)
        if frame is None or frame.empty:
            continue
        row = last_closed_bar(frame, snapshot.arming_interval, snapshot.at)
        if row is None:
            continue  # nothing has closed yet on this timeframe as of `at`
        df = frame.loc[: row.name]  # everything up to and including the last closed bar
        ctx = IntradaySetupContext(
            scrip_code=code,
            symbol=snapshot.symbols.get(code, code),
            interval=snapshot.arming_interval,
            alignment=snapshot.alignment.get(code),
            regime=snapshot.regime.get(code),
            max_hold_bars=max_hold_bars,
        )
        for kind in setups:
            setup = INTRADAY_REGISTRY[kind]
            sig = setup.arm(df, ctx, dict(params.get(kind.value, {})))
            if sig is not None:
                out.append(sig)
    out.sort(key=lambda s: (s.setup.value, s.scrip_code))
    return out
