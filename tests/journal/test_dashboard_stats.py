"""EOD / weekly / monthly performance views (dashboard follow-up): journal/stats.py's
eod_report and performance_rollup are pure reporting over the paper book's closed trades -
no new signal, sizing or backtest logic, so these tests are about the grouping/aggregation
being right, not about trading behaviour."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.data.synth import sessions
from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.backtest.portfolio import Portfolio
from tradedesk.config.models import RiskConfig
from tradedesk.engine.signals import SetupKind, Signal
from tradedesk.journal import Journal
from tradedesk.journal.stats import eod_report, performance_rollup
from tradedesk.markets import EquityCostModel

RISK = RiskConfig(trading_capital=Decimal("1000000"))
CAL = sessions(date(2026, 1, 5), 90)  # spans several ISO weeks and calendar months


def _closed_paper_trade(
    j: Journal, i: int, entry_on: date, exit_on: date, r: float, setup: str = "base_breakout"
) -> None:
    s = Signal(
        id=f"{setup}:NSE_{i}:{entry_on.isoformat()}", scrip_code=f"NSE_{i}", symbol=f"S{i}",
        setup=SetupKind(setup), armed_on=entry_on, trigger=100.0, stop=95.0, t1=110.0, t2=115.0,
        atr=2.0,
    )  # fmt: skip
    pf = Portfolio(risk=RISK, costs=EquityCostModel(RISK.costs), equity=1_000_000.0)
    pos = Position(
        signal=s, entry_date=entry_on, entry_price=100.0, qty_initial=100, qty_open=0, stop=95.0,
        highest_close=100.0, sessions_held=(exit_on - entry_on).days,
        fills=[Fill(on=entry_on, price=100.0, qty=100, reason=FillReason.ENTRY),
               Fill(on=exit_on, price=100.0 + 5 * r, qty=100, reason=FillReason.TRAIL)],
    )  # fmt: skip
    j.record_trade(pf.settle(pos), source="paper")


def test_eod_report_counts_only_trades_that_exited_that_day() -> None:
    with Journal() as j:
        _closed_paper_trade(j, 1, CAL[0], CAL[2], r=+1.0)
        _closed_paper_trade(j, 2, CAL[0], CAL[2], r=-1.0)
        _closed_paper_trade(j, 3, CAL[0], CAL[5], r=+1.0)  # different exit day
        rep = eod_report(j, CAL[2])
        assert rep["date"] == CAL[2].isoformat()
        assert rep["closed_count"] == 2
        assert {t["symbol"] for t in rep["closed_trades"]} == {"S1", "S2"}
        assert rep["win_rate"] == 0.5
        # a day with nothing closed and nothing open is still a well-formed, empty report
        empty = eod_report(j, CAL[10])
        assert empty["closed_count"] == 0 and empty["net_pnl"] == 0 and empty["open_positions"] == 0


def test_eod_report_reports_still_open_positions() -> None:
    with Journal() as j:
        _closed_paper_trade(j, 1, CAL[0], CAL[2], r=+1.0)
        s = Signal(
            id="base_breakout:NSE_9:x", scrip_code="NSE_9", symbol="OPEN9",
            setup=SetupKind.BASE_BREAKOUT, armed_on=CAL[0], trigger=100.0, stop=95.0,
            t1=110.0, t2=115.0, atr=2.0,
        )  # fmt: skip
        j.save_position(
            Position(
                signal=s,
                entry_date=CAL[0],
                entry_price=100.0,
                qty_initial=100,
                qty_open=100,
                stop=95.0,
                highest_close=100.0,
                fills=[Fill(on=CAL[0], price=100.0, qty=100, reason=FillReason.ENTRY)],
            ),
            source="paper",
        )
        rep = eod_report(j, CAL[2])
        assert rep["open_positions"] == 1 and rep["open_symbols"] == ["OPEN9"]


def test_performance_rollup_groups_by_exit_week_and_month() -> None:
    with Journal() as j:
        # two trades exiting in the same ISO week, one a month later
        _closed_paper_trade(j, 1, CAL[0], CAL[1], r=+1.0)
        _closed_paper_trade(j, 2, CAL[0], CAL[1], r=-0.5)
        _closed_paper_trade(j, 3, CAL[0], CAL[40], r=+2.0)

        weekly = performance_rollup(j, period="week", n=52)
        by_week = {row["period"]: row for row in weekly}
        this_week = f"{CAL[1].isocalendar()[0]}-W{CAL[1].isocalendar()[1]:02d}"
        assert by_week[this_week]["trades"] == 2
        assert by_week[this_week]["win_rate"] == 0.5

        monthly = performance_rollup(j, period="month", n=12)
        by_month = {row["period"]: row for row in monthly}
        assert by_month[f"{CAL[1].year}-{CAL[1].month:02d}"]["trades"] == 2
        assert by_month[f"{CAL[40].year}-{CAL[40].month:02d}"]["trades"] == 1
        total_trades = sum(row["trades"] for row in monthly)
        assert total_trades == 3  # every closed trade lands in exactly one month


def test_performance_rollup_respects_n_and_is_empty_when_no_paper_trades() -> None:
    with Journal() as j:
        assert performance_rollup(j, period="week") == []
        for i in range(5):
            _closed_paper_trade(j, i, CAL[0], CAL[1 + i], r=1.0)
        assert len(performance_rollup(j, period="week", n=1)) <= 1
