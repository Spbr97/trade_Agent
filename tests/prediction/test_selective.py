from __future__ import annotations

import numpy as np

from tradedesk.prediction.selective import (
    AccuracySelectorPolicy,
    accuracy_coverage_curve,
    select_accuracy_operating_point,
)


def test_top_one_policy_can_qualify_while_top_two_fails() -> None:
    sessions = np.repeat(np.arange(40), 3)
    probabilities = np.tile([0.95, 0.90, 0.20], 40)
    labels = np.tile([1, 0, 0], 40)
    labels[np.arange(36, 40) * 3] = 0  # top-one: 36/40 = 90%
    realised_r = np.where(labels == 1, 0.5, -1.0)
    policy = AccuracySelectorPolicy(min_calls=30, min_active_sessions=30)

    curve = accuracy_coverage_curve(
        labels, probabilities, realised_r, sessions,
        policy=policy, thresholds=[0.80], top_ks=[1, 2],
    )
    top_one, top_two = curve

    assert top_one["qualified"] is True
    assert top_one["observed_success"] == 0.90
    assert top_one["wilson_lower_bound"] >= 0.70
    assert top_one["session_coverage"] == 1.0
    assert top_two["qualified"] is False
    assert top_two["observed_success"] == 0.45
    assert select_accuracy_operating_point(curve) == top_one


def test_small_lucky_sample_fails_wilson_and_sample_gates() -> None:
    labels = np.ones(10, dtype=int)
    probabilities = np.full(10, 0.99)
    realised_r = np.full(10, 0.2)
    curve = accuracy_coverage_curve(
        labels, probabilities, realised_r, list(range(10)),
        thresholds=[0.95], top_ks=[1],
    )
    row = curve[0]
    assert row["observed_success"] == 1.0
    assert row["qualified"] is False
    assert any("selected calls" in reason for reason in row["failures"])
    assert any("active sessions" in reason for reason in row["failures"])
    assert select_accuracy_operating_point(curve) is None


def test_missing_economics_never_passes() -> None:
    n = 40
    policy = AccuracySelectorPolicy(
        min_calls=30, min_active_sessions=30, min_wilson_lower_bound=0.60,
    )
    curve = accuracy_coverage_curve(
        np.ones(n, dtype=int), np.full(n, 0.95), np.full(n, np.nan), list(range(n)),
        policy=policy, thresholds=[0.90], top_ks=[1],
    )
    row = curve[0]
    assert row["qualified"] is False
    assert row["expectancy_r"] is None
    assert "economics available for 0/40 selected calls" in row["failures"]


def test_mismatched_inputs_are_rejected() -> None:
    with np.testing.assert_raises_regex(ValueError, "equal length"):
        accuracy_coverage_curve(
            np.ones(2, dtype=int), np.ones(1), np.ones(2), ["a", "b"]
        )
