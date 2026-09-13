"""The gate any intraday setup must clear before it may alert (SDD sections 18/23, step 6
of the 2026-09-13 intraday plan; generalises the ad hoc null tests in
scripts/barrier_sweep.py and scripts/entry_search.py into reusable, importable machinery).

This session's central finding was that a setup can post an ordinary-looking win rate and
positive expectancy while being WORSE than picking a random entry on the same stock and
day - all three of the project's live daily setups do exactly this. A win-rate or
expectancy threshold alone cannot see that; only a matched random-timing comparison can.
`run_null_baseline` is that comparison, generalised so any future intraday setup (not just
VWAP Reclaim) can be checked the same way before it is trusted.

Method: for each REAL trade the setup produced, draw `n_cohorts` alternate entries at random
bar positions WITHIN THE SAME TRADING SESSION on the SAME instrument - same day, same stock,
so nothing about the market regime or that day's character differs, only the moment of
entry - each using the identical stop distance (in ATR units) and target multiple as the real
trade, resolved with the identical gap-aware, stop-wins-ties barrier the rest of this project
uses. Cohort j's mean R is the average, across every real trade, of that trade's j-th random
draw - a full alternate "what if you entered randomly instead" universe with the same trade
count, same stocks, same days. `TrackRecord.random_baseline_r` (engine/scoring.py) is exactly
this result's `null_mean_gross_r`; `journal/stats.py::eligibility()` is the caller that
turns it into a pass/fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.markets.costs import EquityCostModel
from tradedesk.models import TradeType

DEFAULT_SEED = 20260101


@dataclass(frozen=True)
class RealisedTrade:
    """One real, already-resolved trade the setup produced - the input `run_null_baseline`
    compares against matched random alternatives."""

    scrip_code: str
    entry_bar: int  # positional index into bars_by_code[scrip_code], the bar entry filled on
    session_start: int  # first positional index of that trade's session
    session_end: int  # last positional index of that trade's session (inclusive)
    entry: float
    stop: float
    target: float
    gross_r: float


@dataclass(frozen=True)
class NullBaselineResult:
    n_trades: int
    n_cohorts: int
    setup_mean_gross_r: float
    setup_mean_net_r: float
    null_mean_gross_r: float  # mean, across cohorts, of that cohort's mean R - THIS populates TrackRecord.random_baseline_r  # noqa: E501
    null_std_gross_r: float
    p_value: float  # fraction of cohorts whose mean R >= the setup's - small means real edge
    passes: bool  # p_value < alpha
    alpha: float


def _bar_barrier(
    o: Any, h: Any, low_: Any, c: Any, start: int, end: int, *, entry: float, stop: float,
    target: float,
) -> float:
    """Gross R from `start` to at most `end` (inclusive, a session boundary - intraday
    setups never hold overnight). Same gap-aware, stop-wins-ties rule as
    prediction/labeling.py::triple_barrier and scripts/barrier_sweep.py, at bar
    granularity instead of session granularity."""
    risk = entry - stop
    n = min(end - start, len(c) - start - 1) + 1
    for k in range(n):
        i = start + k
        if k > 0 and o[i] <= stop:
            return (float(o[i]) - entry) / risk
        if low_[i] <= stop:
            return -1.0
        if h[i] >= target:
            return (target - entry) / risk
    last = min(start + n - 1, end, len(c) - 1)
    return (float(c[last]) - entry) / risk


def run_null_baseline(
    trades: list[RealisedTrade],
    bars_by_code: dict[str, tuple[Any, Any, Any, Any]],  # scrip_code -> (open, high, low, close) arrays  # noqa: E501
    *,
    n_cohorts: int = 1000,
    seed: int = DEFAULT_SEED,
    alpha: float = 0.05,
    cost_model: EquityCostModel | None = None,
    risk_rupees: float = 500.0,
) -> NullBaselineResult:
    """Compare `trades`' realised mean R against `n_cohorts` matched random-entry
    alternatives, same stock and session, same stop-distance and target multiple.

    Raises if `trades` is empty - there is nothing to validate, and returning some default
    "passes=False" result would look like a real (failed) measurement rather than "not run"."""
    if not trades:
        raise ValueError("no trades to validate - run_null_baseline needs at least one")

    rng = np.random.default_rng(seed)
    setup_r = np.array([t.gross_r for t in trades], dtype=float)
    setup_mean_gross_r = float(setup_r.mean())

    cohort_means = np.zeros(n_cohorts)
    for t in trades:
        o, h, low_, c = bars_by_code[t.scrip_code]
        risk_per_share = t.entry - t.stop
        target_r = (t.target - t.entry) / risk_per_share
        span = t.session_end - t.session_start + 1
        if span <= 1:
            cohort_means += setup_mean_gross_r / len(trades)  # degenerate session: no draw possible  # noqa: E501
            continue
        draws = rng.integers(t.session_start, t.session_end, size=n_cohorts)  # exclusive of the last bar so there is room to resolve  # noqa: E501
        for j, start in enumerate(draws):
            entry = float(o[start + 1]) if start + 1 < len(o) else float(c[start])
            stop = entry - risk_per_share
            target = entry + target_r * risk_per_share
            r = _bar_barrier(o, h, low_, c, start + 1, t.session_end, entry=entry, stop=stop, target=target)  # noqa: E501
            cohort_means[j] += r / len(trades)

    null_mean = float(cohort_means.mean())
    null_std = float(cohort_means.std())
    p_value = float(np.mean(cohort_means >= setup_mean_gross_r))

    net_r = []
    cm = cost_model
    for t in trades:
        if cm is None:
            net_r.append(t.gross_r)
            continue
        qty = max(1.0, round(risk_rupees / (t.entry - t.stop)))
        exit_price = t.entry + t.gross_r * (t.entry - t.stop)
        cost = cm.round_trip_cost(
            trade_type=TradeType.INTRADAY, qty=qty,
            entry_price=Decimal(str(round(t.entry, 2))), exit_price=Decimal(str(round(exit_price, 2))),  # noqa: E501
        )
        cost_r = float(cost.total) / (qty * (t.entry - t.stop))
        net_r.append(t.gross_r - cost_r)

    return NullBaselineResult(
        n_trades=len(trades),
        n_cohorts=n_cohorts,
        setup_mean_gross_r=setup_mean_gross_r,
        setup_mean_net_r=float(np.mean(net_r)),
        null_mean_gross_r=null_mean,
        null_std_gross_r=null_std,
        p_value=p_value,
        passes=p_value < alpha,
        alpha=alpha,
    )


def sessions_in(idx: pd.DatetimeIndex) -> list[tuple[int, int]]:
    """(start, end) positional index pairs, one per IST trading session, for building
    `RealisedTrade.session_start`/`session_end` from a bars frame's own index."""
    dates = idx.tz_convert("Asia/Kolkata").normalize() if idx.tz is not None else idx.normalize()
    _, first_at, counts = np.unique(dates.to_numpy(), return_index=True, return_counts=True)
    order = np.argsort(first_at)
    return [(int(first_at[i]), int(first_at[i] + counts[i] - 1)) for i in order]
