"""VWAP Reclaim (SDD section 7): "reclaim VWAP + strong close + volume expansion + retest +
continuation."

Arms when the arming bar: was BELOW session VWAP on the previous bar, closes back ABOVE
VWAP now (the reclaim), with a strong close (in the upper part of its own range, not just
scraping back over the line), on above-normal volume. Rejects outright on a multi-timeframe
conflict (SDD section 5) - this is the setup where MTF alignment is genuinely load-bearing,
since a VWAP reclaim against a higher-timeframe downtrend is a much weaker signal than one
with it.

The "retest + continuation" half of the SDD's description is deliberately NOT baked into
`arm()` itself: like every daily setup here, `arm()` only computes the geometry (trigger,
stop, targets) and returns an armed-but-unconfirmed signal - the trigger is set at the
reclaim bar's own high, so the actual entry only fires once price continues through it on a
LATER bar. That is the retest-and-continuation check, done the same way
`live/confirmation.py::confirm_trigger` does it for daily signals, not duplicated here.

This is a mechanical geometry only - it has not been validated against a random-timing
baseline (that is `null_baseline.py`, the next step in the plan) and must not alert or size
a real trade until it has. Building the setup and PROVING it have deliberately been kept
separate steps.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.engine.intraday_signals import (
    IntradayExitPlan,
    IntradaySetupKind,
    IntradaySignal,
)
from tradedesk.setups.intraday.base import IntradaySetupContext


class VwapReclaim:
    kind = IntradaySetupKind.VWAP_RECLAIM

    def arm(
        self, df: pd.DataFrame, ctx: IntradaySetupContext, params: dict[str, Any]
    ) -> IntradaySignal | None:
        if len(df) < 2:
            return None
        # SDD section 5: reject outright on a materially conflicting higher timeframe -
        # this setup's whole premise (buying strength resuming) is weakest exactly when the
        # bigger picture disagrees.
        if ctx.alignment is not None and ctx.alignment.conflict:
            return None

        last, prev = df.iloc[-1], df.iloc[-2]
        vwap_last, vwap_prev = last.get("vwap"), prev.get("vwap")
        if pd.isna(vwap_last) or pd.isna(vwap_prev):
            return None  # no session VWAP yet (too early in the session)

        was_below = prev["close"] < vwap_prev
        reclaimed = last["close"] > vwap_last
        if not (was_below and reclaimed):
            return None

        bar_range = last["high"] - last["low"]
        if bar_range <= 0:
            return None
        close_position = (last["close"] - last["low"]) / bar_range
        min_close_position = float(params.get("min_close_position", 0.6))
        if close_position < min_close_position:
            return None

        vol_ratio = last.get("vol_ratio20")
        min_vol_ratio = float(params.get("min_vol_ratio", 1.2))
        if vol_ratio is None or pd.isna(vol_ratio) or vol_ratio < min_vol_ratio:
            return None

        atr = last.get("atr14")
        if atr is None or pd.isna(atr) or atr <= 0:
            return None

        stop_atr_mult = float(params.get("stop_atr_mult", 0.5))
        trigger = float(last["high"])
        stop = float(min(last["low"], vwap_last - stop_atr_mult * atr))
        if stop >= trigger:
            return None

        risk = trigger - stop
        partial_at_r = float(params.get("partial_at_r", 2.0))
        final_r = float(params.get("final_r", 3.0))
        t1 = trigger + partial_at_r * risk
        t2 = trigger + final_r * risk
        armed_at = pd.Timestamp(df.index[-1])

        return IntradaySignal(
            id=f"{self.kind.value}:{ctx.scrip_code}:{armed_at.isoformat()}",
            scrip_code=ctx.scrip_code,
            symbol=ctx.symbol,
            setup=self.kind,
            interval=ctx.interval,
            armed_at=armed_at,
            trigger=trigger,
            stop=stop,
            t1=t1,
            t2=t2,
            atr=float(atr),
            exit_plan=IntradayExitPlan(
                partial_at_r=partial_at_r, max_hold_bars=ctx.max_hold_bars
            ),
            geometry={
                "close_position": round(float(close_position), 3),
                "vol_ratio20": round(float(vol_ratio), 3),
                "vwap": round(float(vwap_last), 4),
            },
            reasons=[
                "reclaimed VWAP after being below it",
                f"strong close ({close_position:.0%} of the bar's range)",
                f"volume {vol_ratio:.2f}x the 20-bar average",
                *(
                    [f"mtf agreement {ctx.alignment.agreement:.0%}"]
                    if ctx.alignment is not None
                    else ["no multi-timeframe context supplied"]
                ),
            ],
        )
