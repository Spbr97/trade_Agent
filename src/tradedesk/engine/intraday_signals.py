"""Intraday signal models (SDD sections 3-7), parallel to `engine/signals.py` rather than
sharing it.

Not a variant of the daily `Signal`: the semantics genuinely differ. `Signal.armed_on` is a
`date` and every hold/exit rule is measured in SESSIONS; an intraday signal arms at a
TIMESTAMP on a specific timeframe and every hold/exit rule is measured in BARS of that
timeframe, which is a different number of real minutes for every interval. Forcing both into
one model would mean either a `date`-typed field silently truncating a timestamp, or a
"sessions" field that actually means "bars" for half its callers - exactly the kind of
ambiguity this project's leakage and unit-mixing bugs have already come from elsewhere
(CLAUDE.md's TradeType/qty_step notes). Keeping them separate costs one extra small module,
not a real one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from tradedesk.broker.indstocks.models import Interval


class IntradaySetupKind(StrEnum):
    VWAP_RECLAIM = "vwap_reclaim"


class IntradayExitPlan(BaseModel):
    """Bar-counted analogue of `engine/signals.py::ExitPlan` - see this module's docstring
    for why the two are not shared."""

    model_config = ConfigDict(frozen=True)

    partial_at_r: float = 2.0
    partial_fraction: float = 0.5
    trail: str = "vwap"  # trail the runner to session VWAP once the partial is taken
    time_stop_bars: int = 20
    time_stop_min_r: float = 0.5
    max_hold_bars: int = 60


class IntradaySignal(BaseModel):
    """An ARMED intraday setup: exact trigger, stop and targets, valid for `valid_bars` more
    bars of `interval`. Prices are per share, same convention as the daily `Signal`."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    scrip_code: str
    symbol: str
    setup: IntradaySetupKind
    interval: Interval
    armed_at: pd.Timestamp
    trigger: float
    stop: float
    t1: float
    t2: float
    atr: float
    exit_plan: IntradayExitPlan = IntradayExitPlan()
    valid_bars: int = 5
    chased_atr_mult: float = 1.0
    geometry: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)

    @property
    def risk_per_share(self) -> float:
        return self.trigger - self.stop
