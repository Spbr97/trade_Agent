"""Trend pullback (PLAN.md 6.5).

Arms when: 20 > 50 > 200-day EMAs, all rising; RS in the top quartile; a 2-5 session
pullback to the 20-day EMA on lower volume.
Trigger: 15-minute close above the previous day's high. Stop: below the pullback low.
Exits: partial at 2R (or the recent swing high, whichever is nearer once above 1R),
trail the rest under the 10-day EMA.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.engine.patterns import find_pullback, overhead_supply
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.setups.base import (
    SetupContext,
    make_signal,
    pattern_config,
    results_ok,
    rs_ok,
    uptrend,
)


class TrendPullback:
    kind = SetupKind.TREND_PULLBACK

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < 210:
            return None
        last = df.iloc[-1]
        trend = uptrend(last, require_stack=True)
        if not trend.ok or not rs_ok(ctx, params) or not results_ok(ctx):
            return None
        cfg = pattern_config(params)
        pb = find_pullback(df, cfg)
        if pb is None:
            return None
        trigger = pb.trigger_level
        stop = pb.low
        risk = trigger - stop
        if risk <= 0:
            return None
        partial_r = float(params.get("partial_at_r", 2.0))
        # Partial at 2R, or at the swing high if that comes first but is still worth >= 1R.
        swing_r = (pb.swing_high - trigger) / risk
        if 1.0 <= swing_r < partial_r:
            partial_r = swing_r
        supply = overhead_supply(df, trigger, cfg)
        exit_plan = ExitPlan(partial_at_r=partial_r, trail="ema10_close")
        return make_signal(
            kind=self.kind,
            df=df,
            trigger=trigger,
            stop=stop,
            final_target=None,  # 3R default; the swing high is an overhead level, not a cap
            exit_plan=exit_plan,
            ctx=ctx,
            geometry={"pullback": pb.model_dump(), "overhead": supply.model_dump()},
            reasons=[
                f"{pb.length}-bar pullback to EMA{cfg.pullback_ema} "
                f"({pb.distance_to_ema_pct:+.1%}), volume {pb.volume_ratio:.2f}x",
                f"RS {ctx.rs_percentile:.0f}" if ctx.rs_percentile is not None else "RS n/a",
            ],
        )
