"""Trigger notes (PLAN.md 9): a one-line read of the 15-minute chart AFTER a TRIGGERED
alert has gone out. The note is requested in a background task and delivered as a
follow-up INFO alert; the trigger alert itself never waits on the API (tests prove it)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from tradedesk.broker.indstocks.models import IST
from tradedesk.claude.advisor import ClaudeAdvisor
from tradedesk.claude.chart_read import setup_facts
from tradedesk.live.models import Alert, AlertKind, AlertLevel, IntradayBar
from tradedesk.scan.evening_scan import Watchlist

log = logging.getLogger(__name__)


def bar_summary(bars: Sequence[IntradayBar], last: int = 6) -> str:
    rows = [
        f"{b.start:%H:%M} o{b.open:.2f} h{b.high:.2f} l{b.low:.2f} c{b.close:.2f} v{b.volume}"
        for b in bars[-last:]
    ]
    return "; ".join(rows) if rows else "no bars"


class TriggerNoteWorker:
    """Queue trigger alerts; a background task asks Claude and emits the note later."""

    def __init__(
        self,
        advisor: ClaudeAdvisor,
        watchlist: Watchlist,
        emit: Callable[[Alert], None],
        *,
        bars_for: Callable[[str], Sequence[IntradayBar]],
    ) -> None:
        self.advisor = advisor
        self.watchlist = watchlist
        self.emit = emit
        self.bars_for = bars_for
        self.queue: asyncio.Queue[Alert] = asyncio.Queue()
        self.notes: list[Alert] = []

    def on_alert(self, alert: Alert) -> None:
        """Non-blocking: only enqueues. Safe to call from the monitor callback."""
        if alert.kind is AlertKind.TRIGGERED and self.advisor.enabled:
            self.queue.put_nowait(alert)

    async def _note_for(self, alert: Alert) -> Alert | None:
        sid = alert.payload.get("signal_id")
        entry = next((e for e in self.watchlist.entries if e.signal.id == sid), None)
        if entry is None:
            return None
        loop = asyncio.get_running_loop()
        note = await loop.run_in_executor(
            None,
            self.advisor.trigger_note,
            setup_facts(entry),
            bar_summary(self.bars_for(entry.signal.scrip_code)),
        )
        if note is None:
            return None
        return Alert(
            kind=AlertKind.INFO,
            level=AlertLevel.INFO,
            at=datetime.now(IST),
            scrip_code=entry.signal.scrip_code,
            symbol=entry.signal.symbol,
            message=f"Claude on the 15m chart: {note.note}",
            payload={"signal_id": sid, "note": True},
        )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                alert = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                note = await self._note_for(alert)
            except Exception as exc:  # noqa: BLE001 - advisory
                log.warning("trigger note failed: %s", exc)
                continue
            if note is not None:
                self.notes.append(note)
                self.emit(note)
