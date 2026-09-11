from __future__ import annotations

from datetime import date

import pytest

from tradedesk.backtest.fills import (
    Bar,
    EntryOutcome,
    Fill,
    FillReason,
    Position,
    evaluate_entry,
    evaluate_exit,
)
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal

D = date(2026, 3, 2)


def sig(trail: str = "ema10_close") -> Signal:
    return Signal(
        id="s", scrip_code="NSE_1", symbol="X", setup=SetupKind.BASE_BREAKOUT, armed_on=D,
        trigger=100.0, stop=95.0, t1=110.0, t2=115.0, atr=2.0,
        exit_plan=ExitPlan(trail=trail, trail_atr_mult=2.0),  # type: ignore[arg-type]
    )  # fmt: skip


def bar(o: float, h: float, lo: float, c: float, ema10: float = 90.0, atr: float = 2.0) -> Bar:
    return Bar(on=D, open=o, high=h, low=lo, close=c, volume=1, ema10=ema10, atr=atr)


def pos(qty: int = 20, entry: float = 100.0) -> Position:
    return Position(
        signal=sig(), entry_date=D, entry_price=entry, qty_initial=qty, qty_open=qty, stop=95.0,
        highest_close=entry, fills=[Fill(on=D, price=entry, qty=qty, reason=FillReason.ENTRY)],
    )  # fmt: skip


# ------------------------------------------------------------------- entries


def test_entry_gap_fill_touch_fill_chased_invalidated() -> None:
    s = sig()
    assert evaluate_entry(s, bar(101, 104, 100.5, 103), 0.0) == (EntryOutcome.FILLED, 101.0)
    assert evaluate_entry(s, bar(98, 101, 97, 100.5), 0.0) == (EntryOutcome.FILLED, 100.0)
    assert evaluate_entry(s, bar(102.5, 105, 102, 104), 0.0) == (
        EntryOutcome.CHASED,
        None,
    )  # > L + 1 ATR
    assert evaluate_entry(s, bar(97, 99, 93, 94), 0.0) == (EntryOutcome.INVALIDATED, None)
    assert evaluate_entry(s, bar(97, 99, 96, 98), 0.0) == (EntryOutcome.NONE, None)
    out, px = evaluate_entry(s, bar(98, 101, 97, 100.5), 0.001)
    assert out is EntryOutcome.FILLED and px == pytest.approx(100.1)


# --------------------------------------------------------------------- exits


def test_gap_below_stop_exits_at_open_not_stop() -> None:
    p = pos()
    fills = evaluate_exit(p, bar(90, 92, 88, 91), 0.0)
    assert [f.reason for f in fills] == [FillReason.GAP_STOP]
    assert fills[0].price == 90.0 and p.closed
    assert p.gross_pnl() == pytest.approx(-200.0)  # 10 x 20, worse than the planned 1R (100)


def test_stop_wins_when_bar_touches_both_stop_and_target() -> None:
    p = pos()
    fills = evaluate_exit(p, bar(100, 112, 94, 105), 0.0)
    assert [f.reason for f in fills] == [FillReason.STOP] and fills[0].price == 95.0


def test_partial_then_breakeven_then_trail_exit() -> None:
    p = pos()
    fills = evaluate_exit(p, bar(101, 111, 100, 109, ema10=100), 0.0)
    assert [f.reason for f in fills] == [FillReason.PARTIAL]
    assert fills[0].qty == 10 and fills[0].price == 110.0
    assert p.qty_open == 10 and p.partial_done and p.stop == 100.0  # breakeven
    # A later dip to 99 hits the moved stop (no longer 95).
    fills = evaluate_exit(p, bar(101, 103, 99, 101, ema10=100), 0.0)
    assert [f.reason for f in fills] == [FillReason.STOP] and fills[0].price == 100.0
    assert p.closed


def test_ema10_trail_exits_on_close_below_ema() -> None:
    p = pos()
    evaluate_exit(p, bar(101, 111, 100, 109, ema10=100), 0.0)
    fills = evaluate_exit(p, bar(108, 109, 104, 104.5, ema10=105), 0.0)  # close < ema10
    assert [f.reason for f in fills] == [FillReason.TRAIL] and fills[0].price == 104.5
    assert p.closed


def test_atr_trail_ratchets_stop_after_partial() -> None:
    p = pos()
    p.signal = sig(trail="atr")
    evaluate_exit(p, bar(101, 111, 100, 109, atr=2.0), 0.0)  # partial; highest close 109
    assert p.stop == pytest.approx(105.0)  # max(breakeven 100, 109 - 2 x 2)
    evaluate_exit(p, bar(109, 114, 108, 113, atr=2.0), 0.0)
    assert p.stop == pytest.approx(109.0)
    fills = evaluate_exit(p, bar(112, 113, 108, 110, atr=2.0), 0.0)  # low 108 <= 109
    assert [f.reason for f in fills] == [FillReason.STOP] and fills[0].price == pytest.approx(109.0)


def test_time_stop_and_max_hold() -> None:
    p = pos()
    for _ in range(4):
        assert evaluate_exit(p, bar(101, 102, 99, 101), 0.0) == []
    fills = evaluate_exit(p, bar(101, 102, 99, 101), 0.0)  # session 5, +0.2R < 1R
    assert [f.reason for f in fills] == [FillReason.TIME_STOP] and p.sessions_held == 5

    p = pos()
    for _ in range(4):
        evaluate_exit(p, bar(104, 106, 103, 106), 0.0)
    assert evaluate_exit(p, bar(106, 107, 105, 106), 0.0) == []  # +1.2R at session 5: keep
    for _ in range(4):
        assert evaluate_exit(p, bar(106, 107, 105, 106), 0.0) == []
    fills = evaluate_exit(p, bar(106, 107, 105, 106), 0.0)  # session 10
    assert [f.reason for f in fills] == [FillReason.MAX_HOLD]


def test_entry_day_stop_out_and_no_gap_rule_on_entry_day() -> None:
    p = pos()
    fills = evaluate_exit(p, bar(100, 101, 94, 96), 0.0, entry_day=True)
    assert [f.reason for f in fills] == [FillReason.STOP] and p.sessions_held == 0
    p = pos()
    assert evaluate_exit(p, bar(100, 103, 99, 102), 0.0, entry_day=True) == []


def test_tiny_position_partial_takes_whole_lot() -> None:
    p = pos(qty=1)
    fills = evaluate_exit(p, bar(101, 111, 100, 109), 0.0)
    assert [f.reason for f in fills] == [FillReason.PARTIAL] and p.closed
