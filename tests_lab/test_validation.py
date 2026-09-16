from __future__ import annotations

from itertools import combinations
from math import erf, sqrt

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.validation import (
    cpcv,
    deflated_sharpe,
    probability_of_overfitting,
    purge,
    reconstruct,
    walk_forward,
)


def sample(n=180):
    calendar = pd.bdate_range("2020-01-01", periods=n + 20)
    return pd.DataFrame({"armed_on": calendar[:n], "label_end_date": calendar[5 : n + 5]}), calendar


def test_label_purge_and_post_test_embargo():
    df, calendar = sample()
    test = np.arange(50, 60)
    train = np.setdiff1d(np.arange(len(df)), test)
    kept = purge(df, train, test, calendar, embargo=10)
    assert not np.intersect1d(kept, np.arange(45, 75)).size
    assert 44 in kept and 75 in kept


def test_cpcv_has_fifteen_folds_five_complete_paths():
    df, calendar = sample()
    folds, path_map, ids = cpcv(df, calendar)
    assert len(folds) == 15 and path_map.shape == (6, 5)
    predictions = []
    for i, fold in enumerate(folds):
        assert not np.intersect1d(fold.train, fold.test).size
        p = np.full(len(df), np.nan)
        p[fold.test] = i
        predictions.append(p)
        for tr in fold.train:
            assert not (
                (df.iloc[fold.test].armed_on <= df.iloc[tr].label_end_date)
                & (df.iloc[fold.test].label_end_date >= df.iloc[tr].armed_on)
            ).any()
    paths = reconstruct(predictions, path_map, ids)
    assert paths.shape == (180, 5) and np.isfinite(paths).all()
    for group in range(6):
        assert len(set(path_map[group])) == 5
    for a, b in combinations(range(5), 2):
        assert not np.array_equal(paths[:, a], paths[:, b])


def test_walkforward_never_trains_on_future_or_overlapping_labels():
    df, calendar = sample(250)
    for fold in walk_forward(df, calendar):
        assert df.iloc[fold.train].label_end_date.max() < df.iloc[fold.test].armed_on.min()


def test_dsr_reference_and_multiple_testing_penalty():
    # Zero benchmark, Gaussian skew/kurtosis: z = .1 * sqrt(100) / sqrt(1 + .1**2/2).
    expected = (1 + erf((1 / sqrt(1.005)) / sqrt(2))) / 2
    assert deflated_sharpe(0.1, 0.05, 1, 101, 0, 3) == pytest.approx(expected, abs=1e-6)
    assert deflated_sharpe(0.1, 0.05, 100, 101, 0, 3) < deflated_sharpe(0.1, 0.05, 5, 101, 0, 3)
    assert deflated_sharpe(0, 0, 1, 101, 0, 3) == 0.5
    assert deflated_sharpe(0.1, 0.05, 1, 2, 0, 3) is None


def test_pbo_stable_skill_and_noise():
    rng = np.random.default_rng(7)
    stable = rng.normal(0, 1, (1200, 5)) + np.arange(5) * 2
    assert probability_of_overfitting(stable)["pbo"] == 0
    # Average across independent noise experiments, not one noisy finite estimate.
    values = [probability_of_overfitting(rng.normal(0, 1, (240, 9)))["pbo"] for _ in range(80)]
    assert np.mean(values) == pytest.approx(0.5, abs=0.10)
    with pytest.raises(ValueError):
        probability_of_overfitting(stable, partitions=5)
