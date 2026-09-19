"""New Sharpe/CAGR/log-return machinery added for the ML prediction-layer strategy
comparison (prediction/train.py::compare_strategies). The log_returns test locks in a real
bug found running this on live NSE data: a lumpy trade-based equity curve's arithmetic
pct_change() mean is spuriously positive on a flat round trip (Jensen's inequality), which
produced a NEGATIVE-CAGR strategy reporting a POSITIVE Sharpe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from tradedesk.backtest.reports import (
    cagr,
    equity_curve_from_trades,
    log_returns,
    longest_losing_streak,
    longest_winning_streak,
    sharpe_ratio,
)


@dataclass
class _FakeTrade:
    exit_date: date
    net_pnl: float


def test_log_returns_of_a_flat_round_trip_average_to_zero() -> None:
    """+9% then back down to the exact starting level: pct_change's arithmetic mean is
    spuriously positive (+9%, -8.257...% -> mean +0.37%), but log returns net to exactly
    zero, matching the true "no growth" outcome."""
    equity = pd.Series([100_000.0, 109_000.0, 100_000.0])
    naive_mean = equity.pct_change().dropna().mean()
    assert naive_mean > 0.003  # the artifact: arithmetic mean says "grew"

    lr = log_returns(equity).dropna()
    assert abs(lr.sum()) < 1e-9  # log returns are additive: a round trip nets to exactly 0
    assert abs(lr.mean()) < 1e-9


def test_sharpe_and_cagr_agree_in_sign_on_a_losing_equity_curve() -> None:
    """The real bug: Strategy A's actual backtest reported Sharpe +1.4 alongside CAGR
    -16% - a losing strategy should not show a positive risk-adjusted return once the
    Jensen's-inequality artifact is fixed."""
    trades = [
        _FakeTrade(date(2024, 1, 2), 9_000.0),
        _FakeTrade(date(2024, 1, 3), -11_000.0),
        _FakeTrade(date(2024, 1, 4), -8_000.0),
    ]
    calendar = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    eq = equity_curve_from_trades(trades, calendar, starting_capital=100_000.0)
    assert eq.iloc[-1] < eq.iloc[0]  # this cohort lost money overall

    c = cagr(eq, calendar[0], calendar[-1])
    s = sharpe_ratio(log_returns(eq).dropna())
    assert c < 0
    assert s <= 0  # must agree in sign with CAGR, not the pct_change artifact's false positive


def test_sharpe_ratio_zero_on_flat_or_short_series() -> None:
    assert sharpe_ratio(pd.Series([0.0])) == 0.0
    assert sharpe_ratio(pd.Series([0.0, 0.0, 0.0])) == 0.0


def test_cagr_zero_on_empty_or_nonpositive_start() -> None:
    assert cagr(pd.Series(dtype=float), date(2024, 1, 1), date(2024, 12, 31)) == 0.0
    assert cagr(pd.Series([0.0, 100.0]), date(2024, 1, 1), date(2024, 12, 31)) == 0.0


def test_longest_streak_counts_consecutive_runs_in_given_order() -> None:
    results = [1.0, 2.0, -1.0, -0.5, -2.0, 3.0, -0.1, -0.2]
    assert longest_losing_streak(results) == 3  # the -1.0/-0.5/-2.0 run
    assert longest_winning_streak(results) == 2  # the 1.0/2.0 run
    assert longest_losing_streak([]) == 0
    assert longest_losing_streak([1.0, 2.0]) == 0
    assert longest_winning_streak([-1.0]) == 0


def test_longest_streak_treats_exact_zero_as_a_loss() -> None:
    # r_multiple == 0 is a scratch, not a win - matches metrics()'s own `r > 0` win test.
    assert longest_losing_streak([0.0, 0.0, 1.0]) == 2


def test_equity_curve_from_trades_forward_fills_between_exits() -> None:
    trades = [_FakeTrade(date(2024, 1, 3), 1_000.0)]
    calendar = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    eq = equity_curve_from_trades(trades, calendar, starting_capital=10_000.0)
    assert eq.loc[date(2024, 1, 1)] == 10_000.0
    assert eq.loc[date(2024, 1, 2)] == 10_000.0  # unchanged before the exit
    assert eq.loc[date(2024, 1, 3)] == 11_000.0
    assert eq.loc[date(2024, 1, 4)] == 11_000.0  # forward-filled after the exit
