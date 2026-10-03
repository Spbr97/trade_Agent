from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from tradedesk.prediction.features import FEATURE_NAMES
from tradedesk.prediction.race import (
    AccuracyRaceProtocol,
    audit_accuracy_race_dataset,
    run_accuracy_race,
)
from tradedesk.prediction.selective import AccuracySelectorPolicy


def _dataset(n_sessions: int = 60, rows_per_session: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = n_sessions * rows_per_session
    frame = pd.DataFrame(rng.normal(size=(n, len(FEATURE_NAMES))), columns=FEATURE_NAMES)
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(n_sessions)]
    frame["armed_on"] = np.repeat(sessions, rows_per_session)
    frame["signal_id"] = [f"s{i}" for i in range(n)]
    probability = 1 / (1 + np.exp(-2 * frame[FEATURE_NAMES[0]].to_numpy()))
    frame["label"] = (rng.random(n) < probability).astype(int)
    frame["realised_r"] = np.where(frame["label"] == 1, 1.0, -1.0)
    frame["plain_score"] = np.clip(probability * 100, 0, 100)
    return frame


def test_readiness_fails_closed_on_missing_schema_and_economics() -> None:
    frame = _dataset().drop(columns=[FEATURE_NAMES[0]])
    frame["realised_r"] = np.nan
    audit = audit_accuracy_race_dataset(frame)
    assert audit["ready"] is False
    assert FEATURE_NAMES[0] in audit["missing_features"]
    assert any("economics" in reason for reason in audit["blockers"])
    result = run_accuracy_race(frame)
    assert result["status"] == "blocked"
    assert result["candidates"] == []


def test_race_uses_identical_chronological_population_and_never_forces_nominee() -> None:
    frame = _dataset()
    protocol = AccuracyRaceProtocol(n_splits=3, embargo_sessions=2, final_test_frac=0.2)
    impossible = AccuracySelectorPolicy(
        min_calls=10,
        min_active_sessions=5,
        min_session_coverage=0.1,
        min_observed_success=1.0,
        min_wilson_lower_bound=0.99,
        min_sessions_meeting_target=1.0,
    )
    result = run_accuracy_race(frame, protocol=protocol, policy=impossible)
    assert result["status"] == "abstain"
    assert result["nominee"] is None
    assert result["locked_test"]["status"] == "not_opened_no_nominee"
    assert {row["kind"] for row in result["candidates"]} == set(protocol.candidates)
    assert len({row["oos_rows"] for row in result["candidates"]}) == 1


def test_readiness_manifest_is_deterministic() -> None:
    frame = _dataset()
    first = audit_accuracy_race_dataset(frame)
    second = audit_accuracy_race_dataset(frame.copy())
    assert first["ready"] is True
    assert first["fingerprint_sha256"] == second["fingerprint_sha256"]


def test_race_can_use_an_explicit_audited_causal_feature_contract() -> None:
    frame = _dataset()
    causal = [FEATURE_NAMES[0], FEATURE_NAMES[1]]

    result = run_accuracy_race(
        frame,
        feature_names=causal,
        feature_version="causal-test-v1",
    )

    assert result["cohort"]["ready"] is True
    assert result["cohort"]["feature_version"] == "causal-test-v1"
    assert result["cohort"]["feature_names"] == causal
    assert [row["kind"] for row in result["candidates"]] == [
        "rule_score",
        "logistic",
        "hist_gradient_boosting",
    ]
