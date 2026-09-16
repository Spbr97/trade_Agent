"""M15: optional model families with time-purged, held-out calibration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from tradedesk_lab.validation import purge


@dataclass
class ImputedTree:
    """Keep optional tree libraries independent of sklearn's estimator-tag ABI."""

    model: Any
    imputer: Any = None

    def fit(self, x: pd.DataFrame, y: pd.Series) -> ImputedTree:
        self.imputer = SimpleImputer()
        self.model.fit(self.imputer.fit_transform(x), y)
        return self

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self.imputer.transform(x))


def grids(seed: int = 14) -> tuple[dict[str, list[dict]], dict[str, str]]:
    result = {"logistic": [{"C": c} for c in (0.1, 1.0)]}
    missing = {}
    for name, module in (
        ("xgboost", "xgboost"),
        ("catboost", "catboost"),
        ("lightgbm", "lightgbm"),
    ):
        try:
            __import__(module)
            result[name] = [{"depth": depth, "iterations": 100} for depth in (3, 5)]
        except Exception as exc:
            missing[name] = f"unavailable: {type(exc).__name__}"
    return result, missing


def estimator(family: str, hp: dict, seed: int = 14) -> Any:
    if family == "logistic":
        return make_pipeline(
            SimpleImputer(),
            StandardScaler(),
            LogisticRegression(C=hp["C"], max_iter=1500, random_state=seed),
        )
    if family == "xgboost":
        from xgboost import XGBClassifier

        model = XGBClassifier(
            max_depth=hp["depth"],
            n_estimators=hp["iterations"],
            learning_rate=0.04,
            n_jobs=2,
            random_state=seed,
            eval_metric="logloss",
            min_child_weight=5,
        )
    elif family == "catboost":
        from catboost import CatBoostClassifier

        model = CatBoostClassifier(
            depth=hp["depth"],
            iterations=hp["iterations"],
            learning_rate=0.04,
            thread_count=2,
            random_seed=seed,
            allow_writing_files=False,
            verbose=False,
        )
    elif family == "lightgbm":
        from lightgbm import LGBMClassifier

        model = LGBMClassifier(
            max_depth=hp["depth"],
            n_estimators=hp["iterations"],
            learning_rate=0.04,
            n_jobs=2,
            random_state=seed,
            verbosity=-1,
        )
    elif family == "extratrees":
        from sklearn.ensemble import ExtraTreesClassifier

        model = ExtraTreesClassifier(
            n_estimators=hp["iterations"],
            max_depth=hp["depth"],
            min_samples_leaf=20,
            max_features=0.75,
            n_jobs=2,
            random_state=seed,
        )
    else:
        raise ValueError(f"Unknown model family: {family}")
    return ImputedTree(model)


@dataclass
class Bundle:
    family: str
    parameters: dict
    features: list[str]
    model: Any
    calibrator: Any
    training_end: str

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(df[self.features])[:, 1]
        logits = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / np.clip(1 - raw, 1e-6, 1))
        return self.calibrator.predict_proba(logits.reshape(-1, 1))[:, 1]


def fit(
    family: str,
    hp: dict,
    df: pd.DataFrame,
    features: list[str],
    calendar: pd.DatetimeIndex,
    seed: int = 14,
) -> Bundle:
    dates = np.sort(pd.to_datetime(df.armed_on).unique())
    if len(dates) < 20:
        raise ValueError("Too few dates for temporal calibration")
    split = dates[int(len(dates) * 0.8)]
    calibration = np.flatnonzero(pd.to_datetime(df.armed_on) >= split)
    train = np.flatnonzero(pd.to_datetime(df.armed_on) < split)
    train = purge(df, train, calibration, calendar)
    if (
        len(train) < 30
        or min(df.iloc[train].label.nunique(), df.iloc[calibration].label.nunique()) < 2
    ):
        raise ValueError("Insufficient independent training/calibration classes")
    model = estimator(family, hp, seed)
    model.fit(df.iloc[train][features], df.iloc[train].label)
    raw = np.clip(model.predict_proba(df.iloc[calibration][features])[:, 1], 1e-6, 1 - 1e-6)
    calibrator = LogisticRegression(C=1.0, random_state=seed)
    calibrator.fit(np.log(raw / (1 - raw)).reshape(-1, 1), df.iloc[calibration].label)
    return Bundle(family, hp, features, model, calibrator, str(df.armed_on.max()))


def agreement(probabilities: dict[str, np.ndarray], spread_limit: float = 0.10) -> dict:
    if len(probabilities) < 2:
        raise ValueError("Agreement requires at least two independently fitted model families")
    values = np.column_stack(list(probabilities.values()))
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("Probabilities must be finite and in [0,1]")
    spread = np.ptp(values, axis=1)
    return {
        "probabilities": probabilities,
        "mean": values.mean(axis=1),
        "spread": spread,
        "agree": spread <= spread_limit,
        "limit": spread_limit,
    }
