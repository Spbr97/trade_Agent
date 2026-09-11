"""Text for outbound alerts (PLAN.md 7): a TRIGGERED alert carries the trade card; other
alerts carry a one-liner. Channels only format, never compute."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tradedesk.live.models import Alert, AlertKind
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry
from tradedesk.scan.watchlist_report import trade_card


@dataclass(frozen=True)
class OutboundMessage:
    title: str
    body: str
    image: Path | None = None
    signal_id: str | None = None  # present -> Telegram gets Took it / Skip buttons
    urgent: bool = False


def entry_for(alert: Alert, wl: Watchlist | None) -> WatchlistEntry | None:
    if wl is None or not alert.scrip_code:
        return None
    sid = alert.payload.get("signal_id")
    for e in wl.entries:
        if (sid and e.signal.id == sid) or (not sid and e.signal.scrip_code == alert.scrip_code):
            return e
    return None


def build_message(alert: Alert, wl: Watchlist | None = None) -> OutboundMessage:
    e = entry_for(alert, wl)
    if alert.kind is AlertKind.TRIGGERED and e is not None:
        fill = alert.payload.get("fill")
        head = f"TRIGGERED · {e.signal.symbol} · {e.grade.value} ({e.score})"
        body = trade_card(e, wl.capital if wl else 0.0)
        if fill is not None:
            body = f"Entry   {float(fill):>10.2f}  {alert.message.split(': ', 1)[-1]}\n" + body
        image = Path(e.chart_path) if e.chart_path and Path(e.chart_path).exists() else None
        return OutboundMessage(head, body, image, e.signal.id, urgent=True)
    title = f"{alert.kind.value.replace('_', ' ').upper()}" + (
        f" · {alert.symbol}" if alert.symbol else ""
    )
    return OutboundMessage(title, alert.message, None, None, urgent=alert.level.value == "urgent")
