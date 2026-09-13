"""NR7 / inside-day breakout (PLAN.md 6.5).

Arms when: uptrend (above a rising EMA50, RS top quartile) plus an NR7 or inside day
within 5% of recent highs. Trigger: 15-minute close above that day's high. Stop: below
that day's low. Exits: partial at 2R, trail at 2 x ATR below the highest close.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.engine.patterns import overhead_supply, volatility_squeeze
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.setups.base import (
    SetupContext,
    make_signal,
    pattern_config,
    results_ok,
    rs_ok,
    uptrend,
)


class Nr7Breakout:
    kind = SetupKind.NR7_BREAKOUT

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < 60:
            return None
        last = df.iloc[-1]
        trend = uptrend(last)
        if not trend.ok or not rs_ok(ctx, params) or not results_ok(ctx):
            return None
        cfg = pattern_config(params)
        sq = volatility_squeeze(df, cfg)
        if not (sq.nr7 or sq.inside_day) or not sq.near_high:
            return None
        atr = float(last["atr14"])
        if sq.day_high - sq.day_low < float(params.get("min_range_atr", 0.15)) * atr:
            return None  # a bar this tiny is noise, not a coil
        supply = overhead_supply(df, sq.day_high, cfg)
        exit_plan = ExitPlan(
            partial_at_r=float(params.get("partial_at_r", 2.0)),
            partial_fraction=float(params.get("partial_fraction", 0.5)),
            trail="atr",
            trail_atr_mult=float(params.get("trail_atr_multiple", 2.0)),
        )
        return make_signal(
            kind=self.kind,
            df=df,
            trigger=sq.day_high,
            stop=sq.day_low,
            final_target=None,
            exit_plan=exit_plan,
            ctx=ctx,
            geometry={"squeeze": sq.model_dump(), "overhead": supply.model_dump()},
            reasons=[
                ("NR7" if sq.nr7 else "inside day")
                + f", {sq.pct_from_high20:.1%} below the 20-day high",
                f"RS {ctx.rs_percentile:.0f}" if ctx.rs_percentile is not None else "RS n/a",
            ],
        )
