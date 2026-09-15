"""wait_for_eligible_signals: same-day pickup of a setup that earns alert-rights mid-session
(2026-09-15 request). Only re-checks eligibility against current config/data - never lowers
the bar, invents a signal, or bypasses config/setups.yaml's human-approval gate."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from tradedesk.broker.indstocks.models import IST
from tradedesk.engine.regime import Regime, RegimeSnapshot
from tradedesk.engine.scoring import Grade
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.live.session import wait_for_eligible_signals
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry

D = date(2026, 3, 2)


def at(h: int, m: int, s: int = 0) -> datetime:
    return datetime.combine(D, time(h, m, s), tzinfo=IST)


def _sig(code: str = "NSE_1") -> Signal:
    return Signal(
        id=f"base_breakout:{code}:2026-03-01", scrip_code=code, symbol="ONE",
        setup=SetupKind.BASE_BREAKOUT, armed_on=D - timedelta(days=1), trigger=100.0,
        stop=95.0, t1=110.0, t2=115.0, atr=2.0,
    )  # fmt: skip


def _entry(alertable: bool, grade: Grade = Grade.A) -> WatchlistEntry:
    return WatchlistEntry(
        signal=_sig(), score=85, grade=grade, score_components={}, score_notes=[],
        alertable=alertable, qty=50, risk_amount=250.0, risk_pct=0.0025, position_value=5000.0,
        size_caps=[], costs_round_trip=60.0, net_rr_t1=1.6, net_rr_t2=2.4,
        results_in_sessions=None, heat_before_pct=0.0, heat_after_pct=0.0025, atr_pct=2.0,
        avg_turnover=1e8,
    )  # fmt: skip


def _regime() -> RegimeSnapshot:
    return RegimeSnapshot(
        on=D, regime=Regime.RISK_ON, benchmark_close=1.0, benchmark_ema=1.0, ema_rising=True,
        above_ema=True, breadth_pct=60.0, vix=12.0, vix_change_5d_pct=0.0, size_multiplier=1.0,
        reasons=["fine"],
    )  # fmt: skip


def _watchlist(*, alertable: bool) -> Watchlist:
    return Watchlist(
        on=D, generated_at=at(9, 10), regime=_regime(), capital=100_000.0,
        entries=[_entry(alertable)], open_positions=[],
    )  # fmt: skip


class FakeClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


async def test_returns_immediately_when_already_eligible() -> None:
    calls = []

    def rebuild() -> Watchlist:
        calls.append(1)
        return _watchlist(alertable=True)

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    wl = await wait_for_eligible_signals(
        rebuild, until=time(15, 35), poll_seconds=300, now=lambda: at(9, 10), sleep=fake_sleep
    )
    assert wl is not None
    assert len(calls) == 1
    assert slept == []


async def test_polls_until_something_becomes_eligible() -> None:
    responses = [False, False, True]
    calls = {"n": 0}
    clock = FakeClock(at(9, 10))

    def rebuild() -> Watchlist:
        wl = _watchlist(alertable=responses[calls["n"]])
        calls["n"] += 1
        return wl

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    wl = await wait_for_eligible_signals(
        rebuild, until=time(15, 35), poll_seconds=300, now=clock.now, sleep=fake_sleep
    )
    assert wl is not None
    assert calls["n"] == 3
    assert slept == [300, 300]


async def test_returns_none_once_until_is_reached() -> None:
    clock = FakeClock(at(15, 30))  # 5 minutes of runway left before 15:35

    def rebuild() -> Watchlist:
        return _watchlist(alertable=False)

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    wl = await wait_for_eligible_signals(
        rebuild, until=time(15, 35), poll_seconds=300, now=clock.now, sleep=fake_sleep
    )
    assert wl is None
    # first poll at 15:30 finds nothing, sleeps only the 300s remaining (capped, not the full
    # poll interval past `until`), second poll at 15:35 correctly stops without sleeping again
    assert slept == [300]


async def test_sleep_is_capped_to_remaining_time_not_the_full_poll_interval() -> None:
    clock = FakeClock(at(15, 33))  # only 120s left before 15:35

    def rebuild() -> Watchlist:
        return _watchlist(alertable=False)

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    await wait_for_eligible_signals(
        rebuild, until=time(15, 35), poll_seconds=300, now=clock.now, sleep=fake_sleep
    )
    assert slept == [120]


async def test_on_poll_callback_sees_every_rebuilt_watchlist_including_empty_ones() -> None:
    responses = [False, True]
    calls = {"n": 0}
    seen: list[bool] = []

    def rebuild() -> Watchlist:
        wl = _watchlist(alertable=responses[calls["n"]])
        calls["n"] += 1
        return wl

    async def fake_sleep(s: float) -> None:
        pass

    await wait_for_eligible_signals(
        rebuild, until=time(15, 35), poll_seconds=60, now=lambda: at(9, 10), sleep=fake_sleep,
        on_poll=lambda wl: seen.append(bool(wl.active[0].alertable)),
    )
    assert seen == [False, True]
