"""Signal models shared by setups, the backtester and (from M6) the live scanner."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SetupKind(StrEnum):
    BASE_BREAKOUT = "base_breakout"
    TREND_PULLBACK = "trend_pullback"
    NR7_BREAKOUT = "nr7_breakout"


class ExitPlan(BaseModel):
    """How an open position is managed (PLAN.md 6.5 exits + 1.2 time stop)."""

    model_config = ConfigDict(frozen=True)

    partial_at_r: float = 2.0
    partial_fraction: float = 0.5
    trail: Literal["ema10_close", "atr"] = "ema10_close"
    trail_atr_mult: float = 2.0
    time_stop_sessions: int = 5
    time_stop_min_r: float = 1.0
    max_hold_sessions: int = 10


class Signal(BaseModel):
    """An ARMED setup: exact trigger, stop and targets, valid for `valid_sessions`.

    Prices are per share. `t1` is the partial target (partial_at_r), `t2` the final target
    used for the net reward:risk gate. `atr` is ATR(14) at the arming close, used for the
    'chased' rule (open more than chased_atr_mult x ATR past the trigger -> skip)."""

    model_config = ConfigDict(frozen=True)

    id: str
    scrip_code: str
    symbol: str
    setup: SetupKind
    armed_on: date
    trigger: float
    stop: float
    t1: float
    t2: float
    atr: float
    exit_plan: ExitPlan = ExitPlan()
    valid_sessions: int = 3
    chased_atr_mult: float = 1.0
    rs_percentile: float | None = None
    regime: str | None = None
    geometry: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)

    @property
    def risk_per_share(self) -> float:
        return self.trigger - self.stop

    @property
    def gross_rr_t2(self) -> float:
        return (self.t2 - self.trigger) / self.risk_per_share

    def r_level(self, r: float) -> float:
        return self.trigger + r * self.risk_per_share
