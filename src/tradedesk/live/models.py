"""Models shared by the market-hours components (M7) and the alert channels (M8)."""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntradayBar(BaseModel):
    """One 15-minute bar. `start` is the open time (09:15-anchored slot), `end` = start + 15m."""

    model_config = ConfigDict(frozen=True)

    scrip_code: str
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0  # 0 when the feed carries no volume (ltp mode)
    ticks: int = 0


class AlertKind(StrEnum):
    TRIGGERED = "triggered"
    CHASED = "chased"
    GAP_BELOW_STOP = "gap_below_stop"
    NEAR_STOP = "near_stop"
    STOP_HIT = "stop_hit"
    T1_REACHED = "t1_reached"
    CLOSE_CHECK = "close_check"
    TIME_STOP = "time_stop"
    DATA_STALE = "data_stale"
    DATA_OK = "data_ok"
    RESYNC = "resync"
    INFO = "info"


class AlertLevel(StrEnum):
    URGENT = "urgent"  # sound + Telegram
    WARNING = "warning"
    INFO = "info"


class Alert(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: AlertKind
    level: AlertLevel
    at: datetime
    scrip_code: str | None = None
    symbol: str | None = None
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """De-duplication key: one alert of a kind per instrument per session."""
        return f"{self.kind}:{self.scrip_code or ''}:{self.payload.get('dedupe', '')}"


class SessionRules(BaseModel):
    """Entry-timing rules (PLAN.md 6.6) and staleness policy (5.3)."""

    model_config = ConfigDict(frozen=True)

    session_open: time = time(9, 15)
    session_close: time = time(15, 30)
    no_entry_before: time = time(9, 30)
    late_trigger_after: time = time(15, 0)
    close_check_at: time = time(15, 15)
    bar_minutes: int = 15
    trigger_volume_ratio_min: float = 1.0  # bar volume vs the slot's average; 0 disables
    stale_after_seconds: int = 120
    near_stop_atr: float = 0.5
