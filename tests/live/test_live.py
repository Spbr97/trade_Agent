"""M7: bars from ticks, 15-minute confirmation, gap check, position watch, monitor,
recorder/player, and replay-vs-backtester parity."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.broker.indstocks.models import IST
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.live.bars import BarBuilder, slot_start
from tradedesk.live.confirmation import Decision, confirm_trigger
from tradedesk.live.models import AlertKind, IntradayBar, SessionRules
from tradedesk.live.trigger_monitor import TriggerMonitor
from tradedesk.replay import SessionRecorder, read_session, replay

D = date(2026, 3, 2)
RULES = SessionRules()


def at(h: int, m: int, s: int = 0, d: date = D) -> datetime:
    return datetime.combine(d, time(h, m, s), tzinfo=IST)


def sig(
    code: str = "NSE_1", trigger: float = 100.0, stop: float = 95.0, atr: float = 2.0
) -> Signal:
    return Signal(
        id=f"base_breakout:{code}:{(D - timedelta(days=1)).isoformat()}", scrip_code=code,
        symbol=code.replace("NSE_", "S"), setup=SetupKind.BASE_BREAKOUT,
        armed_on=D - timedelta(days=1), trigger=trigger, stop=stop,
        t1=trigger + 2 * (trigger - stop), t2=trigger + 3 * (trigger - stop), atr=atr,
    )  # fmt: skip


def bar(
    code: str, h: int, m: int, o: float, hi: float, lo: float, c: float, vol: int = 0
) -> IntradayBar:
    start = at(h, m)
    return IntradayBar(
        scrip_code=code,
        start=start,
        end=start + timedelta(minutes=15),
        open=o,
        high=hi,
        low=lo,
        close=c,
        volume=vol,
    )


# ------------------------------------------------------------------ bars


def test_slot_start_anchors_to_0915() -> None:
    assert slot_start(at(9, 15)) == at(9, 15)
    assert slot_start(at(9, 29, 59)) == at(9, 15)
    assert slot_start(at(9, 30)) == at(9, 30)
    assert slot_start(at(15, 29)) == at(15, 15)


def test_bar_builder_closes_on_next_slot_and_flush() -> None:
    b = BarBuilder()
    assert b.on_tick("NSE_1", at(9, 16), 100.0, day_volume=1000) is None
    assert b.on_tick("NSE_1", at(9, 20), 103.0, day_volume=1500) is None
    assert b.on_tick("NSE_1", at(9, 25), 99.0, day_volume=1800) is None
    closed = b.on_tick("NSE_1", at(9, 31), 101.0, day_volume=2000)
    assert closed is not None
    assert (closed.open, closed.high, closed.low, closed.close) == (100.0, 103.0, 99.0, 99.0)
    assert closed.volume == 800 and closed.ticks == 3 and closed.end == at(9, 30)
    assert (
        b.day_open["NSE_1"] == 100.0 and b.day_high["NSE_1"] == 103.0 and b.day_low["NSE_1"] == 99.0
    )
    assert b.flush(at(9, 44)) == []  # current bar not over yet
    flushed = b.flush(at(9, 45))
    assert len(flushed) == 1 and flushed[0].close == 101.0


# --------------------------------------------------------------- confirmation


def test_confirmation_rules() -> None:
    s = sig()
    assert (
        confirm_trigger(s, bar("NSE_1", 9, 30, 99, 101, 98, 100.5), RULES).decision
        is Decision.TRIGGERED
    )
    assert (
        confirm_trigger(s, bar("NSE_1", 9, 30, 99, 101, 98, 99.8), RULES).decision is Decision.NONE
    )  # touch only
    assert (
        confirm_trigger(s, bar("NSE_1", 9, 15, 99, 101, 98, 100.5), RULES).decision
        is Decision.WINDOW
    )
    assert (
        confirm_trigger(s, bar("NSE_1", 15, 0, 99, 101, 98, 100.5), RULES).decision is Decision.LATE
    )
    assert (
        confirm_trigger(s, bar("NSE_1", 10, 0, 96, 97, 93, 94.0), RULES).decision
        is Decision.INVALIDATED
    )
    norms = {time(9, 30): 10_000.0}
    thin = confirm_trigger(
        s, bar("NSE_1", 9, 30, 99, 101, 98, 100.5, vol=4_000), RULES, slot_volume_norm=norms
    )
    assert thin.decision is Decision.THIN and thin.volume_ratio == pytest.approx(0.4)
    ok = confirm_trigger(
        s, bar("NSE_1", 9, 30, 99, 101, 98, 100.5, vol=15_000), RULES, slot_volume_norm=norms
    )
    assert ok.decision is Decision.TRIGGERED and ok.fill_price == 100.5 and ok.volume_ratio == 1.5


# ------------------------------------------------------------------ monitor


def position(
    code: str = "NSE_9", entry: float = 200.0, stop: float = 190.0, qty: int = 10
) -> Position:
    s = sig(code, trigger=entry, stop=stop, atr=4.0)
    return Position(
        signal=s, entry_date=D - timedelta(days=2), entry_price=entry, qty_initial=qty,
        qty_open=qty, stop=stop, highest_close=entry, sessions_held=1,
        fills=[Fill(on=D - timedelta(days=2), price=entry, qty=qty, reason=FillReason.ENTRY)],
    )  # fmt: skip


def test_monitor_gap_check_trigger_position_watch_and_dedupe() -> None:
    a = TrackedSignal(signal=sig("NSE_1"))  # will trigger on the 09:30 bar
    b = TrackedSignal(signal=sig("NSE_2", trigger=50.0, stop=47.0, atr=1.0))  # chased at the open
    pos = position()
    mon = TriggerMonitor(RULES, signals=[a, b], positions=[pos], atr_by_code={"NSE_9": 4.0})

    alerts = mon.on_tick("NSE_2", at(9, 15, 5), 52.0)  # open > 50 + 1 ATR
    assert [x.kind for x in alerts] == [AlertKind.CHASED] and b.state is SignalState.CHASED

    mon.on_tick("NSE_1", at(9, 15, 5), 99.0)
    mon.on_tick("NSE_1", at(9, 20), 100.6)  # above the trigger inside the no-entry window
    assert mon.on_tick("NSE_1", at(9, 30, 1), 100.2) == []  # closes 09:15 bar: WINDOW, no alert
    mon.on_tick("NSE_1", at(9, 40), 100.9)
    alerts = mon.on_tick("NSE_1", at(9, 45, 2), 100.7)  # closes the 09:30 bar at 100.9 > 100
    assert [x.kind for x in alerts] == [AlertKind.TRIGGERED]
    assert a.state is SignalState.TRIGGERED and mon.confirmations[a.signal.id].fill_price == 100.9

    alerts = mon.on_tick("NSE_9", at(9, 46), 188.0)  # opened below the stop
    kinds = [x.kind for x in alerts]
    assert AlertKind.GAP_BELOW_STOP in kinds and AlertKind.STOP_HIT in kinds
    assert mon.on_tick("NSE_9", at(9, 47), 187.0) == []  # de-duplicated
    alerts = mon.on_tick("NSE_9", at(9, 50), 221.0)  # T1 = 220
    assert [x.kind for x in alerts] == [AlertKind.T1_REACHED]
    assert mon.on_tick("NSE_9", at(9, 51), 222.0) == []


def test_monitor_staleness_pauses_and_resumes() -> None:
    a = TrackedSignal(signal=sig("NSE_1"))
    mon = TriggerMonitor(RULES, signals=[a])
    mon.on_tick("NSE_1", at(10, 0), 99.0)
    assert mon.check_staleness(at(10, 1)) == []
    stale = mon.check_staleness(at(10, 3))
    assert [x.kind for x in stale] == [AlertKind.DATA_STALE] and mon.paused
    assert mon.check_staleness(at(10, 4)) == []  # once
    resumed = mon.on_tick("NSE_1", at(10, 5), 100.5)
    assert [x.kind for x in resumed] == [AlertKind.DATA_OK] and not mon.paused
    # A trigger raised while paused is held back, then delivered marked as delayed.
    mon.on_tick("NSE_1", at(10, 10), 100.5)
    mon.check_staleness(at(10, 13))  # stale again
    assert mon.paused
    assert mon.flush(at(10, 16)) == []  # the 10:00 bar closes at 100.5 > trigger, but silently
    assert a.state is SignalState.TRIGGERED
    resumed = mon.on_tick("NSE_1", at(10, 20), 100.7)
    assert [x.kind for x in resumed] == [AlertKind.DATA_OK, AlertKind.TRIGGERED]
    assert resumed[1].message.startswith("[delayed]")


def test_monitor_resync_after_reconnect() -> None:
    a = TrackedSignal(signal=sig("NSE_1"))
    c = TrackedSignal(signal=sig("NSE_2", trigger=50.0, stop=47.0, atr=1.0))
    pos = position()
    mon = TriggerMonitor(RULES, signals=[a, c], positions=[pos])
    quotes = {
        "NSE_1": {"day_open": 99.0, "day_high": 101.0, "day_low": 98.0, "live_price": 100.2},
        "NSE_2": {"day_open": 52.0, "day_high": 53.0, "day_low": 51.0, "live_price": 52.5},
        "NSE_9": {"day_open": 195.0, "day_high": 196.0, "day_low": 189.0, "live_price": 191.0},
    }
    alerts = mon.resync(quotes, at(11, 0))
    kinds = sorted(x.kind for x in alerts)
    assert kinds == sorted([AlertKind.RESYNC, AlertKind.CHASED, AlertKind.STOP_HIT])
    assert c.state is SignalState.CHASED and a.state is SignalState.ARMED


def test_close_check_alerts() -> None:
    pos = position()
    pos.partial_done = True
    pos.signal = pos.signal.model_copy(update={"exit_plan": ExitPlan(trail="ema10_close")})
    mon = TriggerMonitor(RULES, signals=[], positions=[pos], ema10_by_code={"NSE_9": 205.0})
    mon.on_tick("NSE_9", at(15, 14), 203.0)
    alerts = mon.run_close_check(at(15, 15))
    assert AlertKind.CLOSE_CHECK in [x.kind for x in alerts]


# ------------------------------------------------------------ record / replay


def test_recorder_player_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with SessionRecorder(path) as rec:
        rec.raw_tick("NSE_1", at(9, 15, 5), 99.0)
        rec.raw_tick("NSE_1", at(9, 40), 100.9)
        rec.raw_tick("NSE_1", at(9, 45, 2), 100.7)
        rec.quotes(at(10, 0), {"NSE_1": {"day_open": 99.0, "day_high": 101.0, "day_low": 98.0}})
        rec.event(at(15, 15), "close_check")
    live = TriggerMonitor(RULES, signals=[TrackedSignal(signal=sig("NSE_1"))])
    res = replay(read_session(path), live)
    assert res.ticks == 3 and res.resyncs == 1
    assert [a.kind for a in res.alerts if a.kind is AlertKind.TRIGGERED]
    assert live.signals[0].state is SignalState.TRIGGERED


def test_replay_matches_backtester_triggers() -> None:
    """M7 done-when: a replayed session produces the same triggers as the backtester."""
    from tests.backtest.synth_universe import benchmark, breakout_stock, intraday_from_daily
    from tests.backtest.test_runner import BREAKOUT, CAL, NIFTY, REF, config
    from tradedesk.backtest.runner import prepare_market, run_backtest
    from tradedesk.data.candle_store import CandleStore

    stocks = {"NSE_WIN": ("win", 1), "NSE_STOP": ("stop", 3)}
    store = CandleStore()
    store.upsert_instruments([NIFTY])
    store.upsert_candles(benchmark(REF, CAL))
    for code, (aftermath, seed) in stocks.items():
        daily = breakout_stock(code, CAL, breakout_index=BREAKOUT, aftermath=aftermath, seed=seed)
        store.upsert_candles(daily)
        store.upsert_candles(intraday_from_daily(daily))
    cfg = config(CAL[BREAKOUT - 40], CAL[-1])
    md = prepare_market(store, list(stocks), REF, cfg)
    assert md.intraday, "15-minute bars must be loaded"
    res = run_backtest(md, cfg)
    day = CAL[BREAKOUT]
    bt_triggered = {
        ts.signal.id: next(h.note for h in ts.history if h.to_state is SignalState.TRIGGERED)
        for ts in res.signals
        if any(h.to_state is SignalState.TRIGGERED and h.on == day for h in ts.history)
    }
    assert bt_triggered, "the engineered breakout must trigger in the backtest"

    # Rebuild the morning's armed book and replay the day's bars as ticks.
    morning = [
        TrackedSignal(signal=ts.signal)
        for ts in res.signals
        if ts.signal.armed_on < day and all(h.on >= day for h in ts.history)
    ]
    mon = TriggerMonitor(cfg.session_rules, signals=morning)
    for code in stocks:
        for b in md.intraday[code].get(day, []):
            for k, px in enumerate((b.open, b.high, b.low, b.close)):
                mon.on_tick(code, b.start + timedelta(minutes=3 * k + 1), px)
    mon.flush(datetime.combine(day, time(15, 45), tzinfo=IST))

    live_triggered = {ts.signal.id for ts in mon.signals if ts.state is SignalState.TRIGGERED}
    assert live_triggered == set(bt_triggered)
    for sid in live_triggered:
        fill_live = mon.confirmations[sid].fill_price
        entry = next(
            t for t in res.portfolio.closed if t.position.signal.id == sid
        ).position.entry_price
        assert fill_live == pytest.approx(entry)  # slippage is 0 in the test config
