"""Helpers behind the pre-registered literature checks (tradedesk_lab/literature_checks.py)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from tradedesk_lab.literature_checks import (
    _turnover20,
    clustered_t,
    information_discreteness,
    intraday_momentum_day,
    passes,
    predicted_date,
    residual_score,
)


def test_predicted_date_uses_only_last_years_date():
    hist = [100, 465, 830]
    assert predicted_date(hist, 830) == 465 + 365
    assert predicted_date(hist, 100) is None  # nothing a year earlier
    assert predicted_date([100, 300], 300) is None  # 200 days back is not "a year earlier"


def test_information_discreteness_rewards_many_small_up_days():
    smooth = np.full(200, 0.001)  # every day up a little
    jumpy = np.r_[np.full(190, -0.0005), np.full(10, 0.03)]  # mostly down, a few jumps
    assert information_discreteness(smooth, 0.2) == pytest.approx(-1.0)
    assert information_discreteness(jumpy, 0.2) > 0.5


def test_residual_score_strips_the_market_without_an_intercept():
    rng = np.random.default_rng(0)
    mkt = rng.normal(0.0005, 0.01, 250)
    beta_only = 1.5 * mkt  # all of its return is the market's
    skill = 1.5 * mkt + 0.002 + rng.normal(0, 0.002, 250)  # plus a steady excess
    assert abs(residual_score(beta_only, mkt)) < 1e-6 or math.isnan(residual_score(beta_only, mkt))
    assert residual_score(skill, mkt) > 3


def test_intraday_day_follows_the_morning_direction_and_pays_costs():
    up = intraday_momentum_day(100, 101, 50, 50.5, cost=0.0008)
    down = intraday_momentum_day(100, 99, 50, 50.5, cost=0.0008)
    assert up == pytest.approx(0.01 - 0.0008)
    assert down == pytest.approx(-0.01 - 0.0008)


def test_clustered_t_counts_each_day_once():
    mean, t, n = clustered_t([1.0, 1.0, 1.0, 0.0, 2.0, 1.0], ["a", "a", "a", "b", "c", "d"])
    assert n == 4 and mean == pytest.approx(1.0)
    assert passes((0.1, 3.5), [0.05, 0.02], t_bar=3.0)
    assert not passes((0.1, 3.5), [0.05, -0.01], t_bar=3.0)


def test_turnover_uses_only_prior_sessions():
    close = np.full(30, 10.0)
    vol = np.r_[np.full(29, 100.0), 1e9]  # a huge spike on the last day
    out = _turnover20(close, vol, np.array([0, 30]))
    assert out[29] == pytest.approx(1000.0)  # the spike day itself is not counted
    assert np.isnan(out[19]) and out[20] == pytest.approx(1000.0)
