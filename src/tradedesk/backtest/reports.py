"""Backtest metrics (PLAN.md 1.5): expectancy in R, profit factor, drawdown, gap damage,
per setup and per period (walk-forward split), always net of costs and slippage."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from tradedesk.backtest.portfolio import ClosedTrade
from tradedesk.backtest.runner import BacktestResult
from tradedesk.engine.lifecycle import SignalState


@dataclass
class Metrics:
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    net_pnl: float = 0.0
    total_costs: float = 0.0
    avg_sessions_held: float = 0.0
    gap_losses: int = 0  # trades that lost more than the planned 1R
    gap_damage: float = 0.0  # rupees lost beyond 1R across those trades
    exit_reasons: dict[str, int] = field(default_factory=dict)

    def to_row(self) -> dict[str, float | int]:
        return {
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "avg_win_r": round(self.avg_win_r, 2),
            "avg_loss_r": round(self.avg_loss_r, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "profit_factor": round(self.profit_factor, 2),
            "net_pnl": round(self.net_pnl, 0),
            "costs": round(self.total_costs, 0),
            "avg_hold": round(self.avg_sessions_held, 1),
            "gap_losses": self.gap_losses,
            "gap_damage": round(self.gap_damage, 0),
        }


def metrics(trades: Sequence[ClosedTrade]) -> Metrics:
    m = Metrics(trades=len(trades))
    if not trades:
        return m
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    m.wins = len(wins)
    m.win_rate = len(wins) / len(rs)
    m.avg_win_r = sum(wins) / len(wins) if wins else 0.0
    m.avg_loss_r = sum(losses) / len(losses) if losses else 0.0
    m.expectancy_r = sum(rs) / len(rs)
    gross_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_loss = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    m.profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit else 0.0
    )
    m.net_pnl = sum(t.net_pnl for t in trades)
    m.total_costs = sum(t.costs for t in trades)
    m.avg_sessions_held = sum(t.sessions_held for t in trades) / len(trades)
    gap = [t for t in trades if t.gap_damage > 0]
    m.gap_losses = len(gap)
    m.gap_damage = sum(t.gap_damage for t in gap)
    for t in trades:
        m.exit_reasons[t.exit_reason] = m.exit_reasons.get(t.exit_reason, 0) + 1
    return m


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough fall as a fraction of the peak."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(-dd.min())


def log_returns(equity: pd.Series) -> pd.Series:
    """Log returns, not simple pct_change - found to matter for real on a lumpy trade-based
    equity curve (equity_curve_from_trades): a round trip up X% then back down to the same
    level has an ARITHMETIC mean return that is spuriously positive (Jensen's inequality -
    e.g. +9% then -8.26% nets to 0% growth but a +0.37% arithmetic mean), which produced a
    real, observed case of a NEGATIVE-CAGR strategy reporting a POSITIVE Sharpe. Log returns
    are additive and don't have this bias: the same round trip nets to exactly 0."""
    import numpy as np

    ratio = equity / equity.shift(1)
    return pd.Series(np.log(ratio[ratio > 0]), index=ratio[ratio > 0].index)


