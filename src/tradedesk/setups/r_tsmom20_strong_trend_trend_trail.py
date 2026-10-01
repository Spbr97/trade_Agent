"""Self-review replacement candidate `r_tsmom20_strong_trend_trend_trail`.

tsmom20 entry, strong_trend filter, trend_trail exit.

Generated from tradedesk_lab/candidates/rules.py and validated through the full harness
gauntlet before it was proposed - see docs/self-review-applied-log.md for the approval
that added it.
"""

from __future__ import annotations

from typing import Any

import numpy as np  # noqa: F401 - available to the generated expressions
import pandas as pd

from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.setups.base import SetupContext, make_signal

TAIL_BARS = 300


def _trigger(df: pd.DataFrame) -> pd.Series:
    fired = (df["close"] > df["close"].shift(20)) & (df["close"].shift(1) <= df["close"].shift(21))  # noqa: E501
    return fired.fillna(False).astype(bool)


def _filter(df: pd.DataFrame) -> pd.Series:
    passes = (df["adx14"] > 30) & (df["plus_di"] > df["minus_di"])  # noqa: E501
    return passes.fillna(False).astype(bool)


class RTsmom20StrongTrendTrendTrail:
    kind = SetupKind.R_TSMOM20_STRONG_TREND_TREND_TRAIL

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < 210:
            return None
        tail = df.iloc[-TAIL_BARS:]
        if not bool((_trigger(tail) & _filter(tail)).iloc[-1]):
            return None
        last = df.iloc[-1]
        trigger = float(last["close"])
        stop = trigger - 2.5 * float(last["atr14"])
        return make_signal(
            kind=self.kind, df=df, trigger=trigger, stop=stop, final_target=None,
            exit_plan=ExitPlan(
                partial_at_r=2.0, partial_fraction=0.5,
                trail="atr", trail_atr_mult=3.0,
                time_stop_sessions=41, time_stop_min_r=1.0,
                max_hold_sessions=40,
            ),
            ctx=ctx, geometry={"stop_atr": 2.5},
            reasons=["tsmom20 entry, strong_trend filter, trend_trail exit"],
            valid_sessions=1,
        )
