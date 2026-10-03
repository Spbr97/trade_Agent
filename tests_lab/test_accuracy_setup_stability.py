from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from tradedesk_lab.accuracy_setup_stability import (
    DEFAULT_SETUP_STABILITY_PROTOCOL,
    global_walk_forward_masks,
    outcome_summary,
    stability_failures,
)


def test_protocol_freezes_primary_precedence_and_unchanged_accuracy_target() -> None:
    protocol = DEFAULT_SETUP_STABILITY_PROTOCOL
    assert protocol.hypotheses[0].name == "primary_trend_pullback"
    assert protocol.hypotheses[1].name == "secondary_base_breakout"
    assert protocol.selector_thresholds == (
        0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90
    )
    assert len(protocol.sha256) == 64


def test_global_walk_forward_has_embargo_and_nonoverlapping_tests() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=i) for i in range(50)]
    rows = pd.Series(sessions * 2)
    folds = global_walk_forward_masks(rows, sessions, n_folds=4, embargo_sessions=3)
    assert len(folds) == 4
    seen: set[int] = set()
    for train, test, start, _ in folds:
        assert not (set(test) & seen)
        seen.update(test)
        train_dates = rows.iloc[train]
        assert max(train_dates) <= sessions[sessions.index(start) - 4]


def test_stability_gate_is_not_the_promotion_gate() -> None:
    protocol = DEFAULT_SETUP_STABILITY_PROTOCOL
    aggregate = {
        "n": 1000,
        "accuracy": 0.78,
        "wilson_lower_bound": 0.74,
        "expectancy_r": 0.02,
    }
    folds = [
        {"accuracy": 0.76, "expectancy_r": 0.01},
        {"accuracy": 0.77, "expectancy_r": 0.02},
        {"accuracy": 0.78, "expectancy_r": 0.01},
        {"accuracy": 0.79, "expectancy_r": -0.01},
    ]
    assert stability_failures(aggregate, folds, protocol) == []


def test_outcome_summary_preserves_session_reliability() -> None:
    frame = pd.DataFrame(
        {
            "armed_on": [date(2026, 1, 1)] * 2 + [date(2026, 1, 2)] * 2,
            "label": [1, 1, 1, 0],
            "net_r": [0.4, 0.4, 0.4, -1.0],
        }
    )
    summary = outcome_summary(frame)
    assert summary["accuracy"] == 0.75
    assert summary["sessions_meeting_target"] == 1
    assert summary["session_target_rate"] == 0.5
