"""The prospective freeze must ignore last-place float noise at any magnitude, keep rejecting
real revisions, and carry freezes written under the old 10-decimal rule forward without
losing their original fingerprint (2026-10-18: the crypto audit failed every day from the
00:00 UTC candle because CDX_DOGEINR's avg_turnover_20 moved from 7202109.513440001 to
7202109.51344)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from tests.unit.test_leader_discovery import _frame, _tracker
from tradedesk import leader_discovery as ld
from tradedesk.broker.indstocks.models import IST
from tradedesk.prediction_ledger import canonical_sha256

SEALED = 7202109.513440001
DRIFTED = 7202109.51344
META = {
    "db_path": "test.duckdb", "latest_closed_session": "2026-10-13",
    "query_history_start": "2025-05-06", "mature_session_start": "2026-10-10",
    "mature_session_end": "2026-10-10", "mature_sessions": 1, "raw_rows_considered": 3,
}  # fmt: skip


def _setup(tmp_path: Path, monkeypatch) -> tuple[pd.DataFrame, object, Path]:
    current = _frame(date(2026, 10, 10))
    current["avg_turnover_20"] = [SEALED, 50_000_000.0, 9_000_000.0]
    monkeypatch.setattr(ld, "extract_causal_universe", lambda *a, **k: (current.copy(), META))
    tracker = tmp_path / "tracker.jsonl"
    tracker.write_text(
        json.dumps(_tracker()[0] | {"armed_on": "2026-10-10"}) + "\n", encoding="utf-8"
    )

    def run(hour: int) -> dict:
        return ld.run_missed_leader_audit(
            market="nse", db_path=tmp_path / "test.duckdb", tracker_path=tracker,
            output_root=tmp_path / "audit", now=datetime(2026, 10, 14, hour, tzinfo=IST),
            session_count=1,
        )  # fmt: skip

    return current, run, tmp_path / "audit" / "nse" / "sessions" / "2026-10-10.json"


def test_the_legacy_rule_really_did_reject_this_noise() -> None:
    assert round(SEALED, 10) != round(DRIFTED, 10)
    assert ld._stable_floats(SEALED) == ld._stable_floats(DRIFTED)


def test_new_freezes_ignore_last_place_noise_on_large_values(tmp_path: Path, monkeypatch) -> None:
    current, run, path = _setup(tmp_path, monkeypatch)
    run(12)
    assert json.loads(path.read_text())["float_rounding"] == ld.FREEZE_ROUNDING
    current.loc[0, "avg_turnover_20"] = DRIFTED
    assert len(run(13)["prospective_session_freezes"]) == 1
    current.loc[0, "avg_turnover_20"] = DRIFTED + 1.0  # a real revision of one rupee
    with pytest.raises(ValueError, match="changed at immutable field causal_rows_sha256"):
        run(14)


def _make_legacy(path: Path, monkeypatch, run) -> dict:
    """Write the first freeze exactly as the pre-fix code did: 10-decimal hash, no rounding
    marker, then restore the current code."""

    original = ld._rows_hash
    with monkeypatch.context() as patch:
        patch.setattr(ld, "_rows_hash", lambda rows, legacy=False: original(rows, legacy=True))
        run(12)
    frozen = json.loads(path.read_text())
    frozen.pop("float_rounding")
    frozen.pop("artifact_sha256")
    frozen["artifact_sha256"] = canonical_sha256(frozen)
    path.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return frozen


def test_a_legacy_freeze_is_verified_then_carried_forward(tmp_path: Path, monkeypatch) -> None:
    current, run, path = _setup(tmp_path, monkeypatch)
    legacy = _make_legacy(path, monkeypatch, run)

    run(13)  # unchanged rows: verifies under the old rule, then records the new fingerprint
    migrated = json.loads(path.read_text())
    assert migrated["causal_rows_sha256"] == legacy["causal_rows_sha256"]  # original kept
    assert migrated["float_rounding"] == ld.FREEZE_ROUNDING
    assert migrated[ld.SIGNIFICANT_HASH_FIELD]
    body = {k: v for k, v in migrated.items() if k != "artifact_sha256"}
    assert migrated["artifact_sha256"] == canonical_sha256(body)

    current.loc[0, "avg_turnover_20"] = DRIFTED  # the noise that used to fail every day
    assert len(run(14)["prospective_session_freezes"]) == 1
    current.loc[0, "return_5"] = 9.9  # a real revision still fails
    with pytest.raises(ValueError, match="changed at immutable field causal_rows_sha256"):
        run(15)


def test_a_legacy_freeze_that_fails_its_own_rule_is_left_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    current, run, path = _setup(tmp_path, monkeypatch)
    _make_legacy(path, monkeypatch, run)
    before = path.read_text()
    current.loc[0, "avg_turnover_20"] = DRIFTED  # cannot be verified under the old rule
    with pytest.raises(ValueError, match="changed at immutable field causal_rows_sha256"):
        run(13)
    assert path.read_text() == before  # never re-sealed from unverified rows
