"""Live risk manager (PLAN.md 1.2, 8): the same `Portfolio` limits the backtester uses,
rebuilt from the journal's real trades and open positions, plus the results blackout and
the regime multiplier. Produces a RiskStatus for the trade card and the dashboard.

`rebuild()` replays closed live trades in exit order through a fresh Portfolio so the
counters that depend on history (weekly start equity, consecutive losses, pause, re-entry
cooldown) are recovered deterministically after a restart.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, ConfigDict

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.backtest.portfolio import ClosedTrade, Portfolio
from tradedesk.config.models import RiskConfig
from tradedesk.engine.signals import Signal
from tradedesk.journal.db import Journal


class RiskStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: date
    equity: float
    open_positions: int
    heat_pct: float
    heat_cap_pct: float
    entries_today: int
    week_start_equity: float
    weekly_pnl_pct: float
    weekly_locked: bool
    consecutive_losses: int
    paused: bool
    cooldowns: dict[str, int]  # code -> sessions remaining
    size_multiplier: float


@dataclass
class RiskManager:
    risk: RiskConfig
    journal: Journal
    capital: float
    calendar: Sequence[date]  # trading sessions, ascending, covering the journal's history
    sector_of: Mapping[str, str] = field(default_factory=dict)
    portfolio: Portfolio = field(init=False)

    def __post_init__(self) -> None:
        self.rebuild()

    def _idx(self, on: date) -> int:
        return bisect.bisect_left(list(self.calendar), on)

    def rebuild(self) -> None:
        pf = Portfolio(
            risk=self.risk, costs=self.risk.costs, equity=self.capital, sector_of=self.sector_of
        )
        trades = sorted(
            self.journal.trades(source="live"), key=lambda r: (r["exit_date"], r["signal_id"])
        )
        current: date | None = None
        for r in trades:
            exit_on = date.fromisoformat(r["exit_date"])
            if exit_on != current:
                current = exit_on
                pf.start_session(exit_on, self._idx(exit_on))
            entry_on = date.fromisoformat(r["entry_date"])
            qty = int(r["qty"])
            pos = Position(
                signal=_signal_from_trade(r),
                entry_date=entry_on,
                entry_price=float(r["entry_price"]),
                qty_initial=qty,
                qty_open=0,
                stop=float(r["entry_price"]),
                sessions_held=int(r["sessions_held"]),
                fills=[
                    Fill(
                        on=entry_on, price=float(r["entry_price"]), qty=qty, reason=FillReason.ENTRY
                    )
                ],
            )
            trade = ClosedTrade(
                position=pos,
                costs=float(r["costs"]),
                net_pnl=float(r["net_pnl"]),
                r_multiple=float(r["r_multiple"]),
                setup=str(r["setup"]),
                scrip_code=str(r["scrip_code"]),
                entry_date=entry_on,
                exit_date=exit_on,
                sessions_held=int(r["sessions_held"]),
                exit_reason=str(r["exit_reason"]),
                gap_damage=float(r["gap_damage"] or 0.0),
            )
            pf.closed.append(trade)
            pf.equity += trade.net_pnl
            if trade.net_pnl < 0:
                pf.consecutive_losses += 1
                if pf.consecutive_losses >= self.risk.consecutive_loss_pause.losses:
                    pf.pause_until_session = (
                        pf.session_index + 1 + self.risk.consecutive_loss_pause.sessions
                    )
                    pf.consecutive_losses = 0
            else:
                pf.consecutive_losses = 0
            if trade.exit_reason in (FillReason.STOP.value, FillReason.GAP_STOP.value):
                pf.last_stop_out[trade.scrip_code] = pf.session_index
        for pos in self.journal.open_positions(source="live"):
            pf.open[pos.signal.scrip_code] = pos
        self.portfolio = pf

    def start_session(self, on: date) -> None:
        self.portfolio.start_session(on, self._idx(on))

    def can_enter(
        self,
        sig: Signal,
        *,
        size_multiplier: float,
        results_in_sessions: int | None = None,
    ) -> tuple[bool, str]:
        if results_in_sessions is not None and results_in_sessions <= self.risk.max_hold_sessions:
            return False, f"results in {results_in_sessions} sessions (blackout)"
        return self.portfolio.can_enter(sig, size_multiplier)

    def status(self, on: date, size_multiplier: float = 1.0) -> RiskStatus:
        pf = self.portfolio
        if pf.today != on:
            self.start_session(on)
        cooldowns = {
            code: self.risk.reentry_cooldown_sessions - (pf.session_index - idx)
            for code, idx in pf.last_stop_out.items()
            if pf.session_index - idx < self.risk.reentry_cooldown_sessions
        }
        weekly = (pf.equity / pf.week_start_equity - 1) if pf.week_start_equity else 0.0
        return RiskStatus(
            on=on,
            equity=pf.equity,
            open_positions=len(pf.open),
            heat_pct=pf.heat_pct(),
            heat_cap_pct=float(self.risk.max_portfolio_heat_pct),
            entries_today=pf.entries_today,
            week_start_equity=pf.week_start_equity,
            weekly_pnl_pct=weekly,
            weekly_locked=pf.weekly_loss_hit(),
            consecutive_losses=pf.consecutive_losses,
            paused=pf.session_index < pf.pause_until_session,
            cooldowns=cooldowns,
            size_multiplier=size_multiplier,
        )


def _signal_from_trade(r: object) -> Signal:
    from tradedesk.engine.signals import SetupKind

    row = r  # sqlite3.Row
    return Signal(
        id=row["signal_id"],  # type: ignore[index]
        scrip_code=row["scrip_code"],  # type: ignore[index]
        symbol=row["symbol"],  # type: ignore[index]
        setup=SetupKind(row["setup"]),  # type: ignore[index]
        armed_on=date.fromisoformat(row["entry_date"]),  # type: ignore[index]
        trigger=float(row["entry_price"]),  # type: ignore[index]
        stop=float(row["entry_price"]) - 1.0,  # type: ignore[index]
        t1=float(row["entry_price"]) + 2.0,  # type: ignore[index]
        t2=float(row["entry_price"]) + 3.0,  # type: ignore[index]
        atr=1.0,
    )
