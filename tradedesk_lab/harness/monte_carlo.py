"""Phase 4's Monte Carlo trade-reorder - confirmed absent everywhere else in this repo before
writing this. Reshuffles a FIXED trade list's realised R-multiples (same trades, random
order only - never invents an outcome) to get the drawdown and losing-streak distribution a
single historical ordering can't show: "the realistic drawdown and losing-streak
distribution," per the template.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tradedesk.backtest.reports import longest_losing_streak


@dataclass(frozen=True)
class MonteCarloResult:
    n_cohorts: int
    n_trades: int
    max_drawdown_pct: dict[str, float]  # percentile label -> value, e.g. {"p50": ..., "p99": ...}
    losing_streak: dict[str, float]
    seed: int


def _drawdown_pct(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(-dd.min()) if len(equity) else 0.0


def monte_carlo_trade_reorder(
    r_multiples: list[float],
    *,
    starting_capital: float = 100_000.0,
    risk_per_trade_pct: float = 0.0025,
    n_cohorts: int = 10_000,
    seed: int = 20260101,
) -> MonteCarloResult:
    """Sizes every trade at a fixed `risk_per_trade_pct` of a compounding equity curve (the
    same convention `risk.sizing`/`Portfolio` use elsewhere for a per-trade risk budget), then
    reshuffles trade order `n_cohorts` times to build the drawdown/losing-streak distribution.
    Raises on an empty list rather than returning a default result that would look like a
    real (empty) measurement."""
    if not r_multiples:
        raise ValueError("no trades to reshuffle - monte_carlo_trade_reorder needs at least one")
    rng = np.random.default_rng(seed)
    rs = np.asarray(r_multiples, dtype=float)
    drawdowns = np.empty(n_cohorts)
    streaks = np.empty(n_cohorts, dtype=int)
    for i in range(n_cohorts):
        order = rng.permutation(rs)
        equity = np.concatenate(
            (
                [starting_capital],
                starting_capital + np.cumsum(order * risk_per_trade_pct * starting_capital),
            )
        )
        drawdowns[i] = _drawdown_pct(equity)
        streaks[i] = longest_losing_streak(order.tolist())
    percentiles = (50, 95, 99)
    return MonteCarloResult(
        n_cohorts=n_cohorts,
        n_trades=len(rs),
        max_drawdown_pct={f"p{p}": float(np.percentile(drawdowns, p)) for p in percentiles},
        losing_streak={f"p{p}": float(np.percentile(streaks, p)) for p in percentiles},
        seed=seed,
    )
