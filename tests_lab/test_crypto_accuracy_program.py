from __future__ import annotations

import json
from pathlib import Path

from tradedesk_lab.crypto_accuracy_program import build_crypto_accuracy_state


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_crypto_baseline_never_pools_live_and_backfill(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    universe = tmp_path / "universe.json"
    _write(
        log,
        [
            {
                "source": "live",
                "setup": "a",
                "armed_on": "2026-10-01",
                "outcome": "target",
                "label": 1,
                "r_multiple": 0.5,
                "rejected_for": [],
            },
            {
                "source": "live",
                "setup": "a",
                "armed_on": "2026-10-02",
                "outcome": "stop",
                "label": 0,
                "r_multiple": -1.0,
                "rejected_for": ["shadow"],
            },
            {
                "source": "backfill",
                "setup": "a",
                "armed_on": "2025-01-01",
                "outcome": "target",
                "label": 1,
                "r_multiple": 0.5,
                "rejected_for": [],
            },
        ],
    )
    universe.write_text('{"active_inr_pairs": 3, "scanned_pairs": 3}', encoding="utf-8")

    state = build_crypto_accuracy_state(log, universe)

    assert state["source_integrity"]["evidence_mixed"] is False
    assert state["live_forward"]["resolved_calls"] == 2
    assert state["live_forward"]["accuracy"] == 0.5
    assert state["historical_backfill"]["resolved_calls"] == 1
    assert state["historical_backfill"]["accuracy"] == 1.0
    assert state["qualification"]["eligible_for_live"] is False


def test_missing_source_is_backfill_not_forward_evidence(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _write(
        log,
        [{"setup": "old", "armed_on": "2020-01-01", "outcome": "stop", "label": 0}],
    )

    state = build_crypto_accuracy_state(log, tmp_path / "missing.json")

    assert state["live_forward"]["calls"] == 0
    assert state["historical_backfill"]["calls"] == 1
    assert state["live_forward"]["accuracy"] is None
