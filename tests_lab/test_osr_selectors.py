import numpy as np
import pandas as pd
import pytest

from tradedesk_lab.osr_selectors import (
    FEATURE_NAMES,
    calibrated_probabilities,
    fit_platt_calibrator,
    fit_regularized_tree,
    fit_transparent_additive,
    score_regularized_tree,
    score_transparent_additive,
    selector_registry,
)


def _training_frame(rows: int = 300, seed: int = 11) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(rows, len(FEATURE_NAMES)))
    labels = (matrix[:, 0] + 0.8 * matrix[:, 1] > 0).astype(int)
    return pd.DataFrame(matrix, columns=FEATURE_NAMES), labels


def test_selector_registry_is_frozen_and_uses_only_the_30_features():
    registry = selector_registry()
    assert registry["feature_names"] == list(FEATURE_NAMES)
    assert len(registry["feature_names"]) == 30
    assert registry["selector_kinds"] == ["transparent_additive", "regularized_tree"]
    assert registry["qualification_probability"] == 0.50
    assert registry["regularized_tree"]["maximum_depth"] == 1


def test_feature_frames_reject_extra_missing_or_reordered_columns():
    frame, labels = _training_frame()
    with pytest.raises(ValueError, match="ordered OSR feature registry"):
        fit_transparent_additive(frame.assign(label=labels), labels)
    with pytest.raises(ValueError, match="ordered OSR feature registry"):
        fit_transparent_additive(frame[list(reversed(FEATURE_NAMES))], labels)


def test_labels_are_separate_binary_values_and_both_classes_are_required():
    frame, labels = _training_frame()
    with pytest.raises(ValueError, match="binary vector"):
        fit_regularized_tree(frame, np.full(len(frame), 2))
    with pytest.raises(ValueError, match="both classes"):
        fit_regularized_tree(frame, np.zeros(len(frame), dtype=int))
    with pytest.raises(ValueError, match="matching the frame"):
        fit_regularized_tree(frame, labels[:-1])


def test_transparent_additive_and_tree_rank_the_known_signal():
    frame, labels = _training_frame()
    additive = fit_transparent_additive(frame, labels)
    tree = fit_regularized_tree(frame, labels)
    additive_scores = score_transparent_additive(additive, frame)
    tree_scores = score_regularized_tree(tree, frame)
    assert additive_scores[labels == 1].mean() > additive_scores[labels == 0].mean()
    assert tree_scores[labels == 1].mean() > tree_scores[labels == 0].mean()
    assert tree.stumps


def test_platt_calibration_is_regularized_monotonic_and_finite():
    scores = np.linspace(-3, 3, 200)
    labels = (scores > 0.25).astype(int)
    calibrator = fit_platt_calibrator(scores, labels)
    probabilities = calibrated_probabilities(calibrator, scores)
    assert calibrator.slope >= 0
    assert np.isfinite(probabilities).all()
    assert (np.diff(probabilities) >= 0).all()
    assert probabilities.min() >= 0
    assert probabilities.max() <= 1


def test_calibration_rejects_tiny_or_single_class_samples():
    with pytest.raises(ValueError, match="insufficient causal calibration rows"):
        fit_platt_calibrator(np.arange(10), np.tile([0, 1], 5))
    with pytest.raises(ValueError, match="both classes"):
        fit_platt_calibrator(np.arange(50), np.zeros(50))
