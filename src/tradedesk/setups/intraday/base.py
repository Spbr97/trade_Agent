"""Intraday setup protocol and context, parallel to `setups/base.py`'s daily one.

An intraday setup receives the `intraday_features` frame of ONE stock on ONE timeframe, up
to and including the arming bar, plus a context (multi-timeframe alignment, this
instrument's own intraday regime), and returns an `IntradaySignal` or None. It never sees a
bar that has not closed yet - `engine/intraday_engine.py::scan_bar` slices the frame before
any setup sees it, the same safety-net pattern `engine/engine.py::scan_day` uses for daily
setups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine.intraday_regime import IntradayRegime
from tradedesk.engine.intraday_signals import IntradaySetupKind, IntradaySignal
from tradedesk.engine.mtf import Alignment


@dataclass(frozen=True)
class IntradaySetupContext:
    scrip_code: str
    symbol: str
    interval: Interval
    alignment: Alignment | None = None  # multi-timeframe context as of the arming bar
    regime: IntradayRegime | None = None  # this instrument's own intraday regime
    max_hold_bars: int = 60


class IntradaySetup(Protocol):
    kind: IntradaySetupKind

    def arm(
        self, df: pd.DataFrame, ctx: IntradaySetupContext, params: dict[str, Any]
    ) -> IntradaySignal | None: ...
