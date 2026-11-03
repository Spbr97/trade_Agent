"""Inference-only reader for frozen bundles when sklearn's compiled runtime is blocked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib.numpy_pickle as numpy_pickle
import numpy as np
import pandas as pd


class StateBag:
    """Data-only destination for trusted local sklearn/tradedesk pickle state."""

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        else:
            self.__dict__["state"] = state


class PortableUnpickler(numpy_pickle.NumpyUnpickler):
    def find_class(self, module: str, name: str) -> Any:
        if module.startswith("sklearn") or module == "tradedesk_lab.models":
            return StateBag
        return super().find_class(module, name)


def _impute(values: np.ndarray, imputer: StateBag) -> np.ndarray:
    output = values.astype(float, copy=True)
    missing = ~np.isfinite(output)
    if missing.any():
        output[missing] = np.take(np.asarray(imputer.statistics_, dtype=float), np.where(missing)[1])
    return output


def _sigmoid(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, -700, 700)
    return 1 / (1 + np.exp(-value))


class PortableBundle:
    def __init__(self, state: StateBag):
        self.family = state.family
        self.features = list(state.features)
        self.parameters = state.parameters
        self.training_end = state.training_end
        self._model = state.model
        self._calibrator = state.calibrator

    def _raw(self, values: np.ndarray) -> np.ndarray:
        if self.family == "logistic":
            steps = dict(self._model.steps)
            values = _impute(values, steps["simpleimputer"])
            scaler = steps["standardscaler"]
            values = (values - np.asarray(scaler.mean_)) / np.asarray(scaler.scale_)
            model = steps["logisticregression"]
            linear = values @ np.asarray(model.coef_, dtype=float).reshape(-1)
            linear += float(np.asarray(model.intercept_).reshape(-1)[0])
            return _sigmoid(linear)
        values = _impute(values, self._model.imputer)
        return np.asarray(self._model.model.predict_proba(values), dtype=float)[:, 1]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        values = frame[self.features].to_numpy(dtype=float)
        raw = np.clip(self._raw(values), 1e-6, 1 - 1e-6)
        logits = np.log(raw / (1 - raw))
        coefficient = float(np.asarray(self._calibrator.coef_).reshape(-1)[0])
        intercept = float(np.asarray(self._calibrator.intercept_).reshape(-1)[0])
        return _sigmoid(logits * coefficient + intercept)


def load_portable(path: Path) -> PortableBundle:
    """Load only a trusted local lab artifact; never accept an external pickle here."""
    with path.open("rb") as stream:
        state = PortableUnpickler(str(path), stream, ensure_native_byte_order=True).load()
    return PortableBundle(state)
