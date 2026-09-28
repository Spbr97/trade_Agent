"""A concrete, real LabSetup: `rsi2<10 & close>ema50`, the single best-measured
GROSS-edge rule in this project's entire history (`scripts/entry_search.py`, +0.077R vs a
random-timing null, t=7.04) - wrapped into the production trigger/stop/partial-target
shape instead of `entry_search.py`'s own simplified fixed-target model, so it can run
through the real, tested fill/exit primitives (`backtest/fills.py`) and the full harness
gauntlet.

This is Phase 3's first real candidate, used to prove the authoring/validation/promotion
MACHINERY works end to end - it is not expected to newly succeed where this exact rule
has already failed twice under different execution models this project has tried
(`entry_search.py optimize`'s own geometry search, and `research_tracker.py`'s forward
tracking as `rsi2_dip_ema50`, both net-negative after costs). If this candidate also
fails the gauntlet, that is the honest, informative outcome Phase 3 exists to produce
mechanically instead of needing a human to re-derive it by hand each time.

Disclosed approximations, relative to `entry_search.py`'s own model:
- `entry_search.py` enters unconditionally at the next bar's open. Here, `trigger` is set
  to the signal bar's own close, so `evaluate_entry` fills at the next bar's open whenever
  it is at or above that close (the common case for a name that doesn't gap down) - a
  close, but not identical, proxy for "always enter at the next open."
- Geometry (3x ATR stop, 2R target, 10-session hold) is the exact winning cell this
  project's own locked-test search already found for this rule
  (`docs/daily-mean-reversion-exit-search-checkpoint.md`) - reused for comparability, not
  re-searched.
- `partial_fraction=1.0` (the whole position exits at the 2R target, no runner) matches
  this project's own already-measured, already-applied decision for the three live
  setups (`config/setups.yaml`'s 2026-09-13 note: +0.114R/trade from taking the full
  position off at T1 instead of running half), so nothing is left open to trail.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradedesk.engine.signals import ExitPlan
from tradedesk.setups.base import SetupContext

from .base import LabSignal

STOP_ATR_MULT = 3.0
TARGET_R = 2.0
MAX_HOLD_SESSIONS = 10


class MeanReversionV1:
    name = "mean_reversion_v1"

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> LabSignal | None:
        if len(df) < 210:  # ema200/atr14 warmup, matching daily_features' own requirements
            return None
        last = df.iloc[-1]
        if pd.isna(last.get("rsi2")) or pd.isna(last.get("ema50")) or pd.isna(last.get("atr14")):
            return None
        if not (last["rsi2"] < 10 and last["close"] > last["ema50"]):
            return None
        atr = float(last["atr14"])
        trigger = float(last["close"])
        if atr <= 0 or trigger <= 0:
            return None
        stop = trigger - STOP_ATR_MULT * atr
        if stop <= 0:
            return None
        risk = trigger - stop
        t1 = trigger + TARGET_R * risk
        armed_on = pd.Timestamp(df.index[-1]).date()
        return LabSignal(
            id=f"{self.name}:{ctx.scrip_code}:{armed_on.isoformat()}",
            scrip_code=ctx.scrip_code,
            symbol=ctx.symbol,
            armed_on=armed_on,
            trigger=trigger,
            stop=stop,
            t1=t1,
            t2=t1,
            atr=atr,
            exit_plan=ExitPlan(
                partial_at_r=TARGET_R,
                partial_fraction=1.0,
                trail="ema10_close",
                max_hold_sessions=MAX_HOLD_SESSIONS,
            ),
            valid_sessions=1,
            reasons=(f"rsi2={last['rsi2']:.1f} < 10, close above rising-or-flat ema50",),
        )
