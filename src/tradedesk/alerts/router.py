"""Route alerts to channels by grade and level (PLAN.md 6.8, 7, config/alerts.yaml).

- Signal alerts (TRIGGERED, CHASED) use the entry's grade: A -> desktop + Telegram +
  dashboard, B -> dashboard, C -> nothing (still logged).
- Position and health alerts route by level: urgent -> everything enabled, warning ->
  desktop + dashboard, info -> dashboard.
Telegram sends are queued and drained by `worker()` (or `flush()` in sync code) so the
monitor callback never waits on the network.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from tradedesk.alerts.cards import OutboundMessage, build_message, entry_for
from tradedesk.alerts.desktop import DesktopNotifier
from tradedesk.alerts.telegram import TelegramBot
from tradedesk.config.models import AlertsConfig
from tradedesk.dashboard.state import DashboardState
from tradedesk.live.models import Alert, AlertKind, AlertLevel
from tradedesk.scan.evening_scan import Watchlist

log = logging.getLogger(__name__)

SIGNAL_KINDS = {AlertKind.TRIGGERED, AlertKind.CHASED}


@dataclass
class AlertRouter:
    config: AlertsConfig
    watchlist: Watchlist | None = None
    desktop: DesktopNotifier | None = None
    telegram: TelegramBot | None = None
    dashboard: DashboardState | None = None
    routed: list[tuple[Alert, list[str]]] = field(default_factory=list)
    _queue: list[OutboundMessage] = field(default_factory=list)

    def channels_for(self, alert: Alert) -> list[str]:
        if alert.kind in SIGNAL_KINDS:
            e = entry_for(alert, self.watchlist)
            grade = e.grade.value if e is not None else "B"
            route = self.config.grades.get(grade)
            wanted = list(route.channels) if route else []
        elif alert.level is AlertLevel.URGENT:
            wanted = ["desktop", "telegram", "dashboard"]
        elif alert.level is AlertLevel.WARNING:
            wanted = ["desktop", "dashboard"]
        else:
            wanted = ["dashboard"]
        enabled = {
            "desktop": self.config.desktop.enabled and self.desktop is not None,
            "telegram": self.config.telegram.enabled and self.telegram is not None,
            "dashboard": self.dashboard is not None,
        }
        return [c for c in wanted if enabled.get(c)]

    def route(self, alert: Alert) -> list[str]:
        channels = self.channels_for(alert)
        self.routed.append((alert, channels))
        if self.dashboard is not None:
            self.dashboard.add_alert(alert)  # the log shows everything, routed or not
        if not channels:
            return []
        msg = build_message(alert, self.watchlist)
        if "desktop" in channels and self.desktop is not None:
            self.desktop.send(msg)
        if "telegram" in channels and self.telegram is not None:
            self._queue.append(msg)
        return channels

    async def flush(self) -> int:
        """Send queued Telegram messages now (used by replay and tests)."""
        n = 0
        if self.telegram is None:
            self._queue.clear()
            return 0
        while self._queue:
            msg = self._queue.pop(0)
            if await self.telegram.send(msg):
                n += 1
        return n

    async def worker(self, stop: asyncio.Event, interval: float = 1.0) -> None:
        while not stop.is_set():
            try:
                await self.flush()
            except Exception as exc:  # noqa: BLE001
                log.warning("alert flush failed: %s", exc)
            await asyncio.sleep(interval)
        await self.flush()
