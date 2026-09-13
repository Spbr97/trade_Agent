"""Base breakout (PLAN.md 6.5).

Arms when: price above a rising EMA50, RS in the top quartile, a 5-25 session tight base
with volume drying up, close within 3% of the base high.
Trigger: 15-minute close above the base high on above-normal volume (daily approximation
in the backtester). Stop: below the base low; skip if that is more than 2 x ATR away.
Exits: half at 2R and stop to breakeven, trail the rest under the 10-day EMA (closing basis).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.engine.patterns import find_base, overhead_supply
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.setups.base import (
    SetupContext,
    make_signal,
    pattern_config,
    results_ok,
    rs_ok,
    uptrend,
)


class BaseBreakout:
    kind = SetupKind.BASE_BREAKOUT

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < 60:
            return None
        last = df.iloc[-1]
        trend = uptrend(last)
        if not trend.ok or not rs_ok(ctx, params) or not results_ok(ctx):
            return None
        cfg = pattern_config(params)
        base = find_base(df, cfg)
        if base is None:
            return None
        if base.volume_dryup > float(params.get("max_volume_dryup", 1.0)):
            return None
        atr = float(last["atr14"])
        stop = base.low
        if base.high - stop > float(params.get("max_stop_distance_atr", 2.0)) * atr:
            return None
        supply = overhead_supply(df, base.high, cfg)
        exit_plan = ExitPlan(
            partial_at_r=float(params.get("partial_at_r", 2.0)),
            partial_fraction=float(params.get("partial_fraction", 0.5)),
            trail="ema10_close",
        )
        return make_signal(
            kind=self.kind,
            df=df,
            trigger=base.high,
            stop=stop,
            final_target=base.measured_target,
            exit_plan=exit_plan,
            ctx=ctx,
            geometry={"base": base.model_dump(), "overhead": supply.model_dump()},
            reasons=[
                f"{base.length}-bar base, depth {base.depth_pct:.1%}, "
                f"contraction {base.contraction_ratio:.2f}, volume {base.volume_dryup:.2f}x",
                f"RS {ctx.rs_percentile:.0f}" if ctx.rs_percentile is not None else "RS n/a",
            ],
        )
