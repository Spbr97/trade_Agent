"""In-memory dashboard state (PLAN.md 7): health lights, regime, watchlist, triggers,
positions, heat, alert log. The session updates it; the FastAPI app serves it and pushes
every change to open pages over server-sent events."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tradedesk.backtest.fills import Position
from tradedesk.broker.indstocks.models import IST
from tradedesk.engine.lifecycle import TrackedSignal
from tradedesk.live.models import Alert
from tradedesk.scan.evening_scan import Watchlist

MAX_ALERTS = 200


@dataclass
class DashboardState:
    capital: float = 0.0
    health: dict[str, Any] = field(
        default_factory=lambda: {
            "feed": "unknown",
            "token": "unknown",
            "data_freshness": None,
            "last_tick_at": None,
            "paused": False,
        }
    )
    regime: dict[str, Any] | None = None
    watchlist: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    heat_pct: float = 0.0
    alerts: list[dict[str, Any]] = field(default_factory=list)
    prices: dict[str, float] = field(default_factory=dict)
    updated_at: datetime | None = None
    _subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    _loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------- updates

    def set_watchlist(self, wl: Watchlist) -> None:
        self.capital = wl.capital
        self.regime = wl.regime.model_dump(mode="json") if wl.regime else None
        self.watchlist = [
            {
                "symbol": e.signal.symbol,
                "code": e.signal.scrip_code,
                "setup": e.signal.setup.value,
                "grade": e.grade.value,
                "score": e.score,
                "trigger": e.signal.trigger,
                "stop": e.signal.stop,
                "t1": e.signal.t1,
                "t2": e.signal.t2,
                "qty": e.qty,
                "risk_pct": e.risk_pct,
                "net_rr_t2": e.net_rr_t2,
                "rejected_for": e.rejected_for,
                "chart": e.chart_path,
            }
            for e in wl.entries
        ]
        self.publish()

    def set_signals(self, signals: Sequence[TrackedSignal]) -> None:
        self.signals = [
            {
                "id": t.signal.id,
                "symbol": t.signal.symbol,
                "state": t.state.value,
                "trigger": t.signal.trigger,
                "stop": t.signal.stop,
                "last": t.history[-1].note if t.history else "",
            }
            for t in signals
        ]
        self.publish()

    def set_positions(self, positions: Sequence[Position], prices: dict[str, float]) -> None:
        rows = []
        risk = 0.0
        for p in positions:
            px = prices.get(p.signal.scrip_code, p.entry_price)
            risk += max(0.0, p.entry_price - p.stop) * p.qty_open
            rows.append(
                {
                    "symbol": p.signal.symbol,
                    "code": p.signal.scrip_code,
                    "qty": p.qty_open,
                    "entry": p.entry_price,
                    "stop": p.stop,
                    "price": px,
                    "pnl": (px - p.entry_price) * p.qty_open,
                    "r": p.unrealised_r(px),
                    "sessions": p.sessions_held,
                    "partial": p.partial_done,
                }
            )
        self.positions = rows
        self.heat_pct = risk / self.capital if self.capital else 0.0
        self.publish()

    def set_health(self, **kw: Any) -> None:
        self.health.update(kw)
        self.publish()

    def set_price(self, code: str, price: float, at: datetime) -> None:
        self.prices[code] = price
        self.health["last_tick_at"] = at.isoformat()
        # prices tick constantly; publish at most every second
        if self.updated_at is None or (at - self.updated_at).total_seconds() >= 1:
            self.publish(at)

    def add_alert(self, alert: Alert) -> None:
        self.alerts.insert(0, alert.model_dump(mode="json"))
        del self.alerts[MAX_ALERTS:]
        self.publish()

    # ------------------------------------------------------------ snapshot

    def snapshot(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "capital": self.capital,
            "health": self.health,
            "regime": self.regime,
            "watchlist": self.watchlist,
            "signals": self.signals,
            "positions": self.positions,
            "heat_pct": self.heat_pct,
            "prices": self.prices,
            "alerts": self.alerts[:50],
        }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def publish(self, at: datetime | None = None) -> None:
        self.updated_at = at or datetime.now(IST)
        if not self._subscribers:
            return
        snap = self.snapshot()
        for q in list(self._subscribers):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass  # slow page: it will catch up on the next event
