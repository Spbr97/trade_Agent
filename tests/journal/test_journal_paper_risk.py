"""M9: journal round-trips, paper book vs hand-calculated trades (incl. a gap-down exit),
risk manager limits firing in a simulated losing week, auto-bench track records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.data.synth import sessions
from tests.engine.charts import frame
from tradedesk.backtest.fills import Bar, Fill, FillReason, Position
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.config.models import RiskConfig
from tradedesk.engine.indicators import daily_features
from tradedesk.engine.lifecycle import SignalState, TrackedSignal
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.journal import Journal
from tradedesk.journal.stats import live_vs_paper, summary, track_record, track_records
from tradedesk.live.models import Alert, AlertKind, AlertLevel
from tradedesk.markets import EquityCostModel
from tradedesk.models import Side, TradeType
from tradedesk.paper import PaperBook
from tradedesk.risk.costs import leg_cost
from tradedesk.risk.limits import RiskManager

RISK = RiskConfig(trading_capital=Decimal("1000000"))
CAL = sessions(date(2026, 3, 2), 40)


def sig(
    code: str = "NSE_1", trigger: float = 100.0, stop: float = 95.0, day: date = CAL[0]
) -> Signal:
    return Signal(
        id=f"base_breakout:{code}:{day.isoformat()}", scrip_code=code,
        symbol=code.replace("NSE_", "S"),
        setup=SetupKind.BASE_BREAKOUT, armed_on=day, trigger=trigger, stop=stop,
        t1=trigger + 2 * (trigger - stop), t2=trigger + 3 * (trigger - stop), atr=2.0,
    )  # fmt: skip


def bar(on: date, o: float, h: float, lo: float, c: float, ema10: float = 90.0) -> Bar:
    return Bar(on=on, open=o, high=h, low=lo, close=c, volume=1, ema10=ema10, atr=2.0)


# ----------------------------------------------------------------- journal


def test_journal_signal_alert_position_roundtrip() -> None:
    with Journal() as j:
        ts = TrackedSignal(signal=sig())
        ts.move(SignalState.TRIGGERED, CAL[1], "15m close 100.90 > 100.00")
        j.upsert_signal(ts, grade="A", score=85)
        again = j.load_signal(ts.signal.id)
        assert again is not None and again.state is SignalState.TRIGGERED
        assert again.history[0].note == "15m close 100.90 > 100.00"
        j.upsert_signal(ts)  # idempotent: no duplicate transitions
        assert len(j.load_signal(ts.signal.id).history) == 1  # type: ignore[union-attr]
        trig = j.triggered_between(CAL[0], CAL[5])
        assert [(t.signal.id, d, fill) for t, d, fill in trig] == [(ts.signal.id, CAL[1], 100.9)]

        j.record_alert(
            Alert(
                kind=AlertKind.TRIGGERED,
                level=AlertLevel.URGENT,
                at=__import__("datetime").datetime(2026, 3, 3, 9, 45),
                scrip_code="NSE_1",
                symbol="S1",
                message="x",
                payload={"fill": 100.9},
            )
        )
        assert len(j.alerts_on(CAL[1])) == 1

        pos = Position(
            signal=ts.signal, entry_date=CAL[1], entry_price=100.9, qty_initial=50, qty_open=50,
            stop=95.0, highest_close=100.9,
            fills=[Fill(on=CAL[1], price=100.9, qty=50, reason=FillReason.ENTRY)],
        )  # fmt: skip
        j.save_position(pos, source="live")
        back = j.open_positions(source="live")
        assert len(back) == 1 and back[0].qty_open == 50 and back[0].signal.id == ts.signal.id
        j.record_stop_update(ts.signal.id, CAL[2], 95.0, 100.9, "breakeven")
        j.tag(ts.signal.id, "rule_break", "entered before 09:30")
        assert j.tags_for([ts.signal.id])[0]["tag"] == "rule_break"
        j.record_order_update(
            {
                "order_id": "EQ-1",
                "order_status": "SUCCESS",
                "filled_quantity": 50,
                "average_price": 100.9,
            }
        )
        assert j.order_updates()[0]["order_id"] == "EQ-1"


# -------------------------------------------------------------- paper book


def _feats(closes: list[float], **kw: object) -> pd.DataFrame:  # type: ignore[name-defined]  # noqa: F821
    return daily_features(frame(closes, **kw))  # type: ignore[arg-type]


def test_paper_book_gap_down_exit_matches_hand_calculation() -> None:
    with Journal() as j:
        book = PaperBook(j, RISK, capital=1_000_000.0, slippage_pct=0.0)
        ts = TrackedSignal(signal=sig())
        ts.move(SignalState.TRIGGERED, CAL[1], "15m close 100.00 > 100.00")
        feats = _feats([100.0] * 60)  # flat history; entry day bar stays above the stop
        pos = book.open_from_trigger(ts, CAL[1], 100.0, feats)
        assert pos is not None
        # Sizing: 0.25% of 10,00,000 = 2,500 risk / 5 per share = 500 shares.
        assert pos.qty_initial == 500 and pos.qty_open == 500
        assert j.open_positions(source="paper")[0].qty_open == 500

        # Next session opens at 90, below the 95 stop: gap rule -> out at the open.
        closed = book.mark(CAL[2], {"NSE_1": bar(CAL[2], 90.0, 92.0, 89.0, 91.0)})
        assert len(closed) == 1
        t = closed[0]
        assert t.exit_reason == "gap_stop"
        gross = (90.0 - 100.0) * 500
        buy = leg_cost(
            RISK.costs, side=Side.BUY, trade_type=TradeType.DELIVERY, qty=500, price=Decimal("100")
        )
        sell = leg_cost(
            RISK.costs, side=Side.SELL, trade_type=TradeType.DELIVERY, qty=500, price=Decimal("90")
        )
        costs = float(buy.total + sell.total)
        assert t.costs == pytest.approx(costs)
        assert t.net_pnl == pytest.approx(gross - costs)
        assert t.r_multiple == pytest.approx((gross - costs) / 2500.0)
        assert t.r_multiple < -1.0 and t.gap_damage == pytest.approx(-(gross - costs) - 2500.0)
        rows = j.trades(source="paper")
        assert len(rows) == 1 and rows[0]["exit_reason"] == "gap_stop"
        assert book.equity == pytest.approx(1_000_000.0 + t.net_pnl)
        assert book.open_from_trigger(ts, CAL[1], 100.0, feats) is None  # never twice


def test_paper_book_partial_breakeven_and_trail_by_hand() -> None:
    with Journal() as j:
        book = PaperBook(j, RISK, capital=1_000_000.0, slippage_pct=0.0)
        ts = TrackedSignal(signal=sig())
        ts.move(SignalState.TRIGGERED, CAL[1], "15m close 100.00 > 100.00")
        pos = book.open_from_trigger(ts, CAL[1], 100.0, _feats([100.0] * 60))
        assert pos is not None and pos.qty_initial == 500
        # Day 2: high 111 >= T1 110 -> sell 250 at 110, stop to breakeven.
        assert book.mark(CAL[2], {"NSE_1": bar(CAL[2], 101, 111, 100.5, 109, ema10=100)}) == []
        assert pos.qty_open == 250 and pos.partial_done and pos.stop == 100.0
        stops = j.con.execute("SELECT * FROM stop_updates").fetchall()
        assert stops and stops[0]["new_stop"] == 100.0
        # Day 3: closes below EMA10 -> trail exit of the remaining 250 at the close.
        closed = book.mark(CAL[3], {"NSE_1": bar(CAL[3], 108, 109, 104, 104.5, ema10=105)})
        assert len(closed) == 1 and closed[0].exit_reason == "trail"
        gross = 250 * (110 - 100) + 250 * (104.5 - 100)
        assert closed[0].position.gross_pnl() == pytest.approx(gross)
        buy = leg_cost(
            RISK.costs, side=Side.BUY, trade_type=TradeType.DELIVERY, qty=500, price=Decimal("100")
        )
        s1 = leg_cost(
            RISK.costs, side=Side.SELL, trade_type=TradeType.DELIVERY, qty=250, price=Decimal("110")
        )
        s2 = leg_cost(
            RISK.costs,
            side=Side.SELL,
            trade_type=TradeType.DELIVERY,
            qty=250,
            price=Decimal("104.5"),
        )
        assert closed[0].costs == pytest.approx(float(buy.total + s1.total + s2.total))
        assert closed[0].net_pnl == pytest.approx(gross - closed[0].costs)
        assert j.open_positions(source="paper") == []


def test_paper_book_survives_restart() -> None:
    with Journal() as j:
        book = PaperBook(j, RISK, capital=1_000_000.0, slippage_pct=0.0)
        ts = TrackedSignal(signal=sig())
        ts.move(SignalState.TRIGGERED, CAL[1])
        book.open_from_trigger(ts, CAL[1], 100.0, _feats([100.0] * 60))
        book2 = PaperBook(j, RISK, capital=1_000_000.0, slippage_pct=0.0)  # fresh process
        assert set(book2.positions) == {ts.signal.id}
        closed = book2.mark(CAL[2], {"NSE_1": bar(CAL[2], 97, 98, 93, 94)})
        assert closed and closed[0].exit_reason == "stop"


# ------------------------------------------------------------ risk manager


def _record_live_trade(
    j: Journal, code: str, entry: date, exit_: date, pnl: float, reason: str = "stop"
) -> None:
    pf = Portfolio(risk=RISK, costs=EquityCostModel(RISK.costs), equity=1_000_000.0)
    pf.start_session(exit_, 0)
    s = sig(code, 100.0, 95.0, entry)
    pos = Position(
        signal=s, entry_date=entry, entry_price=100.0, qty_initial=100, qty_open=100, stop=95.0,
        highest_close=100.0, sessions_held=1,
        fills=[Fill(on=entry, price=100.0, qty=100, reason=FillReason.ENTRY)],
    )  # fmt: skip
    pos.fills.append(Fill(on=exit_, price=100.0 + pnl / 100, qty=100, reason=FillReason(reason)))
    pos.qty_open = 0
    j.record_trade(pf.settle(pos), source="live")


def test_limits_fire_in_a_simulated_losing_week() -> None:
    with Journal() as j:
        # Monday..Thursday of week 1: four stop-outs of Rs 8,000 each (0.8% of 10 lakh)
        week1 = [d for d in CAL if d.isocalendar()[1] == CAL[0].isocalendar()[1]][:4]
        for i, d in enumerate(week1):
            _record_live_trade(j, f"NSE_{i}", d, d, pnl=-8_000.0)
        friday = [d for d in CAL if d.isocalendar()[1] == CAL[0].isocalendar()[1]][4]
        # Friday: a breakeven stop-out (tiny profit) - it resets the loss streak but still
        # starts a re-entry cooldown for that name.
        _record_live_trade(j, "NSE_5", friday, friday, pnl=+100.0)
        rm = RiskManager(RISK, j, capital=1_000_000.0, calendar=CAL)
        rm.start_session(friday)
        st = rm.status(friday)
        assert st.weekly_locked and st.weekly_pnl_pct < -0.03  # 4 x ~0.8% > 3%
        ok, why = rm.can_enter(sig("NSE_9", day=friday), size_multiplier=1.0)
        assert not ok and why == "weekly loss limit"
        # Next Monday: the weekly lock resets, but the four consecutive losses pause us for
        # two sessions (Fri, Mon).
        next_week = [d for d in CAL if d.isocalendar()[1] == CAL[0].isocalendar()[1] + 1]
        rm.start_session(next_week[0])
        st = rm.status(next_week[0])
        assert not st.weekly_locked and st.paused and "NSE_5" in st.cooldowns
        ok, why = rm.can_enter(sig("NSE_9", day=next_week[0]), size_multiplier=1.0)
        assert not ok and why == "consecutive-loss pause"
        # Tuesday: pause over, but NSE_5 (stopped out Friday) is still in its 3-session cooldown.
        rm.start_session(next_week[1])
        ok, why = rm.can_enter(sig("NSE_9", day=next_week[1]), size_multiplier=1.0)
        assert ok, why
        ok, why = rm.can_enter(sig("NSE_5", day=next_week[1]), size_multiplier=1.0)
        assert not ok and why == "re-entry cooldown"
        assert not rm.can_enter(sig("NSE_9"), size_multiplier=0.0)[0]  # regime risk_off
        ok, why = rm.can_enter(sig("NSE_9"), size_multiplier=1.0, results_in_sessions=6)
        assert not ok and "results" in why


def test_risk_manager_rebuild_is_deterministic_and_counts_open_positions() -> None:
    with Journal() as j:
        _record_live_trade(j, "NSE_1", CAL[0], CAL[1], pnl=+3_000.0, reason="partial")
        pos = Position(
            signal=sig("NSE_2", day=CAL[2]), entry_date=CAL[2], entry_price=100.0, qty_initial=100,
            qty_open=100, stop=95.0, highest_close=100.0,
            fills=[Fill(on=CAL[2], price=100.0, qty=100, reason=FillReason.ENTRY)],
        )  # fmt: skip
        j.save_position(pos, source="live")
        a = RiskManager(RISK, j, capital=1_000_000.0, calendar=CAL)
        b = RiskManager(RISK, j, capital=1_000_000.0, calendar=CAL)
        assert a.portfolio.equity == b.portfolio.equity > 1_000_000.0
        assert set(a.portfolio.open) == {"NSE_2"} and a.status(CAL[3]).heat_pct == pytest.approx(
            500 / a.portfolio.equity
        )
        assert a.can_enter(sig("NSE_2"), size_multiplier=1.0) == (False, "already holding")


# ---------------------------------------------------------- stats / bench


def _paper_trade(j: Journal, i: int, r: float, setup: str = "base_breakout") -> None:
    s = Signal(
        id=f"{setup}:NSE_{i}:{CAL[0].isoformat()}", scrip_code=f"NSE_{i}", symbol=f"S{i}",
        setup=SetupKind(setup), armed_on=CAL[0], trigger=100.0, stop=95.0, t1=110.0, t2=115.0,
        atr=2.0,
    )  # fmt: skip
    pf = Portfolio(risk=RISK, costs=EquityCostModel(RISK.costs), equity=1_000_000.0)
    pos = Position(
        signal=s, entry_date=CAL[1], entry_price=100.0, qty_initial=100, qty_open=0, stop=95.0,
        highest_close=100.0, sessions_held=2,
        fills=[Fill(on=CAL[1], price=100.0, qty=100, reason=FillReason.ENTRY),
               Fill(on=CAL[3], price=100.0 + 5 * r, qty=100, reason=FillReason.TRAIL)],
    )  # fmt: skip
    j.record_trade(pf.settle(pos), source="paper")


def test_track_record_and_auto_bench() -> None:
    with Journal() as j:
        for i in range(10):
            _paper_trade(j, i, r=+1.0)
        tr = track_record(j, "base_breakout")
        assert tr.trades == 10 and tr.expectancy_r > 0.5 and not tr.benched  # not a full window yet
        for i in range(10, 40):
            _paper_trade(j, i, r=-1.0)
        tr = track_record(j, "base_breakout")
        assert tr.trades == 30 and tr.expectancy_r < 0 and tr.benched  # rolling 30 all losers
        assert track_records(j, ["base_breakout", "nr7_breakout"])["nr7_breakout"].trades == 0
        s = summary(j)
        assert s["by_setup"]["base_breakout"]["benched"] is True and s["paper"]["trades"] == 40
        assert live_vs_paper(j).live_trades == 0
