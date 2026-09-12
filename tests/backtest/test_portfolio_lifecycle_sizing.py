from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.config.models import RiskConfig
from tradedesk.engine.lifecycle import IllegalTransition, SignalState, TrackedSignal
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.markets import EquityCostModel
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size

RISK = RiskConfig(trading_capital=Decimal("100000"))
D0 = date(2026, 3, 2)


def sig(code: str, trigger: float = 100.0, stop: float = 95.0) -> Signal:
    return Signal(
        id=f"s:{code}", scrip_code=code, symbol=code, setup=SetupKind.BASE_BREAKOUT, armed_on=D0,
        trigger=trigger, stop=stop, t1=trigger + 2 * (trigger - stop),
        t2=trigger + 3 * (trigger - stop), atr=2.0,
    )  # fmt: skip


def open_pos(
    pf: Portfolio, code: str, qty: int = 20, entry: float = 100.0, stop: float = 95.0
) -> Position:
    p = Position(
        signal=sig(code, entry, stop), entry_date=pf.today or D0, entry_price=entry,
        qty_initial=qty, qty_open=qty, stop=stop, highest_close=entry,
        fills=[Fill(on=pf.today or D0, price=entry, qty=qty, reason=FillReason.ENTRY)],
    )  # fmt: skip
    pf.open_position(p)
    return p


def close_pos(
    pf: Portfolio, p: Position, price: float, reason: FillReason, on: date | None = None
) -> None:
    f = Fill(on=on or pf.today or D0, price=price, qty=p.qty_open, reason=reason)
    p.fills.append(f)
    p.qty_open = 0
    pf.record_fills(p, [f])


def portfolio(**kw: object) -> Portfolio:
    pf = Portfolio(risk=RISK, costs=EquityCostModel(RISK.costs), equity=100_000.0, **kw)  # type: ignore[arg-type]
    pf.start_session(D0, 0)
    return pf


# ---------------------------------------------------------------- limits


def test_max_positions_and_entries_per_day() -> None:
    pf = portfolio()
    for i in range(3):
        assert pf.can_enter(sig(f"NSE_{i}"), 1.0) == (True, "")
        open_pos(pf, f"NSE_{i}", qty=10)
    assert pf.can_enter(sig("NSE_9"), 1.0) == (False, "max new entries today")
    pf.start_session(D0 + timedelta(days=1), 1)
    open_pos(pf, "NSE_3", qty=10)
    open_pos(pf, "NSE_4", qty=10)
    assert pf.can_enter(sig("NSE_9"), 1.0) == (False, "max open positions")
    assert pf.can_enter(sig("NSE_0"), 1.0) == (False, "already holding")


def test_regime_and_heat_and_sector_cap() -> None:
    pf = portfolio(sector_of={"NSE_1": "BANK", "NSE_2": "BANK", "NSE_3": "BANK"})
    assert pf.can_enter(sig("NSE_1"), 0.0) == (False, "regime risk_off")
    open_pos(pf, "NSE_1", qty=20)  # risk 100 = 0.1%
    open_pos(pf, "NSE_2", qty=20)
    assert pf.can_enter(sig("NSE_3"), 1.0) == (False, "sector cap (BANK)")
    assert pf.heat_pct() == pytest.approx(0.002)
    pf.start_session(D0 + timedelta(days=1), 1)
    open_pos(pf, "NSE_4", qty=500)  # 2500 risk = 2.5% -> heat cap reached
    assert pf.can_enter(sig("NSE_5"), 1.0) == (False, "portfolio heat")


def test_weekly_loss_limit_resets_next_week() -> None:
    pf = portfolio()
    p = open_pos(pf, "NSE_1", qty=100, entry=100.0, stop=60.0)
    close_pos(pf, p, 65.0, FillReason.STOP)  # -3,500 > 3% of 1,00,000
    assert pf.weekly_loss_hit()
    assert pf.can_enter(sig("NSE_2"), 1.0) == (False, "weekly loss limit")
    pf.start_session(D0 + timedelta(days=7), 5)  # next ISO week
    assert not pf.weekly_loss_hit()


