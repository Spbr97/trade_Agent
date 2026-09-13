"""Prediction layer (PLAN.md 10): meta-labeling on top of the rule-based setups.

Shadow by default; when switched on it can only lower a grade or halve a size."""

from tradedesk.prediction.calibration import DriftReport, check_and_flag_drift, drift_check
from tradedesk.prediction.features import FEATURE_NAMES, FEATURE_VERSION, signal_features
from tradedesk.prediction.labeling import Label, label_signal, triple_barrier
from tradedesk.prediction.predict import (
    apply_probability,
    latest_bundle,
    latest_bundles_by_setup,
    probability,
)
from tradedesk.prediction.train import (
    ModelBundle,
    TrainReport,
    build_dataset,
    compare_strategies,
    select_threshold,
    train,
    train_per_setup,
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
    "check_and_flag_drift",
    "compare_strategies",
    "drift_check",
    "label_signal",
    "latest_bundle",
    "latest_bundles_by_setup",
    "probability",
    "select_threshold",
    "signal_features",
    "train",
    "train_per_setup",
    "triple_barrier",
]
