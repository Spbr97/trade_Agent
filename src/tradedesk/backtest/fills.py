"""Gap-aware fill rules on daily bars (PLAN.md 11).

Entry (long, trigger level L, armed before this bar):
  open > L + chased_atr_mult x ATR  -> CHASED, no trade
  open >= L                         -> filled at the open (+ slippage)
  high >= L                         -> filled at L (+ slippage)
  else, close < stop                -> INVALIDATED (premise gone)
Exit, in this order, for a held position:
  open <= stop                      -> whole position out at the OPEN (the gap rule)
  low  <= stop                      -> out at the stop; if the bar also touched the
                                       target, the stop wins (intrabar ambiguity rule)
  high >= T1 (partial not done)     -> partial at T1, stop moves to breakeven
  trail (after the partial)         -> ema10 closing basis: out at the close;
                                       atr: stop ratchets to highest close - k x ATR
  time stop / max hold              -> out at the close
Daily bars stand in for the 15-minute confirmation until intraday history exists (M7);
the same rules then apply bar-by-bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from tradedesk.engine.signals import Signal


@dataclass(frozen=True)
class Bar:
    on: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    ema10: float
    atr: float


class EntryOutcome(StrEnum):
    NONE = "none"
    FILLED = "filled"
    CHASED = "chased"
    INVALIDATED = "invalidated"


class FillReason(StrEnum):
    ENTRY = "entry"
    GAP_STOP = "gap_stop"
    STOP = "stop"
    PARTIAL = "partial"
    TRAIL = "trail"
    TIME_STOP = "time_stop"
    MAX_HOLD = "max_hold"
    END = "end"


@dataclass(frozen=True)
class Fill:
    on: date
    price: float
    qty: int
    reason: FillReason

    @property
    def is_sell(self) -> bool:
        return self.reason is not FillReason.ENTRY


@dataclass
class Position:
    signal: Signal
    entry_date: date
    entry_price: float
    qty_initial: int
    qty_open: int
    stop: float
    partial_done: bool = False
    highest_close: float = 0.0
    sessions_held: int = 0
    fills: list[Fill] = field(default_factory=list)

    @property
    def initial_risk(self) -> float:
        return (self.entry_price - self.signal.stop) * self.qty_initial

    @property
    def closed(self) -> bool:
        return self.qty_open == 0

    @property
    def exit_date(self) -> date | None:
        sells = [f for f in self.fills if f.is_sell]
        return sells[-1].on if self.closed and sells else None

    def gross_pnl(self) -> float:
        return sum(f.price * f.qty for f in self.fills if f.is_sell) - self.entry_price * (
            self.qty_initial - self.qty_open
        )

    def unrealised_r(self, price: float) -> float:
        if self.initial_risk <= 0:
            return 0.0
        return (price - self.entry_price) * self.qty_initial / self.initial_risk


def evaluate_entry(sig: Signal, bar: Bar, slippage_pct: float) -> tuple[EntryOutcome, float | None]:
    if bar.open > sig.trigger + sig.chased_atr_mult * sig.atr:
        return EntryOutcome.CHASED, None
    if bar.open >= sig.trigger:
        return EntryOutcome.FILLED, bar.open * (1 + slippage_pct)
    if bar.high >= sig.trigger:
        return EntryOutcome.FILLED, sig.trigger * (1 + slippage_pct)
    if bar.close < sig.stop:
        return EntryOutcome.INVALIDATED, None
    return EntryOutcome.NONE, None


def _sell(pos: Position, bar: Bar, price: float, qty: int, reason: FillReason) -> Fill:
    qty = min(qty, pos.qty_open)
    fill = Fill(on=bar.on, price=price, qty=qty, reason=reason)
    pos.fills.append(fill)
    pos.qty_open -= qty
    return fill


def evaluate_exit(
    pos: Position, bar: Bar, slippage_pct: float, *, entry_day: bool = False
) -> list[Fill]:
    """Apply the exit rules for one bar. Returns the fills generated."""
    plan = pos.signal.exit_plan
    fills: list[Fill] = []
    if not entry_day:
        pos.sessions_held += 1
    pos.highest_close = max(pos.highest_close, bar.close)
    slip = 1 - slippage_pct

    if not entry_day and bar.open <= pos.stop:
        fills.append(_sell(pos, bar, bar.open * slip, pos.qty_open, FillReason.GAP_STOP))
        return fills
    if bar.low <= pos.stop:
        fills.append(_sell(pos, bar, pos.stop * slip, pos.qty_open, FillReason.STOP))
        return fills

    if not pos.partial_done and bar.high >= pos.signal.t1:
        qty = max(1, round(pos.qty_initial * plan.partial_fraction))
        if qty >= pos.qty_open:  # tiny position: the "partial" is the whole lot
            fills.append(_sell(pos, bar, pos.signal.t1 * slip, pos.qty_open, FillReason.PARTIAL))
            return fills
        fills.append(_sell(pos, bar, pos.signal.t1 * slip, qty, FillReason.PARTIAL))
        pos.partial_done = True
        pos.stop = max(pos.stop, pos.entry_price)

    if pos.partial_done and pos.qty_open > 0:
        if plan.trail == "ema10_close":
            if bar.close < bar.ema10:
                fills.append(_sell(pos, bar, bar.close * slip, pos.qty_open, FillReason.TRAIL))
                return fills
        else:
            pos.stop = max(pos.stop, pos.highest_close - plan.trail_atr_mult * bar.atr)

    if pos.qty_open > 0 and not entry_day:
        if (
            pos.sessions_held == plan.time_stop_sessions
            and pos.unrealised_r(bar.close) < plan.time_stop_min_r
        ):
            fills.append(_sell(pos, bar, bar.close * slip, pos.qty_open, FillReason.TIME_STOP))
        elif pos.sessions_held >= plan.max_hold_sessions:
            fills.append(_sell(pos, bar, bar.close * slip, pos.qty_open, FillReason.MAX_HOLD))
    return fills
