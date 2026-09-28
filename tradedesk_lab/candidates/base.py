"""LabSignal/LabSetup: the candidate-authoring shape, structurally identical to
production's `Setup`/`Signal` (`src/tradedesk/setups/base.py`,
`src/tradedesk/engine/signals.py`) but without requiring a real `SetupKind` enum member -
see this package's own docstring for why that matters.

`evaluate_entry`/`evaluate_exit` (`backtest/fills.py`) only ever read the attributes
listed on `LabSignal` below off whatever object they're given - Python does not enforce
the `Signal` type hint at runtime, and `Position` (the other object they touch) is a
plain, non-validating dataclass, so a `LabSignal` duck-types cleanly through both
functions unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import pandas as pd

from tradedesk.engine.signals import ExitPlan
from tradedesk.setups.base import SetupContext


@dataclass(frozen=True)
class LabSignal:
    id: str
    scrip_code: str
    symbol: str
    armed_on: date
    trigger: float
    stop: float
    t1: float
    t2: float
    atr: float
    exit_plan: ExitPlan = ExitPlan()
    valid_sessions: int = 3
    chased_atr_mult: float = 1.0
    reasons: tuple[str, ...] = ()


class LabSetup(Protocol):
    name: str

    def arm(
        self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]
    ) -> LabSignal | None: ...
