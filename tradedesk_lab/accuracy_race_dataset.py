"""Auditable inputs for the accuracy race.

This adapter deliberately uses the clean dataset's point-in-time feature subset and
reconstructed after-cost outcomes.  It does not fill unknown legacy fields with zeros or
pretend that the old production export satisfies the current feature contract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradedesk_lab.clean_dataset import FEATURES
from tradedesk_lab.dataset import Dataset

FEATURE_VERSION = "accuracy-causal-arming-v1"
RULE_SCORE_VERSION = "transparent-long-quality-v1"


def _scale(values: pd.Series, low: float, high: float) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return np.clip((numeric - low) / (high - low), 0.0, 1.0)


def transparent_rule_score(frame: pd.DataFrame) -> np.ndarray:
    """Frozen 0-100 long-setup quality score using decision-time causal inputs only.

    This is a comparator, not a learned probability.  Its fixed components cover trend
    strength/direction, non-overextended structure, momentum and volatility quality.  The
    model candidates must beat it on the exact same rows and chronological folds.
    """
    required = {
        "adx14",
        "di_spread",
        "dist_ema20_atr",
        "dist_high20_atr",
        "rsi14",
        "roc20",
        "atr_pct_rank",
        "range_contraction",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"transparent score missing inputs: {', '.join(missing)}")

    trend = 0.55 * _scale(frame["adx14"], 15.0, 40.0) + 0.45 * _scale(
        frame["di_spread"], -10.0, 25.0
    )
    distance = pd.to_numeric(frame["dist_ema20_atr"], errors="coerce").to_numpy(dtype=float)
    structure = np.clip(1.0 - np.abs(distance - 1.0) / 4.0, 0.0, 1.0)
    structure = 0.6 * structure + 0.4 * _scale(frame["dist_high20_atr"], -3.0, 0.0)
    momentum = 0.55 * _scale(frame["rsi14"], 40.0, 75.0) + 0.45 * _scale(
        frame["roc20"], -10.0, 20.0
    )
    volatility = 0.5 * (1.0 - _scale(frame["atr_pct_rank"], 70.0, 100.0))
    volatility += 0.5 * (1.0 - _scale(frame["range_contraction"], 0.7, 1.5))
    score = 100.0 * (0.35 * trend + 0.30 * structure + 0.20 * momentum + 0.15 * volatility)
    return np.round(np.clip(score, 0.0, 100.0), 6)


def build_accuracy_race_frame(dataset: Dataset) -> pd.DataFrame:
    """Map the clean cohort to the race contract without mutating its frozen artifact."""
    frame = dataset.frame.copy()
    missing = sorted(set(FEATURES) - set(frame.columns))
    if missing:
        raise ValueError(f"clean dataset missing causal features: {', '.join(missing)}")
    if "net_r" not in frame:
        raise ValueError("clean dataset has no reconstructed after-cost net_r")
    frame["realised_r"] = pd.to_numeric(frame["net_r"], errors="coerce")
    frame["plain_score"] = transparent_rule_score(frame)
    keep = [
        "signal_id",
        "scrip_code",
        "setup",
        "armed_on",
        "entry_date",
        "label",
        "outcome",
        "realised_r",
        "plain_score",
        *FEATURES,
    ]
    return frame[keep].sort_values(["armed_on", "signal_id"]).reset_index(drop=True)