def test_consecutive_loss_pause_and_reentry_cooldown() -> None:
    pf = portfolio()
    for i in range(4):
        pf.start_session(D0 + timedelta(days=i), i)
        p = open_pos(pf, f"NSE_{i}", qty=10)
        close_pos(pf, p, 95.0, FillReason.STOP)
    # 4 losses in a row -> no entries for the next 2 sessions
    pf.start_session(D0 + timedelta(days=4), 4)
    assert pf.can_enter(sig("NSE_9"), 1.0) == (False, "consecutive-loss pause")
    pf.start_session(D0 + timedelta(days=5), 5)
    assert pf.can_enter(sig("NSE_9"), 1.0) == (False, "consecutive-loss pause")
    pf.start_session(D0 + timedelta(days=6), 6)
    assert pf.can_enter(sig("NSE_9"), 1.0) == (True, "")
    # Stopped out of NSE_3 at session 3: no re-entry for 3 sessions (6 - 3 = 3: over)
    assert pf.can_enter(sig("NSE_3"), 1.0) == (True, "")
    p = open_pos(pf, "NSE_8", qty=10)
    close_pos(pf, p, 95.0, FillReason.STOP)  # stop-out at session 6 (1 loss: no pause)
    pf.start_session(D0 + timedelta(days=8), 8)
    assert pf.can_enter(sig("NSE_8"), 1.0) == (False, "re-entry cooldown")
    pf.start_session(D0 + timedelta(days=9), 9)
    assert pf.can_enter(sig("NSE_8"), 1.0) == (True, "")


# ------------------------------------------------------------- settlement


def test_settlement_costs_r_multiple_and_gap_damage() -> None:
    pf = portfolio()
    p = open_pos(pf, "NSE_1", qty=20, entry=100.0, stop=95.0)  # planned risk 100
    close_pos(pf, p, 90.0, FillReason.GAP_STOP, on=D0 + timedelta(days=2))
    t = pf.closed[-1]
    assert t.exit_reason == "gap_stop"
    assert t.costs > 0 and t.net_pnl == pytest.approx(-200.0 - t.costs)
    assert t.r_multiple == pytest.approx(t.net_pnl / 100.0)
    assert t.gap_damage == pytest.approx(100.0 + t.costs)
    assert pf.equity == pytest.approx(100_000.0 + t.net_pnl)


def test_same_day_round_trip_is_charged_as_intraday() -> None:
    pf = portfolio()
    p1 = open_pos(pf, "NSE_1", qty=100, entry=100.0)
    close_pos(pf, p1, 101.0, FillReason.STOP, on=D0)  # same day
    pf.start_session(D0 + timedelta(days=1), 1)
    p2 = open_pos(pf, "NSE_2", qty=100, entry=100.0)
    close_pos(pf, p2, 101.0, FillReason.STOP, on=D0 + timedelta(days=3))
    intraday, delivery = pf.closed[0].costs, pf.closed[1].costs
    assert intraday < delivery  # no delivery STT on the buy, no DP charge


# -------------------------------------------------------------- lifecycle


def test_lifecycle_transitions() -> None:
    ts = TrackedSignal(signal=sig("NSE_1"))
    ts.move(SignalState.TRIGGERED, D0)
    ts.move(SignalState.TAKEN, D0)
    ts.move(SignalState.OPEN, D0)
    ts.move(SignalState.CLOSED, D0, "stop")
    assert ts.terminal and [h.to_state for h in ts.history][-1] is SignalState.CLOSED
    with pytest.raises(IllegalTransition):
        ts.move(SignalState.ARMED, D0)
    dead = TrackedSignal(signal=sig("NSE_2"))
    dead.move(SignalState.EXPIRED, D0)
    with pytest.raises(IllegalTransition):
        dead.move(SignalState.TRIGGERED, D0)
    for _ in range(3):
        dead.tick_session()
    assert not dead.expired_by_time
    dead.tick_session()
    assert dead.expired_by_time


# ----------------------------------------------------------------- sizing


def test_position_size_caps() -> None:
    base = SizeInputs(
        equity=100_000, entry=100, stop=95, max_risk_pct=0.0025, max_position_value_pct=0.25
    )
    r = position_size(base)
    assert r.qty == 50 and r.risk_amount == 250 and r.caps == []
    half = position_size(SizeInputs(**{**base.__dict__, "size_multiplier": 0.5}))
    assert half.qty == 25 and "regime x0.5" in half.caps
    tight = position_size(SizeInputs(**{**base.__dict__, "stop": 99.9}))
    assert tight.qty == 250 and "max position value" in tight.caps  # 2500 shares would be 2.5 lakh
    gap = position_size(SizeInputs(**{**base.__dict__, "gap_risk_cap_pct": 0.01, "gap95_pct": 0.5}))
    assert gap.qty == 20 and "gap check" in gap.caps  # 50% gap x 100 x qty <= 1,000
    heat = position_size(SizeInputs(**{**base.__dict__, "available_heat_pct": 0.001}))
    assert heat.qty == 20 and "portfolio heat" in heat.caps
    assert not position_size(SizeInputs(**{**base.__dict__, "stop": 100})).viable


def test_gap95() -> None:
    from tests.engine.charts import frame

    closes = [100.0] * 60
    df = frame(closes)
    df.loc[df.index[10], "open"] = 90.0  # one -10% gap
    df.loc[df.index[20], "open"] = 97.0
    g = gap95_pct(df)
    assert g is not None and 0.03 <= g <= 0.10
    assert gap95_pct(df.iloc[:10]) is None
