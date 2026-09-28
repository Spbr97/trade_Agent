"""Milestone 3: the transparent, additive-evidence Precision Ladder selector.

This module only ever answers "what score would a fold-local model assign", never
"should a model act" - it implements section 5.4 of
``docs/plan-aem-v2-50pct-baseline.md`` (the scoring mechanics) and the hard-veto layer
from section 5.3. It contains no accuracy result: nothing here has been run against a
real dataset. That is Milestone 4's bounded development experiment.

Leakage guarantees, by construction rather than by convention:

- ``fit_feature_contributions`` and ``score_candidates`` reject any frame whose columns
  are not EXACTLY the 34 names frozen in ``aem_v2_contract.FEATURES`` - an outcome
  column (``net_r``, ``strict_success``, ...) or a stray extra column cannot silently
  ride along; it raises. Labels are passed as a separate array, never as a frame column,
  so there is no code path that could accidentally read a label out of the feature
  frame.
- ``fit_oof_calibration`` never scores a row with a model that saw that row's own label:
  it splits training rows into chronological inner folds and, for each fold, fits
  contributions on every OTHER inner fold before scoring the held-out fold. The
  isotonic calibrator is fit only on those genuinely out-of-fold scores.
- Every feature value this module ever sees was already computed by
  ``aem_v2_features.compute_features``, which can only see bars closed strictly before
  its own decision time (Milestone 2's guarantee). This module never touches a bar
  directly, so it cannot reintroduce a future-bar leak even by accident.
- Reliability shrinkage for sparse regimes is the per-bin additive-k smoothing in
  ``_fit_one_binning`` (a sparse bin's contribution shrinks toward the training base
  rate rather than an unstable extreme). Shrinkage for correlated same-session
  candidates is the symbol deduplication in ``select_calls`` - the same session's
  repeated signals on one symbol collapse to that symbol's single best-scoring
  candidate before ranking, rather than being treated as independent evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT, AemV2Geometry
from tradedesk_lab.aem_v2_events import DEFAULT_EVENT_ENGINE_CONTRACT, AemV2EventEngineContract

REGISTERED_FEATURE_NAMES = tuple(feature.name for feature in DEFAULT_AEM_V2_CONTRACT.features)

# A small preregistered, economically motivated set - not fitted, not tuned on results.
# Momentum agreeing with cross-sectional breadth, and price extension agreeing with a
# volume confirmation, are the only two interactions this milestone allows.
REGISTERED_INTERACTIONS: tuple[tuple[str, str], ...] = (
    ("return_5m", "cross_sectional_breadth"),
    ("vwap_distance", "volume_acceleration_3m"),
)

# Frozen, disclosed constants. These are hypotheses about reasonable bounds, not values
# fitted or tuned against any outcome.
BIN_COUNT = 5
SHRINKAGE_K = 20.0
MINIMUM_TRAIN_ROWS = 100
MINIMUM_INNER_FOLD_ROWS = 30
ABSOLUTE_QUALIFICATION_PROBABILITY = 0.50
MAX_IMPACT_PROXY = 0.25
MIN_MEDIAN_TURNOVER_INR = 5_000_000.0
MAX_EXTENSION_ATR = 3.0


def _logit(p: float) -> float:
    clipped = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(clipped / (1 - clipped))


def hard_veto_reasons(
    values: dict[str, float],
    *,
    mode: str,
    geometry: AemV2Geometry,
    event_contract: AemV2EventEngineContract = DEFAULT_EVENT_ENGINE_CONTRACT,
) -> tuple[str, ...]:
    """Section 5.3 vetoes computable purely from already-validated features/geometry.

    Portfolio, position, sector and heat limits are deliberately NOT reimplemented here
    - they already exist and are tested in ``risk/limits.py``. Callers pass a
    ``portfolio_filter`` to ``select_calls`` instead of this module re-deriving them.

    Two corrections made 27 September 2026, after the first real run against the full
    177,351-row development dataset showed 176,964/177,351 rows (99.8%) vetoed by
    ``entry_requires_excess_chase`` and 176,173/177,351 (99.3%) by
    ``no_remaining_causal_price_room`` - a combined 177,350/177,351 rows losing at least
    one, leaving exactly 1 row clean. Neither existing test caught this because both used
    hand-picked synthetic feature values injected directly, never a real
    ``compute_features`` output (the same class of gap the Milestone-2 tz bug slipped
    through in - see ``aem_v2_features.py``'s own history).

    1. ``entry_requires_excess_chase`` removed entirely. ``limit_distance`` is
       ``entry_limit / intended_entry - 1``, and ``aem_v2_events.py::opportunities_at``
       computes ``entry_limit`` as ``ceil_tick(intended_entry * (1 +
       event_contract.maximum_chase_pct))`` for EVERY opportunity, unconditionally. So
       ``limit_distance`` is mathematically pinned to ``maximum_chase_pct`` plus a small
       tick-rounding remainder for every row - comparing it back against the same
       ``maximum_chase_pct`` with strict ``>`` is a near-tautology that fires on
       virtually every row regardless of anything real about the opportunity. It added no
       discriminating signal and duplicated a check ``resolve_opportunity`` already makes
       correctly at fill time (its own ``chase_rejected`` outcome, using the REALIZED
       fill price, which is the only point this can actually be evaluated meaningfully).
    2. ``no_remaining_causal_price_room`` is now checked ONLY for ``anticipatory_impulse``.
       ``causal_level_distance`` measures distance to a BACKWARD-looking recent high -
       for ``anticipatory_impulse`` that is the literal entry thesis (price is still
       BELOW that level, anticipating a break through it, so a positive reading is
       exactly "room before the level"). For ``breakout_retest`` and
       ``confirmed_pullback``, the entry happens AFTER that same level has already been
       broken (both modes require a completed close above it before arming at all - see
       ``opportunities_at``), so the identical feature is structurally negative or
       near-zero for those two modes by construction, not because the opportunity lacks
       room to run. Applying an "anticipatory-only" gate to modes whose entire premise is
       the opposite (already broken out) was the bug; there is no currently-registered
       feature that correctly measures "room to the NEXT level beyond an already-broken
       one" for those two modes, so this veto simply does not apply to them rather than
       guessing at a replacement formula.
    """

    reasons = []
    if geometry.target_pct <= values["modeled_round_trip_cost_pct"] * 2:
        reasons.append("expected_move_too_small_for_costs")
    if values["impact_proxy"] > MAX_IMPACT_PROXY:
        reasons.append("excessive_impact_proxy")
    if values["median_turnover_20d"] < MIN_MEDIAN_TURNOVER_INR:
        reasons.append("inadequate_liquidity")
    if mode == "anticipatory_impulse" and values["causal_level_distance"] < geometry.target_pct:
        reasons.append("no_remaining_causal_price_room")
    if abs(values["daily_extension_atr"]) > MAX_EXTENSION_ATR:
        reasons.append("move_already_extended")
    if values["time_remaining_minutes"] <= 0:
        reasons.append("past_registered_deadline")
    return tuple(reasons)


@dataclass(frozen=True)
class FeatureBinning:
    interior_edges: tuple[float, ...]
    contributions: tuple[float, ...]


def _quantile_edges(values: np.ndarray, bins: int) -> np.ndarray:
    cut_points = np.linspace(0, 1, bins + 1)[1:-1]
    return np.unique(np.quantile(values, cut_points))


def _bin_indices(values: np.ndarray, interior_edges: np.ndarray) -> np.ndarray:
    return np.digitize(values, interior_edges, right=False)


def _fit_one_binning(values: np.ndarray, labels: np.ndarray, *, base_rate: float) -> FeatureBinning:
    interior_edges = _quantile_edges(values, BIN_COUNT)
    bin_ids = _bin_indices(values, interior_edges)
    n_bins = len(interior_edges) + 1
    base_logit = _logit(base_rate)
    contributions = np.zeros(n_bins)
    for b in range(n_bins):
        mask = bin_ids == b
        count = int(mask.sum())
        successes = float(labels[mask].sum()) if count else 0.0
        shrunk_rate = (successes + SHRINKAGE_K * base_rate) / (count + SHRINKAGE_K)
        contributions[b] = _logit(shrunk_rate) - base_logit
    return FeatureBinning(
        interior_edges=tuple(float(e) for e in interior_edges),
        contributions=tuple(float(c) for c in contributions),
    )


def _score_one_binning(values: np.ndarray, binning: FeatureBinning) -> np.ndarray:
    edges = np.array(binning.interior_edges)
    bin_ids = _bin_indices(values, edges)
    return np.array(binning.contributions)[bin_ids]


def _require_registered_columns(frame: pd.DataFrame) -> None:
    if set(frame.columns) != set(REGISTERED_FEATURE_NAMES):
        raise ValueError(
            "frame columns must equal exactly the frozen feature registry - no outcome "
            "column and no extra column is permitted"
        )


def _require_binary_labels(labels: np.ndarray, n: int) -> np.ndarray:
    labels = np.asarray(labels)
    if labels.shape != (n,) or not set(np.unique(labels)).issubset({0, 1}):
        raise ValueError("labels must be a binary array matching the frame length")
    return labels.astype(float)


@dataclass(frozen=True)
class FittedContributions:
    base_rate: float
    intercept: float
    feature_bins: dict[str, FeatureBinning] = field(default_factory=dict)
    interaction_bins: dict[tuple[str, str], FeatureBinning] = field(default_factory=dict)
    train_rows: int = 0


def fit_feature_contributions(frame: pd.DataFrame, labels: np.ndarray) -> FittedContributions:
    """Fit additive evidence contributions on a training frame only.

    ``frame`` must contain exactly the 34 registered feature columns; ``labels`` is a
    separate 0/1 array the same length as ``frame`` and is never read from the frame.
    """

    _require_registered_columns(frame)
    if len(frame) < MINIMUM_TRAIN_ROWS:
        raise ValueError("insufficient_training_rows")
    labels = _require_binary_labels(labels, len(frame))
    base_rate = float(labels.mean())
    feature_bins = {
        name: _fit_one_binning(frame[name].to_numpy(dtype=float), labels, base_rate=base_rate)
        for name in REGISTERED_FEATURE_NAMES
    }
    interaction_bins = {}
    for a, b in REGISTERED_INTERACTIONS:
        product = frame[a].to_numpy(dtype=float) * frame[b].to_numpy(dtype=float)
        interaction_bins[(a, b)] = _fit_one_binning(product, labels, base_rate=base_rate)
    return FittedContributions(
        base_rate=base_rate,
        intercept=_logit(base_rate),
        feature_bins=feature_bins,
        interaction_bins=interaction_bins,
        train_rows=len(frame),
    )


def score_candidates(fitted: FittedContributions, frame: pd.DataFrame) -> np.ndarray:
    """Apply an already-fitted model to any frame. Never fits on ``frame`` itself."""

    _require_registered_columns(frame)
    score = np.full(len(frame), fitted.intercept, dtype=float)
    for name, binning in fitted.feature_bins.items():
        score += _score_one_binning(frame[name].to_numpy(dtype=float), binning)
    for (a, b), binning in fitted.interaction_bins.items():
        product = frame[a].to_numpy(dtype=float) * frame[b].to_numpy(dtype=float)
        score += _score_one_binning(product, binning)
    return score


@dataclass(frozen=True)
class OofCalibrationResult:
    fitted: FittedContributions
    calibrator: Any
    oof_scores: np.ndarray
    fold_of_row: np.ndarray


def fit_oof_calibration(
    frame: pd.DataFrame,
    labels: np.ndarray,
    session_order: np.ndarray,
    *,
    n_inner_folds: int = 3,
) -> OofCalibrationResult:
    """Chronological nested calibration: a row is never scored by a model fit on it.

    ``session_order`` is any sortable per-row key (e.g. the session date) used only to
    assign rows to chronological inner folds; it is never itself a feature.
    """

    if n_inner_folds < 2:
        raise ValueError("at least two inner folds are required")
    _require_registered_columns(frame)
    labels = _require_binary_labels(labels, len(frame))
    order = np.argsort(np.asarray(session_order), kind="stable")
    fold_of_row = np.empty(len(frame), dtype=int)
    for fold_id, indices in enumerate(np.array_split(order, n_inner_folds)):
        fold_of_row[indices] = fold_id
    oof_scores = np.empty(len(frame), dtype=float)
    for fold_id in range(n_inner_folds):
        test_mask = fold_of_row == fold_id
        train_mask = ~test_mask
        if int(train_mask.sum()) < MINIMUM_INNER_FOLD_ROWS or not test_mask.any():
            raise ValueError("insufficient_rows_for_inner_fold")
        inner_fitted = fit_feature_contributions(frame.loc[train_mask], labels[train_mask])
        oof_scores[test_mask] = score_candidates(inner_fitted, frame.loc[test_mask])
    calibrator = _fit_isotonic(oof_scores, labels)
    final_fitted = fit_feature_contributions(frame, labels)
    return OofCalibrationResult(
        fitted=final_fitted, calibrator=calibrator, oof_scores=oof_scores, fold_of_row=fold_of_row
    )


def _fit_isotonic(scores: np.ndarray, labels: np.ndarray) -> Any:
    from sklearn.isotonic import IsotonicRegression

    model = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
    model.fit(scores, labels)
    return model


def calibrated_probability(calibrator: Any, scores: np.ndarray) -> np.ndarray:
    return np.asarray(calibrator.predict(scores))


def fit_logistic_control(frame: pd.DataFrame, labels: np.ndarray, *, seed: int = 20260101) -> Any:
    """The transparent regularized-logistic control the plan requires alongside the
    custom selector - same folds, same feature columns, no custom evidence weighting.
    """

    _require_registered_columns(frame)
    labels = _require_binary_labels(labels, len(frame))
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    classifier = LogisticRegression(C=0.5, max_iter=1000, random_state=seed)
    model = make_pipeline(StandardScaler(), classifier)
    model.fit(frame[list(REGISTERED_FEATURE_NAMES)].to_numpy(dtype=float), labels)
    return model


def score_logistic_control(model: Any, frame: pd.DataFrame) -> np.ndarray:
    _require_registered_columns(frame)
    matrix = frame[list(REGISTERED_FEATURE_NAMES)].to_numpy(dtype=float)
    return np.asarray(model.predict_proba(matrix)[:, 1])


@dataclass(frozen=True)
class ScoredCandidate:
    identifier: str
    session: str
    scrip_code: str
    calibrated_probability: float
    veto_reasons: tuple[str, ...] = ()

    @property
    def qualifies(self) -> bool:
        if self.veto_reasons:
            return False
        return self.calibrated_probability > ABSOLUTE_QUALIFICATION_PROBABILITY


def select_calls(
    candidates: list[ScoredCandidate],
    *,
    top_k: int,
    portfolio_filter: Callable[[ScoredCandidate], bool] | None = None,
) -> tuple[str, ...]:
    """Threshold, then rank, then deduplicate, then cap - never the reverse order.

    A candidate that fails ``qualifies`` (a hard veto or a sub-threshold calibrated
    probability) can never be selected no matter how it would have ranked - the
    qualifying filter runs strictly before ranking and the top-k cap.
    """

    if top_k not in DEFAULT_AEM_V2_CONTRACT.top_k_policies:
        raise ValueError("top_k must be one of the frozen top-k policies")
    by_session: dict[str, list[ScoredCandidate]] = {}
    for candidate in candidates:
        by_session.setdefault(candidate.session, []).append(candidate)
    selected: list[str] = []
    for group in by_session.values():
        qualifying = [c for c in group if c.qualifies]
        if portfolio_filter is not None:
            qualifying = [c for c in qualifying if portfolio_filter(c)]
        best_per_symbol: dict[str, ScoredCandidate] = {}
        for candidate in qualifying:
            current = best_per_symbol.get(candidate.scrip_code)
            if current is None or candidate.calibrated_probability > current.calibrated_probability:
                best_per_symbol[candidate.scrip_code] = candidate
        ranked = sorted(
            best_per_symbol.values(), key=lambda c: c.calibrated_probability, reverse=True
        )
        selected.extend(c.identifier for c in ranked[:top_k])
    return tuple(selected)
