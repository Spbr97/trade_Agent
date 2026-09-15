"""Run a market-hours session: watchlist -> TriggerMonitor <- price WebSocket, with the
periodic jobs from PLAN.md 2 (bar flush, staleness check, quote poll/resync, 15:15 close
check) and a JSONL recording for the replay harness. Alert delivery is a callback (M8)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from tradedesk.backtest.fills import Position
from tradedesk.broker.indstocks import IndstocksClient, PriceFeed, Tick
from tradedesk.broker.indstocks.models import IST
from tradedesk.dashboard.state import DashboardState
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.live.models import Alert, SessionRules
from tradedesk.live.trigger_monitor import TriggerMonitor
from tradedesk.replay.recorder import SessionRecorder
from tradedesk.scan.evening_scan import Watchlist

log = logging.getLogger(__name__)


def signals_from_watchlist(wl: Watchlist, *, alertable_only: bool = True) -> list[TrackedSignal]:
    entries = [e for e in wl.active if e.alertable] if alertable_only else wl.active
    return [TrackedSignal(signal=e.signal) for e in entries]


async def wait_for_eligible_signals(
    rebuild: Callable[[], Watchlist],
    *,
    until: time,
    poll_seconds: int,
    now: Callable[[], datetime] = lambda: datetime.now(IST),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_poll: Callable[[Watchlist], None] | None = None,
) -> Watchlist | None:
    """Poll `rebuild()` (a fresh watchlist rebuild against current stored data and current
    config/setups.yaml) every `poll_seconds` until it yields at least one alertable entry, or
    `until` (IST wall-clock time) is reached.

    Why this exists: eligibility (journal/stats.py::track_record, research_tracker's
    findings) only changes via the once-daily after-close/research-tracker jobs, and even
    then a newly-eligible setup only reaches config/setups.yaml through a human approving a
    review_queue.py item and editing the config by hand - never automatic (CLAUDE.md's hard
    rule that model/Claude output can never create or enable a live signal on its own).
    `tradedesk live` used to just exit the instant nothing was alertable at its 09:10
    startup, so a same-day approval had no way to go live without someone noticing and
    manually restarting the session. This closes that gap without weakening the approval
    gate itself - it only re-checks eligibility against whatever config and data already
    say, using the exact same `signals_from_watchlist` check as everywhere else; it never
    lowers a bar, invents a signal, or bypasses `config/setups.yaml`.

    Returns the first watchlist with something alertable, or None if `until` arrives first.
    `on_poll`, if given, is called with every rebuilt watchlist (including empty ones) so a
    caller can log/report each check.
    """
    while True:
        wl = rebuild()
        if on_poll is not None:
            on_poll(wl)
        if signals_from_watchlist(wl, alertable_only=True):
            return wl
        current = now()
        if current.time() >= until:
            return None
        until_dt = datetime.combine(current.date(), until, tzinfo=current.tzinfo)
        remaining = (until_dt - current).total_seconds()
        await sleep(min(poll_seconds, max(remaining, 1.0)))


def ws_code(scrip_code: str) -> str:
    exch, token = scrip_code.split("_", 1)
    return f"{exch}:{token}"


async def run_session(
    client: IndstocksClient,
    monitor: TriggerMonitor,
    *,
    codes: Sequence[str],
    rules: SessionRules,
    recording: Path | None,
    on_alert: Callable[[Alert], None],
    until: time = time(15, 35),
    quote_poll_seconds: int = 180,
    today: date | None = None,
    dashboard: DashboardState | None = None,
    extra_tasks: Sequence[Callable[[asyncio.Event], Awaitable[None]]] = (),
) -> None:
    today = today or datetime.now(IST).date()
    stop_at = datetime.combine(today, until, tzinfo=IST)
    close_check_at = datetime.combine(today, rules.close_check_at, tzinfo=IST)
    rec = SessionRecorder(recording) if recording else None
    token_to_code = {c.split("_", 1)[1]: c for c in codes}
    stop = asyncio.Event()
    close_checked = False
    monitor.on_alert = on_alert

    def on_tick(t: Tick) -> None:
        # Defensive only - every subscribed instrument's token is in token_to_code, so this
        # should never actually miss. It used to guess "NSE_<token>" on a miss, which would
        # silently mislabel a BSE (or any other market's) tick as NSE instead of just
        # leaving it unresolved - now it does the latter.
        code = token_to_code.get(t.instrument, t.instrument)
        if t.ltp is None:
            return
        if rec:
            rec.tick(t)
        alerts = monitor.on_tick(code, t.timestamp, t.ltp, t.data.get("volume"))
        if dashboard is not None:
            dashboard.set_price(code, t.ltp, t.timestamp)
            if alerts:
                dashboard.set_signals(monitor.signals)
                dashboard.set_positions(list(monitor.positions.values()), monitor.bars.last_price)

    feed = PriceFeed(client.tokens, on_tick, mode="ltp")
    await feed.subscribe([ws_code(c) for c in codes])

    async def resync_after_reconnect() -> None:
        quotes = await client.quotes_full(list(codes))
        snap = {k: q.model_dump() for k, q in quotes.items()}
        now = datetime.now(IST)
        if rec:
            rec.quotes(now, snap)
        monitor.resync(snap, now)

    feed.on_reconnect = resync_after_reconnect
    if dashboard is not None:

        def _down() -> None:
            dashboard.set_health(feed="disconnected")

        def _up() -> None:
            dashboard.set_health(feed="connected")

        feed.on_disconnect = _down
        dashboard.set_health(feed="connecting", token="ok" if client.tokens.token else "unknown")
        dashboard.set_signals(monitor.signals)

    async def housekeeping() -> None:
        nonlocal close_checked
        last_poll = datetime.now(IST)
        while not stop.is_set():
            now = datetime.now(IST)
            monitor.flush(now)
            monitor.check_staleness(now)
            if dashboard is not None:
                dashboard.set_health(
                    feed="connected" if feed.connected else "disconnected",
                    paused=monitor.paused,
                    last_tick_at=monitor.last_tick_at.isoformat() if monitor.last_tick_at else None,
                )
                dashboard.set_signals(monitor.signals)
            if now - last_poll >= timedelta(seconds=quote_poll_seconds):
                last_poll = now
                try:
                    quotes = await client.quotes_full(list(codes))
                    snap = {k: q.model_dump() for k, q in quotes.items()}
                    if rec:
                        rec.quotes(now, snap)
                    for code, q in snap.items():  # day volume reaches the bar builder
                        if q.get("volume") is not None and q.get("live_price") is not None:
                            monitor.on_tick(code, now, float(q["live_price"]), int(q["volume"]))
                except Exception as exc:  # noqa: BLE001 - keep the session alive
                    log.warning("quote poll failed: %s", exc)
            if not close_checked and now >= close_check_at:
                close_checked = True
                if rec:
                    rec.event(now, "close_check")
                monitor.run_close_check(now)
            if now >= stop_at:
                stop.set()
            if rec:
                rec.flush()
            await asyncio.sleep(15)

    try:
        await asyncio.gather(feed.run(stop), housekeeping(), *(t(stop) for t in extra_tasks))
    finally:
        if rec:
            rec.close()


def apply_decision(signals: Sequence[TrackedSignal], signal_id: str, action: str, on: date) -> bool:
    """Telegram 'Took it' / 'Skip' -> TAKEN/SKIPPED on the tracked signal."""
    for t in signals:
        if t.signal.id == signal_id and t.state is SignalState.TRIGGERED:
            t.move(SignalState.TAKEN if action == "took" else SignalState.SKIPPED, on, "telegram")
            return True
    return False


def positions_placeholder() -> list[Position]:
    """Open positions come from the journal in M9; until then the session starts flat."""
    return []
