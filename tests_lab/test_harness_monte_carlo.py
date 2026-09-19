import pytest
from tradedesk_lab.harness.monte_carlo import monte_carlo_trade_reorder


def test_raises_on_empty_trade_list():
    with pytest.raises(ValueError, match="no trades"):
        monte_carlo_trade_reorder([])


def test_all_identical_r_reproduces_the_same_drawdown_every_reshuffle():
    # Order can't matter when every trade has the same R - a losing R multiple reshuffled
    # any way still produces the identical monotonic equity curve and drawdown.
    result = monte_carlo_trade_reorder(
        [-0.5] * 20, n_cohorts=50, seed=1, starting_capital=100_000.0, risk_per_trade_pct=0.01
    )
    assert result.max_drawdown_pct["p50"] == pytest.approx(result.max_drawdown_pct["p99"])
    assert result.losing_streak["p50"] == 20  # every trade loses -> the whole run is one streak


def test_all_winning_trades_never_draw_down():
    result = monte_carlo_trade_reorder([0.5] * 10, n_cohorts=30, seed=2)
    assert result.max_drawdown_pct["p99"] == 0.0
    assert result.losing_streak["p99"] == 0


def test_reproducible_with_the_same_seed():
    a = monte_carlo_trade_reorder([1.0, -1.0, 0.5, -0.5], n_cohorts=100, seed=7)
    b = monte_carlo_trade_reorder([1.0, -1.0, 0.5, -0.5], n_cohorts=100, seed=7)
    assert a.max_drawdown_pct == b.max_drawdown_pct
    assert a.losing_streak == b.losing_streak
