"""Accuracy-first selective policy over chronologically out-of-sample predictions.

This module does not train a model and never turns a research probability into a live
call. It answers the narrower question: at each frozen probability threshold, and when
keeping at most the top one/two/three candidates per session, what accuracy, confidence,
coverage and economics were actually observed? Missing economics is a failed gate, never
a pass. A caller may nominate an operating point only when every policy requirement holds.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from tradedesk.engine.scoring import wilson_lower_bound


@dataclass(frozen=True)
class AccuracySelectorPolicy:
    """Research nomination gate; the stricter global live gate still applies afterwards."""

    min_calls: int = 100
    min_active_sessions: int = 30
    min_session_coverage: float = 0.40
    min_observed_success: float = 0.80
    min_wilson_lower_bound: float = 0.70
    session_success_target: float = 0.70
    min_sessions_meeting_target: float = 0.70
    min_expectancy_r: float = 0.0


DEFAULT_THRESHOLDS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))
DEFAULT_TOP_K = (1, 2, 3)
DEFAULT_SELECTOR_POLICY = AccuracySelectorPolicy()


def _validate_inputs(
    labels: np.ndarray,
    probabilities: np.ndarray,
    realised_r: np.ndarray,
    sessions: Sequence[Hashable],
) -> None:
    n = len(labels)
    if len(probabilities) != n or len(realised_r) != n or len(sessions) != n:
        raise ValueError("labels, probabilities, realised_r and sessions must have equal length")
    if np.any((labels != 0) & (labels != 1)):
        raise ValueError("labels must contain only 0/1")


def _select_top_k(
    probabilities: np.ndarray,
    sessions: Sequence[Hashable],
    threshold: float,
    top_k: int,
) -> np.ndarray:
    by_session: dict[Hashable, list[int]] = {}
    for i, (probability, session) in enumerate(zip(probabilities, sessions, strict=True)):
        if np.isfinite(probability) and probability >= threshold:
            by_session.setdefault(session, []).append(i)
    selected: list[int] = []
    for indices in by_session.values():
        indices.sort(key=lambda i: (-float(probabilities[i]), i))
        selected.extend(indices[:top_k])
    return np.asarray(sorted(selected), dtype=int)  # type: ignore[no-any-return]


def accuracy_coverage_curve(
    labels: np.ndarray,
    probabilities: np.ndarray,
    realised_r: np.ndarray,
    sessions: Sequence[Hashable],
    *,
    policy: AccuracySelectorPolicy | None = None,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    top_ks: Sequence[int] = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """Return every threshold/top-k operating point, including every failed gate."""
    labels = np.asarray(labels, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    realised_r = np.asarray(realised_r, dtype=float)
    _validate_inputs(labels, probabilities, realised_r, sessions)
    policy = policy or DEFAULT_SELECTOR_POLICY

    unique_sessions = set(sessions)
    total_sessions = len(unique_sessions)
    rows: list[dict[str, Any]] = []
    for top_k in top_ks:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        for threshold in thresholds:
            selected = _select_top_k(probabilities, sessions, float(threshold), int(top_k))
            n_selected = int(len(selected))
            wins = int(labels[selected].sum()) if n_selected else 0
            success_rate = wins / n_selected if n_selected else 0.0
            lower_bound = wilson_lower_bound(wins, n_selected)

            active = {sessions[i] for i in selected}
            per_session: dict[Hashable, list[int]] = {}
            for i in selected:
                per_session.setdefault(sessions[i], []).append(int(i))
            meeting = sum(
                float(labels[idx].mean()) >= policy.session_success_target
                for idx in per_session.values()
            )
            active_count = len(active)
            session_target_rate = meeting / active_count if active_count else 0.0
            session_coverage = active_count / total_sessions if total_sessions else 0.0

            economic = realised_r[selected] if n_selected else np.asarray([], dtype=float)
            n_economic = int(np.isfinite(economic).sum())
            economics_complete = n_economic == n_selected and n_selected > 0
            expectancy_r = float(economic.mean()) if economics_complete else None

            failures: list[str] = []
            if n_selected < policy.min_calls:
                failures.append(f"only {n_selected} selected calls, need {policy.min_calls}")
            if active_count < policy.min_active_sessions:
                failures.append(
                    f"only {active_count} active sessions, need {policy.min_active_sessions}"
                )
            if session_coverage < policy.min_session_coverage:
                failures.append(
                    f"session coverage {session_coverage:.1%} < "
                    f"required {policy.min_session_coverage:.1%}"
                )
            if success_rate < policy.min_observed_success:
                failures.append(
                    f"observed success {success_rate:.1%} < "
                    f"required {policy.min_observed_success:.1%}"
                )
            if lower_bound < policy.min_wilson_lower_bound:
                failures.append(
                    f"Wilson lower bound {lower_bound:.1%} < "
                    f"required {policy.min_wilson_lower_bound:.1%}"
                )
            if session_target_rate < policy.min_sessions_meeting_target:
                failures.append(
                    f"sessions meeting target {session_target_rate:.1%} < "
                    f"required {policy.min_sessions_meeting_target:.1%}"
                )
            if not economics_complete:
                failures.append(f"economics available for {n_economic}/{n_selected} selected calls")
            elif expectancy_r is not None and expectancy_r < policy.min_expectancy_r:
                failures.append(
                    f"expectancy {expectancy_r:+.3f}R < required "
                    f"{policy.min_expectancy_r:+.3f}R"
                )

            rows.append(
                {
                    "threshold": float(threshold),
                    "top_k": int(top_k),
                    "n_candidates": int(len(labels)),
                    "n_selected": n_selected,
                    "call_coverage": n_selected / len(labels) if len(labels) else 0.0,
                    "wins": wins,
                    "observed_success": success_rate,
                    "wilson_lower_bound": lower_bound,
                    "total_sessions": total_sessions,
                    "active_sessions": active_count,
                    "zero_call_sessions": total_sessions - active_count,
                    "session_coverage": session_coverage,
                    "sessions_meeting_target": meeting,
                    "session_target_rate": session_target_rate,
                    "n_with_economics": n_economic,
                    "expectancy_r": expectancy_r,
                    "qualified": not failures,
                    "failures": failures,
                }
            )
    return rows


def select_accuracy_operating_point(curve: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose maximum useful coverage among fully qualified development points."""
    qualified = [row for row in curve if row.get("qualified")]
    if not qualified:
        return None
    return max(
        qualified,
        key=lambda row: (
            float(row["session_coverage"]),
            int(row["n_selected"]),
            float(row["wilson_lower_bound"]),
            float(row["expectancy_r"]),
            -int(row["top_k"]),
            float(row["threshold"]),
        ),
    )
