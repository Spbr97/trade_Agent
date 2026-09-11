"""M8: trade-card messages, desktop notifier, Telegram bot, router, dashboard, and
'cards with charts arrive during replay'."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from tests.engine.charts import flat, frame, trend
from tradedesk.alerts import AlertRouter, DesktopNotifier, TelegramBot, build_message
from tradedesk.alerts.charts import render_signal_chart
from tradedesk.broker.indstocks.models import IST
from tradedesk.config.models import AlertsConfig
from tradedesk.dashboard import DashboardState, create_app
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.engine.regime import Regime, RegimeSnapshot
from tradedesk.engine.scoring import Grade
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.live.models import Alert, AlertKind, AlertLevel, SessionRules
from tradedesk.live.session import apply_decision
from tradedesk.live.trigger_monitor import TriggerMonitor
from tradedesk.replay import SessionRecorder, read_session, replay
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry

D = date(2026, 3, 2)


def at(h: int, m: int, s: int = 0) -> datetime:
    return datetime.combine(D, time(h, m, s), tzinfo=IST)


def sig(code: str = "NSE_1", trigger: float = 100.0) -> Signal:
    return Signal(
        id=f"base_breakout:{code}:2026-03-01", scrip_code=code, symbol="ONE",
        setup=SetupKind.BASE_BREAKOUT,
        armed_on=D - timedelta(days=1), trigger=trigger, stop=95.0, t1=110.0, t2=115.0, atr=2.0,
        reasons=["15-bar base"],
    )  # fmt: skip


def entry(grade: Grade = Grade.A, chart: str | None = None) -> WatchlistEntry:
    return WatchlistEntry(
        signal=sig(), score=85 if grade is Grade.A else 70 if grade is Grade.B else 50, grade=grade,
        score_components={}, score_notes=[], alertable=grade is not Grade.C, qty=50,
        risk_amount=250.0,
        risk_pct=0.0025, position_value=5000.0, size_caps=[], costs_round_trip=60.0, net_rr_t1=1.6,
        net_rr_t2=2.4, results_in_sessions=None, heat_before_pct=0.0, heat_after_pct=0.0025,
        atr_pct=2.0, avg_turnover=1e8, chart_path=chart,
    )  # fmt: skip


def watchlist(e: WatchlistEntry) -> Watchlist:
    regime = RegimeSnapshot(
        on=D, regime=Regime.RISK_ON, benchmark_close=1.0, benchmark_ema=1.0, ema_rising=True,
        above_ema=True, breadth_pct=60.0, vix=12.0, vix_change_5d_pct=0.0, size_multiplier=1.0,
        reasons=["fine"],
    )  # fmt: skip
    return Watchlist(
        on=D,
        generated_at=at(16, 30),
        regime=regime,
        capital=100000.0,
        entries=[e],
        open_positions=[],
    )


def triggered_alert() -> Alert:
    return Alert(
        kind=AlertKind.TRIGGERED, level=AlertLevel.URGENT, at=at(9, 45), scrip_code="NSE_1",
        symbol="ONE",
        message="TRIGGERED ONE base_breakout: 15m close 100.90 > 100.00",
        payload={"signal_id": sig().id, "fill": 100.9},
    )  # fmt: skip


@pytest.fixture(scope="module")
def chart_png(tmp_path_factory: pytest.TempPathFactory) -> Path:
    up = trend(60, start=100, step_pct=0.006, seed=1)
    df = daily_features(frame(up + flat(15, up[-1] * 0.99, seed=2)))
    out = tmp_path_factory.mktemp("charts") / "ONE.png"
    return render_signal_chart(df, sig(), out)


# ------------------------------------------------------------------- cards


def test_build_message_trade_card_and_one_liner(chart_png: Path) -> None:
    wl = watchlist(entry(chart=str(chart_png)))
    msg = build_message(triggered_alert(), wl)
    assert msg.title.startswith("TRIGGERED · ONE · A (85)")
    assert "Entry       100.90" in msg.body and "Trigger" in msg.body and "Qty" in msg.body
    assert msg.image == chart_png and msg.signal_id == sig().id and msg.urgent
    other = Alert(
        kind=AlertKind.NEAR_STOP,
        level=AlertLevel.WARNING,
        at=at(10, 0),
        scrip_code="NSE_9",
        symbol="NINE",
        message="near stop",
    )
    plain = build_message(other, wl)
    assert plain.title == "NEAR STOP · NINE" and plain.signal_id is None and not plain.urgent


# ----------------------------------------------------------------- desktop


def test_desktop_notifier_uses_injected_toast_and_swallows_failures() -> None:
    calls: list[tuple[str, str]] = []
    beeps: list[bool] = []
    n = DesktopNotifier(sound=True, toast=lambda t, b: calls.append((t, b)), beep=beeps.append)
    msg = build_message(triggered_alert(), watchlist(entry()))
    assert n.send(msg) and n.sent == 1
    assert calls and calls[0][0].startswith("TRIGGERED") and beeps == [True]

    def boom(t: str, b: str) -> None:
        raise RuntimeError("no toast today")

    bad = DesktopNotifier(toast=boom, beep=beeps.append)
    assert bad.send(msg) is False and bad.sent == 0


# ---------------------------------------------------------------- telegram


@respx.mock
async def test_telegram_send_text_photo_and_buttons(chart_png: Path) -> None:
    api = "https://api.telegram.org/botTOKEN"
    send = respx.post(f"{api}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    photo = respx.post(f"{api}/sendPhoto").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient() as http:
        bot = TelegramBot(token="TOKEN", chat_id=42, http=http)
        assert await bot.send(build_message(triggered_alert(), watchlist(entry())))
        body = json.loads(send.calls[0].request.content)
        assert body["chat_id"] == 42 and "Took it" in json.dumps(body["reply_markup"])
        assert "took:" + sig().id in json.dumps(body["reply_markup"])
        assert await bot.send(
            build_message(triggered_alert(), watchlist(entry(chart=str(chart_png))))
        )
        assert photo.call_count == 1 and b"image/png" in photo.calls[0].request.content
        assert bot.sent == 2


@respx.mock
async def test_telegram_poll_handles_buttons_from_allowed_chat_only() -> None:
    api = "https://api.telegram.org/botTOKEN"
    updates = {
        "ok": True,
        "result": [
            {
                "update_id": 10,
                "callback_query": {
                    "id": "c1",
                    "data": f"took:{sig().id}",
                    "message": {"chat": {"id": 42}},
                },
            },
            {
                "update_id": 11,
                "callback_query": {
                    "id": "c2",
                    "data": f"skip:{sig().id}",
                    "message": {"chat": {"id": 999}},
                },
            },
        ],
    }
    respx.get(f"{api}/getUpdates").mock(return_value=httpx.Response(200, json=updates))
    answered = respx.post(f"{api}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    decisions: list[tuple[str, str]] = []
    async with httpx.AsyncClient() as http:
        bot = TelegramBot(token="TOKEN", chat_id=42, http=http)
        handled = await bot.poll_once(lambda s, a: decisions.append((s, a)), timeout=0)
    assert handled == 1 and decisions == [(sig().id, "took")] and answered.call_count == 1
    assert bot._offset == 12


def test_apply_decision_moves_triggered_signal() -> None:
    ts = TrackedSignal(signal=sig())
    ts.move(SignalState.TRIGGERED, D)
    assert apply_decision([ts], sig().id, "took", D) and ts.state is SignalState.TAKEN
    assert not apply_decision([ts], sig().id, "skip", D)  # already decided


# ------------------------------------------------------------------ router


def test_router_routes_by_grade_and_level() -> None:
    cfg = AlertsConfig.model_validate(
        {"desktop": {"enabled": True}, "telegram": {"enabled": True, "allowed_chat_id": 42}}
    )
    calls: list[str] = []
    desk = DesktopNotifier(toast=lambda t, b: calls.append(t), beep=lambda u: None)
    bot = TelegramBot(token="T", chat_id=42)
    dash = DashboardState()
    r = AlertRouter(
        cfg, watchlist=watchlist(entry(Grade.A)), desktop=desk, telegram=bot, dashboard=dash
    )
    assert r.route(triggered_alert()) == ["desktop", "telegram", "dashboard"]
    assert len(r._queue) == 1 and dash.alerts and calls
    r.watchlist = watchlist(entry(Grade.B))
    assert r.route(triggered_alert()) == ["dashboard"]
    r.watchlist = watchlist(entry(Grade.C))
    assert r.route(triggered_alert()) == []
    warn = Alert(
        kind=AlertKind.NEAR_STOP,
        level=AlertLevel.WARNING,
        at=at(10, 0),
        scrip_code="NSE_9",
        message="x",
    )
    assert r.route(warn) == ["desktop", "dashboard"]
    info = Alert(kind=AlertKind.INFO, level=AlertLevel.INFO, at=at(10, 0), message="y")
    assert r.route(info) == ["dashboard"]
    off = AlertRouter(
        AlertsConfig(), watchlist=watchlist(entry(Grade.A)), desktop=desk, telegram=bot
    )
    assert off.route(triggered_alert()) == []  # channels disabled in config, no dashboard


# --------------------------------------------------------------- dashboard


async def test_dashboard_state_and_endpoints() -> None:
    state = DashboardState()
    state.set_watchlist(watchlist(entry()))
    ts = TrackedSignal(signal=sig())
    state.set_signals([ts])
    state.add_alert(triggered_alert())
    state.set_health(feed="connected", token="ok")
    app = create_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        page = await c.get("/")
        assert page.status_code == 200 and "tradedesk" in page.text and "EventSource" in page.text
        snap = (await c.get("/api/state")).json()
        assert snap["watchlist"][0]["symbol"] == "ONE" and snap["regime"]["regime"] == "risk_on"
        assert snap["signals"][0]["state"] == "armed" and snap["health"]["feed"] == "connected"
        assert snap["alerts"][0]["kind"] == "triggered"
        assert (await c.get("/chart", params={"path": "nope.png"})).status_code == 404


async def test_dashboard_sse_over_real_server() -> None:
    import socket

    import uvicorn

    state = DashboardState()
    state.set_watchlist(watchlist(entry()))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.1)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as c:
            async with c.stream("GET", "/events") as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                lines = r.aiter_lines()
                first = await asyncio.wait_for(lines.__anext__(), 5)
                assert first.startswith("data: ") and json.loads(first[6:])["capital"] == 100000.0
                state.set_health(feed="connected")  # a change must be pushed to the open page
                nxt = ""
                while not nxt.startswith("data: "):
                    nxt = await asyncio.wait_for(lines.__anext__(), 5)
                assert json.loads(nxt[6:])["health"]["feed"] == "connected"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)


async def test_dashboard_publish_reaches_subscribers() -> None:
    state = DashboardState()
    q = state.subscribe()
    state.set_health(feed="connected")
    snap = await asyncio.wait_for(q.get(), 1)
    assert snap["health"]["feed"] == "connected"
    state.unsubscribe(q)


# ------------------------------------------------ cards with charts on replay


@respx.mock
async def test_replay_delivers_card_with_chart(tmp_path: Path, chart_png: Path) -> None:
    api = "https://api.telegram.org/botTOKEN"
    photo = respx.post(f"{api}/sendPhoto").mock(return_value=httpx.Response(200, json={"ok": True}))
    path = tmp_path / "session.jsonl"
    with SessionRecorder(path) as rec:  # a tick a minute from 09:15 to 10:00, climbing
        for k in range(46):
            px = 99.0 if k < 16 else 100.2 + 0.02 * (k - 16)
            rec.raw_tick("NSE_1", at(9, 15) + timedelta(minutes=k, seconds=5), px)
    wl = watchlist(entry(Grade.A, chart=str(chart_png)))
    cfg = AlertsConfig.model_validate({"desktop": {"enabled": True}, "telegram": {"enabled": True}})
    toasts: list[str] = []
    async with httpx.AsyncClient() as http:
        router = AlertRouter(
            cfg, watchlist=wl,
            desktop=DesktopNotifier(toast=lambda t, b: toasts.append(t), beep=lambda u: None),
            telegram=TelegramBot(token="TOKEN", chat_id=42, http=http), dashboard=DashboardState(),
        )  # fmt: skip
        mon = TriggerMonitor(
            SessionRules(), signals=[TrackedSignal(signal=sig())], on_alert=router.route
        )
        res = replay(read_session(path), mon)
        sent = await router.flush()
    assert [a.kind for a in res.alerts if a.kind is AlertKind.TRIGGERED]
    assert any(t.startswith("TRIGGERED") for t in toasts)
    assert sent == 1 and photo.call_count == 1  # the card went out with its chart
    assert router.dashboard is not None and router.dashboard.alerts[0]["kind"] == "triggered"
