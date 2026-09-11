"""The 15-minute confirmation rule (PLAN.md 6.6), shared by the live monitor and the
backtester (when 15-minute history is available).

A signal triggers on a bar when:
- the bar CLOSES above the trigger level (a touch is not enough),
- the bar does not end before `no_entry_before` (the 09:15-09:30 bar never triggers),
- the bar does not start at/after `late_trigger_after` (late triggers wait for the daily
  close and fill next session - the daily path handles that),
- bar volume >= trigger_volume_ratio_min x the slot's normal volume, when both are known.
A bar that closes below the stop INVALIDATES the signal. The day's open beyond
trigger + chased_atr_mult x ATR marks it CHASED - that is the gap check's job, done at
09:15 before any bar closes (gap_check.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from tradedesk.engine.signals import Signal
from tradedesk.live.models import IntradayBar, SessionRules


class Decision(StrEnum):
    NONE = "none"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"
    LATE = "late"  # closed above the level after the cut-off: defer to the daily close
    WINDOW = "window"  # closed above the level inside the no-entry window: ignore
    THIN = "thin"  # closed above the level on volume below the slot's norm


@dataclass(frozen=True)
class Confirmation:
    decision: Decision
    fill_price: float | None = None
    volume_ratio: float | None = None
    note: str = ""


def confirm_trigger(
    sig: Signal,
    bar: IntradayBar,
    rules: SessionRules,
    *,
    slot_volume_norm: Mapping[time, float] | None = None,
) -> Confirmation:
    if bar.close < sig.stop:
        return Confirmation(Decision.INVALIDATED, note=f"15m close {bar.close:.2f} below stop")
    if bar.close <= sig.trigger:
        return Confirmation(Decision.NONE)
    end_t = bar.end.timetz().replace(tzinfo=None)
    start_t = bar.start.timetz().replace(tzinfo=None)
    if end_t <= rules.no_entry_before:
        return Confirmation(Decision.WINDOW, note="inside the no-entry window")
    if start_t >= rules.late_trigger_after:
        return Confirmation(Decision.LATE, note="after the late-trigger cut-off")
    ratio: float | None = None
    if slot_volume_norm and bar.volume > 0 and rules.trigger_volume_ratio_min > 0:
        norm = slot_volume_norm.get(start_t)
        if norm and norm > 0:
            ratio = bar.volume / norm
            if ratio < rules.trigger_volume_ratio_min:
                return Confirmation(
                    Decision.THIN, volume_ratio=ratio, note=f"volume {ratio:.2f}x the slot norm"
                )
    return Confirmation(
        Decision.TRIGGERED,
        fill_price=bar.close,
        volume_ratio=ratio,
        note=f"15m close {bar.close:.2f} > {sig.trigger:.2f}"
        + (f", volume {ratio:.1f}x" if ratio is not None else ""),
    )


def is_chased(sig: Signal, day_open: float) -> bool:
    return day_open > sig.trigger + sig.chased_atr_mult * sig.atr
