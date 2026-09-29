"""Frozen, dependency-light selectors for the OSR Milestone-3 bounded race.

Both models accept exactly the 30 decision-time OSR features.  Labels are always a
separate binary array.  The transparent model is an additive collection of smoothed
univariate evidence bins; the nonlinear challenger is a heavily regularized ensemble
of shallow logistic decision stumps implemented with NumPy so the research checkpoint
does not depend on a newly installed or blocked binary wheel.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT

FEATURE_NAMES = tuple(feature.name for feature in DEFAULT_OSR_CONTRACT.features)
SELECTOR_KINDS = ("transparent_additive", "regularized_tree")

ADDITIVE_BIN_COUNT = 4
ADDITIVE_SHRINKAGE_K = 40.0
ADDITIVE_MAX_ABS_CONTRIBUTION = 0.75

TREE_ROUNDS = 24
TREE_LEARNING_RATE = 0.08
TREE_L2 = 20.0
TREE_THRESHOLD_COUNT = 8
TREE_MIN_LEAF_ROWS = 30
TREE_MAX_ABS_LEAF_VALUE = 1.0

PLATT_L2 = 5.0
PLATT_MAX_ITERATIONS = 60
MINIMUM_TRAIN_ROWS = 60
MINIMUM_CALIBRATION_ROWS = 40
QUALIFICATION_PROBABILITY = 0.50


def selector_registry() -> dict[str, Any]:
    """The fixed model/threshold registry frozen before the real race is run."""

    return {
        "feature_names": list(FEATURE_NAMES),
        "selector_kinds": list(SELECTOR_KINDS),
        "qualification_probability": QUALIFICATION_PROBABILITY,
        "minimum_train_rows": MINIMUM_TRAIN_ROWS,
        "minimum_calibration_rows": MINIMUM_CALIBRATION_ROWS,
        "transparent_additive": {
            "bin_count": ADDITIVE_BIN_COUNT,
            "shrinkage_k": ADDITIVE_SHRINKAGE_K,
            "maximum_absolute_feature_contribution": ADDITIVE_MAX_ABS_CONTRIBUTION,
        },
        "regularized_tree": {
            "rounds": TREE_ROUNDS,
            "learning_rate": TREE_LEARNING_RATE,
            "l2": TREE_L2,
            "threshold_count": TREE_THRESHOLD_COUNT,
            "minimum_leaf_rows": TREE_MIN_LEAF_ROWS,
            "maximum_absolute_leaf_value": TREE_MAX_ABS_LEAF_VALUE,
            "maximum_depth": 1,
        },
        "calibration": {
            "method": "causal_inner_walk_forward_platt",
            "l2": PLATT_L2,
            "maximum_iterations": PLATT_MAX_ITERATIONS,
            "nonnegative_slope": True,
        },
    }


def _logit(probability: float) -> float:
    value = min(max(float(probability), 1e-6), 1 - 1e-6)
    return math.log(value / (1 - value))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def require_feature_frame(frame: pd.DataFrame) -> np.ndarray:
    if tuple(frame.columns) != FEATURE_NAMES:
        raise ValueError(
            "selector frame columns must equal the frozen ordered OSR feature registry"
        )
    matrix = frame.to_numpy(dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_NAMES):
        raise ValueError("selector feature matrix has an invalid shape")
    if not np.isfinite(matrix).all():
        raise ValueError("selector features must be finite")
    return matrix


def require_binary_labels(labels: np.ndarray, rows: int) -> np.ndarray:
    values = np.asarray(labels, dtype=float)
    if values.shape != (rows,) or not set(np.unique(values)).issubset({0.0, 1.0}):
        raise ValueError("selector labels must be a binary vector matching the frame")
    if len(values) < MINIMUM_TRAIN_ROWS:
        raise ValueError("insufficient selector training rows")
    if len(np.unique(values)) != 2:
        raise ValueError("selector training labels must contain both classes")
    return values


@dataclass(frozen=True)
class EvidenceBin:
    interior_edges: tuple[float, ...]
    contributions: tuple[float, ...]


@dataclass(frozen=True)
class TransparentAdditiveModel:
    intercept: float
    bins: tuple[EvidenceBin, ...]
    train_rows: int


def _fit_evidence_bin(values: np.ndarray, labels: np.ndarray, base_rate: float) -> EvidenceBin:
    quantiles = np.linspace(0.0, 1.0, ADDITIVE_BIN_COUNT + 1)[1:-1]
    edges = np.unique(np.quantile(values, quantiles))
    assignments = np.digitize(values, edges, right=False)
    contributions = []
    base_logit = _logit(base_rate)
    for bin_id in range(len(edges) + 1):
        mask = assignments == bin_id
        count = int(mask.sum())
        successes = float(labels[mask].sum()) if count else 0.0
        rate = (successes + ADDITIVE_SHRINKAGE_K * base_rate) / (
            count + ADDITIVE_SHRINKAGE_K
        )
        contribution = _logit(rate) - base_logit
        contributions.append(
            float(
                np.clip(
                    contribution,
                    -ADDITIVE_MAX_ABS_CONTRIBUTION,
                    ADDITIVE_MAX_ABS_CONTRIBUTION,
                )
            )
        )
    return EvidenceBin(
        interior_edges=tuple(float(value) for value in edges),
        contributions=tuple(contributions),
    )


def fit_transparent_additive(
    frame: pd.DataFrame, labels: np.ndarray
) -> TransparentAdditiveModel:
    matrix = require_feature_frame(frame)
    labels = require_binary_labels(labels, len(frame))
    base_rate = float(labels.mean())
    bins = tuple(
        _fit_evidence_bin(matrix[:, column], labels, base_rate)
        for column in range(matrix.shape[1])
    )
    return TransparentAdditiveModel(
        intercept=_logit(base_rate),
        bins=bins,
        train_rows=len(frame),
    )


def score_transparent_additive(
    model: TransparentAdditiveModel, frame: pd.DataFrame
) -> np.ndarray:
    matrix = require_feature_frame(frame)
    scores = np.full(len(frame), model.intercept, dtype=float)
    scale = 1.0 / math.sqrt(len(FEATURE_NAMES))
    for column, fitted in enumerate(model.bins):
        edges = np.asarray(fitted.interior_edges, dtype=float)
        assignments = np.digitize(matrix[:, column], edges, right=False)
        scores += np.asarray(fitted.contributions, dtype=float)[assignments] * scale
    return scores


@dataclass(frozen=True)
class DecisionStump:
    feature_index: int
    threshold: float
    left_value: float
    right_value: float


@dataclass(frozen=True)
class RegularizedTreeModel:
    intercept: float
    stumps: tuple[DecisionStump, ...]
    train_rows: int


def fit_regularized_tree(frame: pd.DataFrame, labels: np.ndarray) -> RegularizedTreeModel:
    matrix = require_feature_frame(frame)
    labels = require_binary_labels(labels, len(frame))
    base_rate = float(labels.mean())
    scores = np.full(len(frame), _logit(base_rate), dtype=float)
    stumps: list[DecisionStump] = []
    quantiles = np.linspace(0.0, 1.0, TREE_THRESHOLD_COUNT + 2)[1:-1]

    for _round in range(TREE_ROUNDS):
        probabilities = _sigmoid(scores)
        gradients = labels - probabilities
        hessians = probabilities * (1.0 - probabilities)
        total_gradient = float(gradients.sum())
        total_hessian = float(hessians.sum())
        parent_gain = total_gradient**2 / (total_hessian + TREE_L2)
        best: tuple[float, int, float, np.ndarray] | None = None

        for feature_index in range(matrix.shape[1]):
            values = matrix[:, feature_index]
            for threshold in np.unique(np.quantile(values, quantiles)):
                left = values <= threshold
                left_count = int(left.sum())
                right_count = len(values) - left_count
                if left_count < TREE_MIN_LEAF_ROWS or right_count < TREE_MIN_LEAF_ROWS:
                    continue
                left_gradient = float(gradients[left].sum())
                left_hessian = float(hessians[left].sum())
                right_gradient = total_gradient - left_gradient
                right_hessian = total_hessian - left_hessian
                gain = (
                    left_gradient**2 / (left_hessian + TREE_L2)
                    + right_gradient**2 / (right_hessian + TREE_L2)
                    - parent_gain
                )
                if best is None or gain > best[0] + 1e-12:
                    best = (float(gain), feature_index, float(threshold), left)

        if best is None or best[0] <= 1e-12:
            break
        _gain, feature_index, threshold, left = best
        left_gradient = float(gradients[left].sum())
        left_hessian = float(hessians[left].sum())
        right = ~left
        right_gradient = float(gradients[right].sum())
        right_hessian = float(hessians[right].sum())
        left_value = float(
            np.clip(
                left_gradient / (left_hessian + TREE_L2),
                -TREE_MAX_ABS_LEAF_VALUE,
                TREE_MAX_ABS_LEAF_VALUE,
            )
        )
        right_value = float(
            np.clip(
                right_gradient / (right_hessian + TREE_L2),
                -TREE_MAX_ABS_LEAF_VALUE,
                TREE_MAX_ABS_LEAF_VALUE,
            )
        )
        stump = DecisionStump(feature_index, threshold, left_value, right_value)
        stumps.append(stump)
        scores += TREE_LEARNING_RATE * np.where(left, left_value, right_value)

    return RegularizedTreeModel(
        intercept=_logit(base_rate),
        stumps=tuple(stumps),
        train_rows=len(frame),
    )


def score_regularized_tree(model: RegularizedTreeModel, frame: pd.DataFrame) -> np.ndarray:
    matrix = require_feature_frame(frame)
    scores = np.full(len(frame), model.intercept, dtype=float)
    for stump in model.stumps:
        left = matrix[:, stump.feature_index] <= stump.threshold
        scores += TREE_LEARNING_RATE * np.where(left, stump.left_value, stump.right_value)
    return scores


def fit_selector(kind: str, frame: pd.DataFrame, labels: np.ndarray) -> Any:
    if kind == "transparent_additive":
        return fit_transparent_additive(frame, labels)
    if kind == "regularized_tree":
        return fit_regularized_tree(frame, labels)
    raise ValueError(f"unregistered OSR selector kind: {kind}")


def score_selector(kind: str, model: Any, frame: pd.DataFrame) -> np.ndarray:
    if kind == "transparent_additive":
        return score_transparent_additive(model, frame)
    if kind == "regularized_tree":
        return score_regularized_tree(model, frame)
    raise ValueError(f"unregistered OSR selector kind: {kind}")


@dataclass(frozen=True)
class PlattCalibrator:
    score_mean: float
    score_scale: float
    slope: float
    intercept: float
    calibration_rows: int


def fit_platt_calibrator(scores: np.ndarray, labels: np.ndarray) -> PlattCalibrator:
    values = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=float)
    if values.ndim != 1 or labels.shape != values.shape:
        raise ValueError("calibration scores and labels must be matching vectors")
    if len(values) < MINIMUM_CALIBRATION_ROWS:
        raise ValueError("insufficient causal calibration rows")
    if not np.isfinite(values).all() or not set(np.unique(labels)).issubset({0.0, 1.0}):
        raise ValueError("calibration inputs must be finite with binary labels")
    if len(np.unique(labels)) != 2:
        raise ValueError("calibration labels must contain both classes")

    mean = float(values.mean())
    scale = float(values.std())
    if not math.isfinite(scale) or scale < 1e-12:
        scale = 1.0
    standardized = (values - mean) / scale
    slope = 0.0
    intercept = _logit(float(labels.mean()))

    for _iteration in range(PLATT_MAX_ITERATIONS):
        logits = slope * standardized + intercept
        probabilities = _sigmoid(logits)
        residual = probabilities - labels
        weights = probabilities * (1.0 - probabilities)
        gradient = np.array(
            [float((residual * standardized).sum()) + PLATT_L2 * slope, residual.sum()]
        )
        hessian = np.array(
            [
                [float((weights * standardized**2).sum()) + PLATT_L2, float((weights * standardized).sum())],
                [float((weights * standardized).sum()), float(weights.sum()) + 1e-8],
            ]
        )
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as exc:
            raise ValueError("calibration Hessian is singular") from exc
        next_slope = max(0.0, float(slope - step[0]))
        next_intercept = float(intercept - step[1])
        if max(abs(next_slope - slope), abs(next_intercept - intercept)) < 1e-9:
            slope, intercept = next_slope, next_intercept
            break
        slope, intercept = next_slope, next_intercept

    return PlattCalibrator(mean, scale, slope, intercept, len(values))


def calibrated_probabilities(calibrator: PlattCalibrator, scores: np.ndarray) -> np.ndarray:
    values = np.asarray(scores, dtype=float)
    standardized = (values - calibrator.score_mean) / calibrator.score_scale
    return _sigmoid(calibrator.slope * standardized + calibrator.intercept)


def model_summary(model: Any) -> dict[str, Any]:
    """Return stable diagnostics without serializing the fitted model itself."""

    if isinstance(model, TransparentAdditiveModel):
        return {
            "kind": "transparent_additive",
            "train_rows": model.train_rows,
            "feature_bins": len(model.bins),
        }
    if isinstance(model, RegularizedTreeModel):
        used = sorted({FEATURE_NAMES[stump.feature_index] for stump in model.stumps})
        return {
            "kind": "regularized_tree",
            "train_rows": model.train_rows,
            "stumps": len(model.stumps),
            "features_used": used,
        }
    if isinstance(model, PlattCalibrator):
        return asdict(model)
    raise ValueError("unsupported OSR fitted model summary")
