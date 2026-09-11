"""Paper book (PLAN.md 8): every TRIGGERED signal is simulated whether you take it or
not - entry at the confirming 15-minute close plus slippage, the exit plan applied to
each session's daily bar with the gap rule, full delivery costs. It is the forward test,
the source of per-setup track records (auto-bench) and, later, the labels for M11.

Sizing follows risk.yaml against a paper equity that only accumulates paper P&L. Portfolio
crowding limits are NOT applied here on purpose: the paper book measures the setups, not
the order in which signals happened to arrive.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from tradedesk.backtest.fills import Bar, Fill, FillReason, Position, evaluate_exit
from tradedesk.backtest.portfolio import ClosedTrade, Portfolio
from tradedesk.config.models import RiskConfig
from tradedesk.engine.lifecycle import TrackedSignal
from tradedesk.journal.db import Journal
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size


def bar_from_row(on: date, row: pd.Series) -> Bar:
    return Bar(
        on=on,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        ema10=float(row["ema10"])
        if "ema10" in row and pd.notna(row["ema10"])
        else float(row["close"]),
        atr=float(row["atr14"]) if "atr14" in row and pd.notna(row["atr14"]) else 0.0,
    )


@dataclass
class PaperBook:
    journal: Journal
    risk: RiskConfig
    capital: float
    slippage_pct: float = 0.0005
    positions: dict[str, Position] = field(default_factory=dict)  # by signal id
    _settler: Portfolio = field(init=False)

    def __post_init__(self) -> None:
        self._settler = Portfolio(risk=self.risk, costs=self.risk.costs, equity=self.capital)
        for p in self.journal.open_positions(source="paper"):
            self.positions[p.signal.id] = p

    @property
    def equity(self) -> float:
        realised = sum(float(r["net_pnl"]) for r in self.journal.trades(source="paper"))
        return self.capital + realised

    # ------------------------------------------------------------ entries

    def open_from_trigger(
        self,
        ts: TrackedSignal,
        on: date,
        fill_price: float,
        feats: pd.DataFrame,
        *,
        size_multiplier: float = 1.0,
    ) -> Position | None:
        """Simulate the fill for a TRIGGERED signal; `feats` is the daily feature frame up
        to and including `on` (its last row is the entry day's bar)."""
        sig = ts.signal
        if (
            sig.id in self.positions
            or self.journal.con.execute(
                "SELECT 1 FROM paper_trades WHERE signal_id = ?", (sig.id,)
            ).fetchone()
        ):
            return None
        entry = fill_price * (1 + self.slippage_pct)
        size = position_size(
            SizeInputs(
                equity=self.equity,
                entry=entry,
                stop=sig.stop,
                max_risk_pct=float(self.risk.max_risk_per_trade_pct),
                max_position_value_pct=float(self.risk.max_position_value_pct),
                size_multiplier=size_multiplier if size_multiplier > 0 else 1.0,
                gap_risk_cap_pct=float(self.risk.gap_risk_cap_pct),
                gap95_pct=gap95_pct(feats),
            )
        )
        if not size.viable:
            return None
        pos = Position(
            signal=sig,
            entry_date=on,
            entry_price=entry,
            qty_initial=size.qty,
            qty_open=size.qty,
            stop=sig.stop,
            highest_close=entry,
            fills=[Fill(on=on, price=entry, qty=size.qty, reason=FillReason.ENTRY)],
        )
        self.positions[sig.id] = pos
        self.journal.upsert_signal(ts)
        # Entry-day exit rules on the rest of the day (daily bar approximation).
        bar = bar_from_row(on, feats.iloc[-1])
        fills = evaluate_exit(pos, bar, self.slippage_pct, entry_day=True)
        self._after_fills(pos, fills, on)
        return pos

    # ------------------------------------------------------------- marking

    def mark(self, on: date, bars: Mapping[str, Bar]) -> list[ClosedTrade]:
        """Apply session `on` to every open paper position that has a bar. Returns the
        trades closed today."""
        closed: list[ClosedTrade] = []
        for sid, pos in list(self.positions.items()):
            if pos.entry_date >= on:
                continue
            bar = bars.get(pos.signal.scrip_code)
            if bar is None:
                continue
            old_stop = pos.stop
            fills = evaluate_exit(pos, bar, self.slippage_pct)
            if pos.stop != old_stop:
                self.journal.record_stop_update(sid, on, old_stop, pos.stop, "exit plan")
            trade = self._after_fills(pos, fills, on)
            if trade is not None:
                closed.append(trade)
        return closed

    def _after_fills(self, pos: Position, fills: list[Fill], on: date) -> ClosedTrade | None:
        if pos.closed:
            trade = self._settler.settle(pos)
            self.journal.record_trade(trade, source="paper")
            self.positions.pop(pos.signal.id, None)
            return trade
        self.journal.save_position(pos, source="paper")
        return None
