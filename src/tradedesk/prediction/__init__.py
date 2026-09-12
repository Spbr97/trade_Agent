"""Prediction layer (PLAN.md 10): meta-labeling on top of the rule-based setups.

Shadow by default; when switched on it can only lower a grade or halve a size."""

from tradedesk.prediction.calibration import DriftReport, drift_check
from tradedesk.prediction.features import FEATURE_NAMES, FEATURE_VERSION, signal_features
from tradedesk.prediction.labeling import Label, label_signal, triple_barrier
from tradedesk.prediction.predict import apply_probability, latest_bundle, probability
from tradedesk.prediction.train import (
    ModelBundle,
    TrainReport,
    build_dataset,
    compare_strategies,
    select_threshold,
    train,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "DriftReport",
    "Label",
    "ModelBundle",
    "TrainReport",
    "apply_probability",
    "build_dataset",
    "compare_strategies",
    "drift_check",
    "label_signal",
    "latest_bundle",
    "probability",
    "select_threshold",
    "signal_features",
    "train",
    "triple_barrier",
]
