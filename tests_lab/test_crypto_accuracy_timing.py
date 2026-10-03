from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb
import pandas as pd
import pytest
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.crypto_accuracy_timing import (
    MAX_OFFSET,
    MIN_OFFSET,
    N_COHORTS,
    _load_crypto_bars,
    _new_record,
    _resolve_timing,
    _verify_record,
    freeze_offsets,
    summarize_timing,
)

from tradedesk.config import load_config
from tradedesk.markets import crypto_market


def _bars(periods: int = 80) -> pd.DataFrame:
    stamps = pd.date_range("2025-12-01 05:30", periods=periods, freq="D", tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "open": [100.0] * periods,
            "high": [110.0] * periods,
            "low": [98.0] * periods,
            "close": [104.0] * periods,
        },
        index=stamps,
    )


def _call(signal_id: str, *, logged_at: str = "2026-01-02T12:00:00+00:00") -> dict:
    return {
        "signal_id": signal_id,
        "scrip_code": "CDX_TESTINR",
        "symbol": "TESTINR",
        "setup": "test_setup",
        "armed_on": "2026-01-01",
        "entry": 100.0,
        "stop": 90.0,
        "t1": 120.0,
        "source": "live",
        "logged_at": logged_at,
        "outcome": None,
    }


def test_crypto_loader_preserves_the_real_daily_open_timestamp() -> None:
    with duckdb.connect(":memory:") as con:
        con.execute(
            "CREATE TABLE candles ("
            "scrip_code VARCHAR, interval VARCHAR, ts BIGINT, open DOUBLE, "
            "high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
        )
        stamp = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
        con.execute(
            "INSERT INTO candles VALUES (?, '1day', ?, 100, 110, 90, 105, 1)",
            ["CDX_TESTINR", stamp],
        )
        bars = _load_crypto_bars(con, "CDX_TESTINR")

    assert str(bars.index.tz) == "Asia/Kolkata"
    assert bars.index[0].hour == 5
    assert bars.index[0].minute == 30


def test_crypto_offsets_are_deterministic_and_never_use_model_timing() -> None:
    first = freeze_offsets("signal-a")
    second = freeze_offsets("signal-a")

    assert first == second
    assert len(first) == N_COHORTS
    assert min(first) >= MIN_OFFSET
    assert max(first) <= MAX_OFFSET
    assert 0 not in first


def test_new_crypto_call_is_forward_only_and_tamper_evident() -> None:
    row = _call("signal-a")
    activated = datetime(2026, 1, 2, 10, tzinfo=UTC)
    assigned = datetime(2026, 1, 2, 13, tzinfo=UTC)
    record = _new_record(row, _bars(), assigned, activated)

    assert record["prospective_eligible"] is True
    assert len(record["timings"]) == MAX_OFFSET + 1
    _verify_record(record, row)

    record["cohort_offsets"][0] = MAX_OFFSET + 1
    with pytest.raises(ValueError, match="timing assignment changed"):
        _verify_record(record, row)

    old = _call("old", logged_at="2026-01-02T09:00:00+00:00")
    excluded = _new_record(old, _bars(), assigned, activated)
    assert excluded["prospective_eligible"] is False


def test_crypto_timing_uses_only_entries_after_assignment_and_hashes_source() -> None:
    bars = _bars()
    row = _call("signal-a")
    activated = datetime(2026, 1, 2, 10, tzinfo=UTC)
    assigned = datetime(2026, 1, 2, 13, tzinfo=UTC)
    record = _new_record(row, bars, assigned, activated)
    model = record["timings"][0]
    costs = crypto_market(load_config(ROOT)).costs
    as_of = datetime(2026, 3, 1, tzinfo=UTC)

    assert _resolve_timing(model, record, bars, as_of=as_of, costs=costs)
    assert model["status"] == "resolved"
    assert pd.Timestamp(model["entry_date"]).date() > assigned.date()
    assert model["net_r"] is not None
    assert model["source_sha256"]

    changed = bars.copy()
    changed.iloc[0, changed.columns.get_loc("close")] = 103.0
    with pytest.raises(ValueError, match="timing source changed"):
        _resolve_timing(model, record, changed, as_of=as_of, costs=costs)


def test_crypto_summary_never_pools_setup_evidence() -> None:
    records = []
    start = date(2026, 1, 1)
    for index in range(100):
        armed_on = (start + timedelta(days=index // 2)).isoformat()
        records.append(
            {
                "signal_id": f"a-{index}",
                "setup": "setup_a",
                "armed_on": armed_on,
                "prospective_eligible": True,
                "cohort_offsets": [MIN_OFFSET] * N_COHORTS,
                "timings": [
                    {
                        "offset_sessions": offset,
                        "status": "resolved",
                        "label": int(offset == 0),
                        "net_r": 0.5 if offset == 0 else -1.0,
                    }
                    for offset in range(0, MAX_OFFSET + 1)
                ],
            }
        )
    records.append(
        {
            "signal_id": "b-1",
            "setup": "setup_b",
            "armed_on": start.isoformat(),
            "prospective_eligible": True,
            "cohort_offsets": [MIN_OFFSET] * N_COHORTS,
            "timings": [
                {
                    "offset_sessions": offset,
                    "status": "resolved",
                    "label": 0,
                    "net_r": -1.0,
                }
                for offset in range(0, MAX_OFFSET + 1)
            ],
        }
    )

    summary = summarize_timing({"records": records})

    assert summary["status"] == "candidate_timing_pass"
    assert summary["qualified_setups"] == ["setup_a"]
    assert summary["evidence_pooled_across_setups"] is False
    by_setup = {row["setup"]: row for row in summary["by_setup"]}
    assert by_setup["setup_a"]["paired_calls"] == 100
    assert by_setup["setup_a"]["timing_advantage_r"] == 1.5
    assert by_setup["setup_b"]["paired_calls"] == 1
    assert by_setup["setup_b"]["status"] == "collecting_insufficient_evidence"
    assert summary["eligible_for_live"] is False
