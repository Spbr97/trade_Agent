"""Market-hours monitor (PLAN.md 2, 5.3, 6.6, 8).

Feed ticks in (from the WebSocket, or the replayer); it builds 15-minute bars, confirms
triggers on bar closes, runs the gap check on each instrument's first tick of the day,
watches held positions on every tick, fails closed when the feed goes stale, and re-checks
every trigger and stop against the day's high/low after a reconnect. Alerts are collected
and handed to `on_alert` (M8 wires the channels). Pure Python, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, time
from typing import Any

from tradedesk.backtest.fills import Position
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.live.bars import BarBuilder
from tradedesk.live.confirmation import Confirmation, Decision, confirm_trigger, is_chased
from tradedesk.live.gap_check import gap_check_positions, gap_check_signals
from tradedesk.live.models import Alert, AlertKind, AlertLevel, IntradayBar, SessionRules
from tradedesk.live.position_watch import close_check, watch_price

DEDUPED = {
    AlertKind.TRIGGERED,
    AlertKind.CHASED,
    AlertKind.GAP_BELOW_STOP,
    AlertKind.NEAR_STOP,
    AlertKind.STOP_HIT,
    AlertKind.T1_REACHED,
    AlertKind.CLOSE_CHECK,
    AlertKind.TIME_STOP,
    AlertKind.DATA_STALE,
}


class TriggerMonitor:
    def __init__(
        self,
        rules: SessionRules,
        *,
        signals: Sequence[TrackedSignal],
        positions: Sequence[Position] = (),
        atr_by_code: Mapping[str, float] | None = None,
        ema10_by_code: Mapping[str, float] | None = None,
        slot_volume_norms: Mapping[str, Mapping[time, float]] | None = None,
        on_alert: Callable[[Alert], None] | None = None,
    ) -> None:
        self.rules = rules
        self.signals: list[TrackedSignal] = list(signals)
        self.positions: dict[str, Position] = {p.signal.scrip_code: p for p in positions}
        self.atr = dict(atr_by_code or {})
        self.ema10 = dict(ema10_by_code or {})
        self.slot_norms = {k: dict(v) for k, v in (slot_volume_norms or {}).items()}
        self.on_alert = on_alert
        self.bars = BarBuilder(bar_minutes=rules.bar_minutes, session_open=rules.session_open)
        self.alerts: list[Alert] = []
        self.confirmations: dict[str, Confirmation] = {}  # signal id -> how it triggered
        self.bars_seen: list[IntradayBar] = []
        self.last_tick_at: datetime | None = None
        self.paused = False
        self._sent: set[str] = set()
        self._gap_checked: set[str] = set()
        self._suppressed: list[Alert] = []  # raised while the feed was stale; sent on resume

    # ----------------------------------------------------------------- utils

    @property
    def armed(self) -> list[TrackedSignal]:
        return [t for t in self.signals if t.state is SignalState.ARMED]

    def _emit(self, alert: Alert, out: list[Alert]) -> None:
        if alert.kind in DEDUPED:
            if alert.key in self._sent:
                return
            self._sent.add(alert.key)
        if self.paused and alert.kind not in (AlertKind.DATA_STALE, AlertKind.DATA_OK):
            self._suppressed.append(alert)  # fail closed now, deliver (marked) on resume
            return
        self.alerts.append(alert)
        out.append(alert)
        if self.on_alert:
            self.on_alert(alert)

    # ----------------------------------------------------------------- input

    def on_tick(
        self, code: str, ts: datetime, price: float, day_volume: int | None = None
    ) -> list[Alert]:
        out: list[Alert] = []
        self.last_tick_at = ts
        if self.paused:
            self.paused = False
            held = self._suppressed
            self._suppressed = []
            self._emit(
                Alert(
                    kind=AlertKind.DATA_OK,
                    level=AlertLevel.INFO,
                    at=ts,
                    message=f"price feed resumed; {len(held)} alert(s) raised while stale follow",
                ),
                out,
            )
            for a in held:
                delayed = a.model_copy(update={"message": "[delayed] " + a.message})
                self.alerts.append(delayed)
                out.append(delayed)
                if self.on_alert:
                    self.on_alert(delayed)
        closed = self.bars.on_tick(code, ts, price, day_volume)
        if code not in self._gap_checked:
            self._gap_checked.add(code)
            opens = {code: price}
            for a in gap_check_signals(self.signals, opens, ts):
                self._emit(a, out)
            pos = self.positions.get(code)
            if pos is not None:
                for a in gap_check_positions([pos], opens, ts):
                    self._emit(a, out)
        if closed is not None:
            out.extend(self.on_bar(closed))
        pos = self.positions.get(code)
        if pos is not None:
            for a in watch_price(pos, price, self.atr.get(code, 0.0), ts, self.rules):
                self._emit(a, out)
        return out

    def on_bar(self, bar: IntradayBar) -> list[Alert]:
        out: list[Alert] = []
        self.bars_seen.append(bar)
        for ts in self.armed:
            sig = ts.signal
            if sig.scrip_code != bar.scrip_code:
                continue
            c = confirm_trigger(
                sig, bar, self.rules, slot_volume_norm=self.slot_norms.get(sig.scrip_code)
            )
            if c.decision is Decision.TRIGGERED:
                ts.move(SignalState.TRIGGERED, bar.end.date(), c.note)
                self.confirmations[sig.id] = c
                self._emit(
                    Alert(
                        kind=AlertKind.TRIGGERED,
                        level=AlertLevel.URGENT,
                        at=bar.end,
                        scrip_code=sig.scrip_code,
                        symbol=sig.symbol,
                        message=f"TRIGGERED {sig.symbol} {sig.setup.value}: {c.note}",
                        payload={
                            "signal_id": sig.id,
                            "fill": c.fill_price,
                            "bar_end": bar.end.isoformat(),
                        },
                    ),
                    out,
                )
            elif c.decision is Decision.INVALIDATED:
                ts.move(SignalState.INVALIDATED, bar.end.date(), c.note)
                self._emit(
                    Alert(
                        kind=AlertKind.INFO,
                        level=AlertLevel.INFO,
                        at=bar.end,
                        scrip_code=sig.scrip_code,
                        symbol=sig.symbol,
                        message=f"{sig.symbol}: invalidated - {c.note}",
                    ),
                    out,
                )
            elif c.decision is Decision.LATE:
                self._emit(
                    Alert(
                        kind=AlertKind.INFO,
                        level=AlertLevel.INFO,
                        at=bar.end,
                        scrip_code=sig.scrip_code,
                        symbol=sig.symbol,
                        message=(
                            f"{sig.symbol}: closed above the trigger after "
                            f"{self.rules.late_trigger_after:%H:%M}; wait for the daily close"
                        ),
                        payload={"dedupe": bar.start.isoformat()},
                    ),
                    out,
                )
        return out

    def flush(self, now: datetime) -> list[Alert]:
        out: list[Alert] = []
        for bar in self.bars.flush(now):
            out.extend(self.on_bar(bar))
        return out

    # ------------------------------------------------------------- housekeeping

    def check_staleness(self, now: datetime) -> list[Alert]:
        out: list[Alert] = []
        if self.last_tick_at is None or self.paused:
            return out
        age = (now - self.last_tick_at).total_seconds()
        if age >= self.rules.stale_after_seconds:
            self.paused = True
            self._emit(
                Alert(
                    kind=AlertKind.DATA_STALE,
                    level=AlertLevel.URGENT,
                    at=now,
                    message=f"DATA STALE: no price update for {age:.0f}s - alerts paused",
                    payload={"dedupe": self.last_tick_at.isoformat()},
                ),
                out,
            )
        return out

    def resync(self, quotes: Mapping[str, Mapping[str, Any]], now: datetime) -> list[Alert]:
        """After a reconnect: compare every trigger and stop with the day's high/low from
        REST quotes (`day_open`, `day_high`, `day_low`, `live_price`) before resuming."""
        out: list[Alert] = []
        for ts in self.armed:
            sig = ts.signal
            q = quotes.get(sig.scrip_code)
            if not q:
                continue
            o, hi = q.get("day_open"), q.get("day_high")
            if o is not None and is_chased(sig, float(o)):
                ts.move(SignalState.CHASED, now.date(), f"open {float(o):.2f} > trigger + ATR")
                self._emit(
                    Alert(
                        kind=AlertKind.CHASED,
                        level=AlertLevel.INFO,
                        at=now,
                        scrip_code=sig.scrip_code,
                        symbol=sig.symbol,
                        message=f"{sig.symbol}: chased at the open (found on resync)",
                    ),
                    out,
                )
            elif hi is not None and float(hi) >= sig.trigger:
                self._emit(
                    Alert(
                        kind=AlertKind.RESYNC,
                        level=AlertLevel.WARNING,
                        at=now,
                        scrip_code=sig.scrip_code,
                        symbol=sig.symbol,
                        message=(
                            f"{sig.symbol}: day high {float(hi):.2f} >= trigger "
                            f"{sig.trigger:.2f} while disconnected; awaiting a 15m close"
                        ),
                        payload={"dedupe": "trigger"},
                    ),
                    out,
                )
        for code, pos in self.positions.items():
            q = quotes.get(code)
            if not q or q.get("day_low") is None:
                continue
            lo = float(q["day_low"])
            if lo <= pos.stop:
                self._emit(
                    Alert(
                        kind=AlertKind.STOP_HIT,
                        level=AlertLevel.URGENT,
                        at=now,
                        scrip_code=code,
                        symbol=pos.signal.symbol,
                        message=(
                            f"{pos.signal.symbol}: day low {lo:.2f} <= stop {pos.stop:.2f} "
                            "while disconnected - check the position now"
                        ),
                        payload={
                            "price": q.get("live_price"),
                            "stop": pos.stop,
                            "qty": pos.qty_open,
                        },
                    ),
                    out,
                )
        return out

    def run_close_check(self, now: datetime) -> list[Alert]:
        out: list[Alert] = []
        for code, pos in self.positions.items():
            price = self.bars.last_price.get(code)
            if price is None:
                continue
            for a in close_check(pos, price, self.ema10.get(code), now):
                self._emit(a, out)
        return out