def sharpe_ratio(daily_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualised Sharpe (rf=0) from a series of PERIODIC returns - pass day-over-day
    equity % change for a daily Sharpe, matching `periods_per_year`'s default of 252
    trading sessions. 0.0 on fewer than 2 points or zero variance rather than raising or
    returning nan/inf, since a short or flat window is a real, unremarkable case here."""
    import math

    r = daily_returns.dropna()
    if len(r) < 2:
        return 0.0
    sd = float(r.std(ddof=0))
    if sd == 0.0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def cagr(equity: pd.Series, start: date, end: date) -> float:
    """Compound annual growth rate from the first to the last equity value over
    `start..end`. 0.0 on an empty/non-positive series or a sub-day window."""
    if equity.empty or float(equity.iloc[0]) <= 0:
        return 0.0
    years = max((end - start).days / 365.25, 1.0 / 365.25)
    return float((float(equity.iloc[-1]) / float(equity.iloc[0])) ** (1.0 / years) - 1.0)


def equity_curve_from_trades(
    trades: Sequence[ClosedTrade], calendar: Sequence[date], starting_capital: float
) -> pd.Series:
    """A trade-level equity curve for comparing two COHORTS of the same trade list (e.g.
    "all triggered signals" vs "signals an ML filter would have kept") - NOT a substitute
    for `BacktestResult.equity` (backtest/runner.py), which is the real bar-by-bar,
    crowding-aware mark-to-market curve for the portfolio actually simulated. Re-running
    the portfolio's heat/position-count limits for a filtered subset would require a whole
    second backtest; this instead compounds `net_pnl` serially in exit-date order on top of
    `calendar`, forward-filling between exits, as a deliberately simplified idealisation
    good enough for a Sharpe/CAGR/drawdown COMPARISON between two cohorts, not for reporting
    either cohort's number as if it were a real, capital-constrained outcome."""
    # Every position starts as NaN, not `starting_capital` - a real bug found by testing
    # this directly: pre-filling every day with a real number left nothing for ffill() to
    # propagate, so any day after the last-recorded update silently reverted to the
    # STARTING capital instead of carrying the latest cumulative equity forward.
    idx = pd.Index(list(calendar), name="date")
    equity = pd.Series(float("nan"), index=idx, dtype=float)
    if len(idx):
        equity.iloc[0] = starting_capital
    running = starting_capital
    for t in sorted(trades, key=lambda t: t.exit_date):
        running += t.net_pnl
        if t.exit_date in equity.index:
            equity.at[t.exit_date] = float(running)
    return equity.ffill()


@dataclass
class Report:
    overall: Metrics
    by_setup: dict[str, Metrics]
    max_drawdown_pct: float
    start_equity: float
    end_equity: float
    signal_states: dict[str, int]
    rejections: dict[str, int]

    def frame(self) -> pd.DataFrame:
        rows = {"ALL": self.overall.to_row()}
        rows.update({k: v.to_row() for k, v in self.by_setup.items()})
        return pd.DataFrame(rows).T

    def text(self) -> str:
        lines = [
            f"equity {self.start_equity:,.0f} -> {self.end_equity:,.0f}  "
            f"max drawdown {self.max_drawdown_pct:.1%}",
            self.frame().to_string(),
            "signals: " + ", ".join(f"{k} {v}" for k, v in sorted(self.signal_states.items())),
        ]
        if self.rejections:
            lines.append(
                "rejections: " + ", ".join(f"{k} {v}" for k, v in sorted(self.rejections.items()))
            )
        if self.overall.exit_reasons:
            lines.append(
                "exits: "
                + ", ".join(f"{k} {v}" for k, v in sorted(self.overall.exit_reasons.items()))
            )
        return "\n".join(lines)


def build_report(
    result: BacktestResult, *, start: date | None = None, end: date | None = None
) -> Report:
    trades = [
        t
        for t in result.portfolio.closed
        if (start is None or t.entry_date >= start) and (end is None or t.entry_date <= end)
    ]
    by_setup: dict[str, Metrics] = {}
    for kind in sorted({t.setup for t in trades}):
        by_setup[kind] = metrics([t for t in trades if t.setup == kind])
    eq = result.equity
    if start is not None or end is not None:
        mask = pd.Series(True, index=eq.index)
        if start is not None:
            mask &= pd.Index(eq.index) >= start
        if end is not None:
            mask &= pd.Index(eq.index) <= end
        eq = eq[mask.to_numpy()]
    states: dict[str, int] = {}
    for ts in result.signals:
        if start is not None and ts.signal.armed_on < start:
            continue
        if end is not None and ts.signal.armed_on > end:
            continue
        states[ts.state.value] = states.get(ts.state.value, 0) + 1
    rejections: dict[str, int] = {}
    for on, _sid, why in result.portfolio.rejections:
        if (start is None or on >= start) and (end is None or on <= end):
            key = why.split(":")[0]
            rejections[key] = rejections.get(key, 0) + 1
    return Report(
        overall=metrics(trades),
        by_setup=by_setup,
        max_drawdown_pct=max_drawdown(eq),
        start_equity=float(eq.iloc[0]) if not eq.empty else result.config.capital,
        end_equity=float(eq.iloc[-1]) if not eq.empty else result.config.capital,
        signal_states=states,
        rejections=rejections,
    )


def walk_forward(result: BacktestResult, split: date) -> tuple[Report, Report]:
    """In-sample (before `split`) and out-of-sample (from `split`) reports of one run.
    Parameters are tuned on the first and judged on the second (PLAN.md 11)."""
    return (
        build_report(result, end=split),
        build_report(result, start=split),
    )


def triggered_signals(result: BacktestResult) -> list[SignalState]:
    return [ts.state for ts in result.signals if ts.state is not SignalState.ARMED]
