"""Setup protocol and shared building blocks (PLAN.md 6.5, CLAUDE.md conventions).

A setup receives the daily feature frame of ONE stock up to and including the arming
session, plus a context (relative-strength rank, regime, results proximity), and returns
a `Signal` or None. It never sees later bars; the caller slices the frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import pandas as pd

from tradedesk.config.models import PatternConfig
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal


@dataclass(frozen=True)
class SetupContext:
    scrip_code: str
    symbol: str
    rs_percentile: float | None = None
    regime: str | None = None
    results_in_sessions: int | None = None  # sessions until the next results date
    max_hold_sessions: int = 10


class Setup(Protocol):
    kind: SetupKind

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None: ...


@dataclass(frozen=True)
class TrendCheck:
    ok: bool
    reasons: list[str]


def uptrend(last: pd.Series, *, require_stack: bool = False) -> TrendCheck:
    """Price above a rising EMA50; with `require_stack`, also 20 > 50 > 200 all rising."""
    reasons: list[str] = []
    ok = True
    if not (last["close"] > last["ema50"] and last["ema50_slope"] > 0):
        ok = False
        reasons.append("not above a rising EMA50")
    if require_stack:
        stacked = last["ema20"] > last["ema50"] > last["ema200"]
        rising = last["ema20_slope"] > 0 and last["ema50_slope"] > 0 and last["ema200_slope"] > 0
        if not (stacked and rising):
            ok = False
            reasons.append("EMAs not stacked 20>50>200 and rising")
    return TrendCheck(ok, reasons)


def rs_ok(ctx: SetupContext, params: dict[str, Any], key: str = "rs_percentile_min") -> bool:
    need = params.get(key)
    if need is None:
        return True
    return ctx.rs_percentile is not None and ctx.rs_percentile >= float(need)


def results_ok(ctx: SetupContext) -> bool:
    """PLAN.md 6.7: reject if results fall inside the maximum holding window."""
    return ctx.results_in_sessions is None or ctx.results_in_sessions > ctx.max_hold_sessions


def make_signal(
    *,
    kind: SetupKind,
    df: pd.DataFrame,
    trigger: float,
    stop: float,
    final_target: float | None,
    exit_plan: ExitPlan,
    ctx: SetupContext,
    geometry: dict[str, Any],
    reasons: list[str],
    valid_sessions: int = 3,
) -> Signal | None:
    """Assemble a Signal; returns None when the geometry is unusable."""
    last = df.iloc[-1]
    risk = trigger - stop
    if risk <= 0 or trigger <= 0:
        return None
    t1 = trigger + exit_plan.partial_at_r * risk
    t2 = final_target if final_target is not None and final_target > t1 else trigger + 3 * risk
    armed_on: date = pd.Timestamp(df.index[-1]).date()
    return Signal(
        id=f"{kind.value}:{ctx.scrip_code}:{armed_on.isoformat()}",
        scrip_code=ctx.scrip_code,
        symbol=ctx.symbol,
        setup=kind,
        armed_on=armed_on,
        trigger=float(trigger),
        stop=float(stop),
        t1=float(t1),
        t2=float(t2),
        atr=float(last["atr14"]),
        exit_plan=exit_plan,
        valid_sessions=valid_sessions,
        rs_percentile=ctx.rs_percentile,
        regime=ctx.regime,
        geometry=geometry,
        reasons=reasons,
    )


def pattern_config(params: dict[str, Any]) -> PatternConfig:
    """Pattern geometry can be overridden per setup in setups.yaml; defaults otherwise."""
    overrides = {k: v for k, v in params.items() if k in PatternConfig.model_fields}
    return PatternConfig.model_validate(overrides)
