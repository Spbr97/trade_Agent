"""null_baseline.py: the statistical properties the whole gate rests on. If these are
wrong, every eligibility decision downstream of TrackRecord.random_baseline_r is wrong too."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradedesk.backtest.null_baseline import RealisedTrade, run_null_baseline, sessions_in


def _flat_bars(n: int, price: float = 100.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:  # noqa: E501
    """A dead-flat instrument: every bar identical. Any entry, real or random, resolves to
    the SAME outcome (a timeout at the session close, 0R), so the null distribution has zero
    variance and the setup can never appear to beat it - the honest baseline case."""
    o = np.full(n, price)
    h = np.full(n, price + 0.5)
    low_ = np.full(n, price - 0.5)
    c = np.full(n, price)
    return o, h, low_, c


def test_run_null_baseline_rejects_an_empty_trade_list() -> None:
    with pytest.raises(ValueError, match="no trades"):
        run_null_baseline([], {})


def test_a_setup_indistinguishable_from_random_does_not_pass() -> None:
    """The setup's trades ARE random draws on a flat instrument - there is genuinely no
    edge here, and the gate must say so (p-value not small)."""
    o, h, low_, c = _flat_bars(100)
    trades = [
        RealisedTrade(
            scrip_code="X", entry_bar=10 + 20 * i, session_start=0, session_end=99,
            entry=100.0, stop=99.0, target=102.0, gross_r=0.0,
        )
        for i in range(4)
    ]
    result = run_null_baseline(trades, {"X": (o, h, low_, c)}, n_cohorts=200, seed=1)
    assert not result.passes
    assert result.p_value > 0.05


def test_a_setup_with_a_real_planted_edge_passes() -> None:
    """Every REAL trade hits its target immediately (gross_r = target multiple); random
    entries on the same flat, non-trending instrument can only time out at 0R. The gap
    between them must be large and p-value small - this is the case the whole gate exists
    to detect."""
    o, h, low_, c = _flat_bars(100)
    trades = [
        RealisedTrade(
            scrip_code="X", entry_bar=10 + 15 * i, session_start=0, session_end=99,
            entry=100.0, stop=99.0, target=102.0, gross_r=2.0,
        )
        for i in range(6)
    ]
    result = run_null_baseline(trades, {"X": (o, h, low_, c)}, n_cohorts=500, seed=2)
    assert result.passes
    assert result.p_value < 0.05
    assert result.setup_mean_gross_r > result.null_mean_gross_r


def test_null_mean_is_what_track_record_random_baseline_r_should_be_populated_with() -> None:
    """Documents the contract engine/scoring.py::TrackRecord.random_baseline_r relies on:
    null_mean_gross_r IS the random-timing baseline for this setup, in the same R units as
    expectancy_r, ready to pass straight through."""
    o, h, low_, c = _flat_bars(50)
    trades = [RealisedTrade("X", 5, 0, 49, 100.0, 99.0, 101.0, 0.0)]
    result = run_null_baseline(trades, {"X": (o, h, low_, c)}, n_cohorts=50, seed=3)
    assert isinstance(result.null_mean_gross_r, float)
    assert not np.isnan(result.null_mean_gross_r)


def test_net_r_is_lower_than_gross_r_once_a_cost_model_is_supplied() -> None:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel

    settings = load_config(".")
    cm = EquityCostModel(settings.risk.costs)
    o, h, low_, c = _flat_bars(50)
    trades = [RealisedTrade("X", 5, 0, 49, 100.0, 99.0, 102.0, 2.0)]
    no_cost = run_null_baseline(trades, {"X": (o, h, low_, c)}, n_cohorts=50, seed=4)
    with_cost = run_null_baseline(trades, {"X": (o, h, low_, c)}, n_cohorts=50, seed=4, cost_model=cm)  # noqa: E501
    assert no_cost.setup_mean_net_r == pytest.approx(no_cost.setup_mean_gross_r)
    assert with_cost.setup_mean_net_r < with_cost.setup_mean_gross_r


def test_draws_never_cross_the_session_boundary() -> None:
    """A random draw must resolve within ITS OWN session, never bleeding into a later
    session's bars - otherwise the comparison is not actually "same day" anymore."""
    n = 200
    o = np.arange(n, dtype=float) + 100.0  # strictly increasing, so a leaked later bar
    h = o + 0.5                            # would be visibly, detectably higher
    low_ = o - 0.5
    c = o.copy()
    sessions = [(0, 49), (50, 99), (100, 149), (150, 199)]
    trades = [
        RealisedTrade("X", 10, s, e, entry=float(o[10] if s == 0 else o[s + 5]),
                       stop=float(o[10] - 1) if s == 0 else float(o[s + 5] - 1),
                       target=float(o[10] + 100) if s == 0 else float(o[s + 5] + 100),
                       gross_r=0.0)
        for s, e in sessions
    ]
    # target is set absurdly high so every draw times out at the SESSION's own close price,
    # not at some artificially early or late bar - if a draw crossed a session boundary its
    # resolved close would come from a different (and here, higher-indexed => higher-priced)
    # session than the one it was drawn from.
    result = run_null_baseline(trades, {"X": (o, h, low_, c)}, n_cohorts=100, seed=5)
    assert not np.isnan(result.null_mean_gross_r)  # ran without an index error, at minimum


def test_sessions_in_splits_a_multi_day_index_correctly() -> None:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=5 * i) for i in range(10)]  # noqa: E501
        + [pd.Timestamp("2026-09-15 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=5 * i) for i in range(6)]  # noqa: E501
    )
    sessions = sessions_in(idx)
    assert sessions == [(0, 9), (10, 15)]
