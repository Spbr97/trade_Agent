from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import IST
from tradedesk.intraday_contract_race import _aggregate_frame, _frame_payload
from tradedesk.selection_readiness import (
    _aggregate_frame_fast,
    _block_metrics,
    _frame_payload_fast,
    _partition_sessions,
    _readiness_gates,
    load_selection_readiness_status,
)


def _sessions(count: int = 72) -> list[str]:
    return [f"2026-01-{index + 1:02d}" for index in range(count)]


def test_partition_is_frozen_and_chronological() -> None:
    sessions = _sessions()
    split = _partition_sessions(list(reversed(sessions)))

    assert [len(value) for value in split.values()] == [42, 3, 12, 3, 12]
    assert split["development_train"] == sessions[:42]
    assert split["internal_diagnostic"] == sessions[-12:]

    with pytest.raises(ValueError, match="exactly 72 sessions"):
        _partition_sessions(sessions[:-1])


def test_readiness_gates_never_treat_missing_evidence_as_pass() -> None:
    blocks = {
        "development_train": {
            "valid_resolved_coverage": 0.90,
            "resolved_sessions": 40,
            "model_eligible_rows": 320,
            "positive_labels": 45,
            "negative_labels": 275,
            "feature_invalid_rows": 0,
        },
        "calibration": {
            "valid_resolved_coverage": 0.90,
            "resolved_sessions": 11,
            "model_eligible_rows": 85,
            "positive_labels": 13,
            "negative_labels": 72,
            "feature_invalid_rows": 0,
        },
        "internal_diagnostic": {
            "valid_resolved_coverage": None,
            "resolved_sessions": 0,
            "model_eligible_rows": 0,
            "positive_labels": 0,
            "negative_labels": 0,
            "feature_invalid_rows": 0,
        },
    }

    gates = _readiness_gates(blocks)

    assert len(gates) == 18
    assert all(
        gate["passed"] is False
        for gate in gates
        if gate["block"] == "internal_diagnostic" and gate["gate"] != "finite_features"
    )
    assert sum(gate["passed"] is True for gate in gates) < len(gates)


def test_block_metrics_excludes_purge_and_invalid_from_model_rows() -> None:
    rows = [
        {
            "split": "calibration",
            "session": "2026-01-01",
            "replay_status": "resolved",
            "model_eligible": True,
            "precision_label": True,
            "feature_status": "valid",
        },
        {
            "split": "calibration",
            "session": "2026-01-02",
            "replay_status": "resolved",
            "model_eligible": False,
            "precision_label": None,
            "feature_status": "invalid:return_1",
        },
        {
            "split": "calibration",
            "session": "2026-01-03",
            "replay_status": "invalid_or_unavailable",
            "model_eligible": False,
            "precision_label": None,
            "feature_status": "valid",
        },
    ]

    metrics = _block_metrics(rows, "calibration")

    assert metrics["candidate_rows"] == 3
    assert metrics["valid_resolved_paths"] == 2
    assert metrics["model_eligible_rows"] == 1
    assert metrics["positive_labels"] == 1
    assert metrics["negative_labels"] == 0
    assert metrics["feature_invalid_rows"] == 1


def test_status_is_fail_closed_before_manifest(tmp_path: Path) -> None:
    status = load_selection_readiness_status("crypto", output_root=tmp_path)

    assert status["status"] == "not_started"
    assert status["blockers"] == ["manifest_not_sealed"]
    assert status["eligible_for_live"] is False
    assert status["active_model_changed"] is False
    assert status["baseline_accuracy_improved"] is False


def test_fast_path_materialization_matches_frozen_m17_helpers() -> None:
    index = pd.date_range("2026-01-05 09:15", periods=15, freq="1min", tz=IST)
    frame = pd.DataFrame(
        {
            "open": [100.0 + index / 10 for index in range(15)],
            "high": [100.5 + index / 10 for index in range(15)],
            "low": [99.5 + index / 10 for index in range(15)],
            "close": [100.2 + index / 10 for index in range(15)],
            "volume": [1000 + index for index in range(15)],
        },
        index=index,
    )

    assert _frame_payload_fast(frame) == _frame_payload(frame)
    pd.testing.assert_frame_equal(
        _aggregate_frame_fast(frame, 5, start=index[0].to_pydatetime()),
        _aggregate_frame(frame, 5, start=index[0].to_pydatetime()),
    )
