"""Portfolio state and the hard limits of PLAN.md 1.2, applied in time order.

The same object will back the paper book and the live risk manager (M9); the backtester
is simply its first client. Limits enforced before any entry: max positions, sector cap,
portfolio heat, max new entries per day, weekly loss limit, consecutive-loss pause,
re-entry cooldown, results blackout (done at scan time), regime multiplier.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from tradedesk.backtest.fills import Fill, FillReason, Position
from tradedesk.config.models import ChargeSchedule, RiskConfig
from tradedesk.engine.signals import Signal
from tradedesk.models import Side, TradeType
from tradedesk.risk.costs import leg_cost


@dataclass
class ClosedTrade:
    position: Position
    costs: float
    net_pnl: float
    r_multiple: float
    setup: str
    scrip_code: str
    entry_date: date
    exit_date: date
    sessions_held: int
    exit_reason: str
    gap_damage: float  # rupees lost beyond the planned 1R (0 if none)


@dataclass
class Portfolio:
    risk: RiskConfig
    costs: ChargeSchedule
    equity: float
    sector_of: Mapping[str, str] = field(default_factory=dict)
    open: dict[str, Position] = field(default_factory=dict)
    closed: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    rejections: list[tuple[date, str, str]] = field(default_factory=list)  # (date, signal id, why)
    # rolling state
    session_index: int = 0
    entries_today: int = 0
    today: date | None = None
    week_key: tuple[int, int] | None = None
    week_start_equity: float = 0.0
    consecutive_losses: int = 0
    pause_until_session: int = -1
    last_stop_out: dict[str, int] = field(default_factory=dict)  # code -> session index

    # ------------------------------------------------------------- sessions

    def start_session(self, on: date, session_index: int) -> None:
        self.today = on
        self.session_index = session_index
        self.entries_today = 0
        key = on.isocalendar()[:2]
        if key != self.week_key:
            self.week_key = key
            self.week_start_equity = self.equity

    def mark_to_market(self, closes: Mapping[str, float]) -> None:
        assert self.today is not None
        value = self.equity
        for code, pos in self.open.items():
            px = closes.get(code, pos.entry_price)
            value += (px - pos.entry_price) * pos.qty_open
        self.equity_curve.append((self.today, value))

    # ---------------------------------------------------------------- limits

    def heat_pct(self) -> float:
        """Open risk (entry - current stop, floored at 0) as a fraction of equity."""
        risk = sum(max(0.0, p.entry_price - p.stop) * p.qty_open for p in self.open.values())
        return risk / self.equity if self.equity > 0 else 0.0

    def available_heat_pct(self) -> float:
        return float(self.risk.max_portfolio_heat_pct) - self.heat_pct()

    def weekly_loss_hit(self) -> bool:
        if self.week_start_equity <= 0:
            return False
        return self.equity <= self.week_start_equity * (1 - float(self.risk.weekly_loss_limit_pct))

    def can_enter(self, sig: Signal, size_multiplier: float) -> tuple[bool, str]:
        if size_multiplier <= 0:
            return False, "regime risk_off"
        if sig.scrip_code in self.open:
            return False, "already holding"
        if len(self.open) >= self.risk.max_open_positions:
            return False, "max open positions"
        if self.entries_today >= self.risk.max_new_entries_per_day:
            return False, "max new entries today"
        if self.weekly_loss_hit():
            return False, "weekly loss limit"
        if self.session_index < self.pause_until_session:
            return False, "consecutive-loss pause"
        last = self.last_stop_out.get(sig.scrip_code)
        if last is not None and self.session_index - last < self.risk.reentry_cooldown_sessions:
            return False, "re-entry cooldown"
        sector = self.sector_of.get(sig.scrip_code)
        if sector is not None:
            same = sum(1 for c in self.open if self.sector_of.get(c) == sector)
            if same >= self.risk.max_per_sector:
                return False, f"sector cap ({sector})"
        if self.available_heat_pct() <= 0:
            return False, "portfolio heat"
        return True, ""

    # ------------------------------------------------------------ positions

    def open_position(self, pos: Position) -> None:
        assert self.today is not None
        self.open[pos.signal.scrip_code] = pos
        self.entries_today += 1

    def record_fills(self, pos: Position, fills: list[Fill]) -> None:
        """Book realised P&L when a position is fully closed."""
        if not pos.closed:
            return
        code = pos.signal.scrip_code
        self.open.pop(code, None)
        trade = self._settle(pos)
        self.closed.append(trade)
        self.equity += trade.net_pnl
        if trade.net_pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.risk.consecutive_loss_pause.losses:
                self.pause_until_session = (
                    self.session_index + 1 + self.risk.consecutive_loss_pause.sessions
                )
                self.consecutive_losses = 0
        else:
            self.consecutive_losses = 0
        if trade.exit_reason in (FillReason.STOP.value, FillReason.GAP_STOP.value):
            self.last_stop_out[code] = self.session_index

    def _settle(self, pos: Position) -> ClosedTrade:
        sells = [f for f in pos.fills if f.is_sell]
        exit_date = sells[-1].on
        same_day = exit_date == pos.entry_date and all(f.on == pos.entry_date for f in sells)
        trade_type = TradeType.INTRADAY if same_day else TradeType.DELIVERY
        total = leg_cost(
            self.costs,
            side=Side.BUY,
            trade_type=trade_type,
            qty=pos.qty_initial,
            price=Decimal(str(round(pos.entry_price, 2))),
        ).total
        seen_days: set[date] = set()
        for f in sells:
            total += leg_cost(
                self.costs,
                side=Side.SELL,
                trade_type=trade_type,
                qty=f.qty,
                price=Decimal(str(round(f.price, 2))),
                dp_applies=f.on not in seen_days,
            ).total
            seen_days.add(f.on)
        costs = float(total)
        net = pos.gross_pnl() - costs
        risk = pos.initial_risk
        r = net / risk if risk > 0 else 0.0
        gap_damage = max(0.0, -net - risk) if net < -risk else 0.0
        return ClosedTrade(
            position=pos,
            costs=costs,
            net_pnl=net,
            r_multiple=r,
            setup=pos.signal.setup.value,
            scrip_code=pos.signal.scrip_code,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            sessions_held=pos.sessions_held,
            exit_reason=sells[-1].reason.value,
            gap_damage=gap_damage,
        )
