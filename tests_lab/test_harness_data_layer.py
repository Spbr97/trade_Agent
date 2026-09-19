import pandas as pd
from tradedesk_lab.harness.data_layer import snapshot_hash, validate_candles

from tradedesk.data.models import IssueKind


def _frame(rows: int = 5, *, zero_volume_at: int | None = None) -> pd.DataFrame:
    idx = pd.date_range("2026-09-16", periods=rows, freq="1h", tz="Asia/Kolkata")
    volume = [1000] * rows
    if zero_volume_at is not None:
        volume[zero_volume_at] = 0
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": volume}, index=idx
    )


def test_snapshot_hash_is_deterministic_and_sensitive_to_content():
    a = _frame()
    b = _frame()
    c = _frame(zero_volume_at=2)
    assert snapshot_hash(a) == snapshot_hash(b)
    assert snapshot_hash(a) != snapshot_hash(c)


def test_validate_candles_flags_zero_volume_bars():
    issues = validate_candles("CDX_TESTINR", _frame(zero_volume_at=1))
    assert any(i.kind == IssueKind.ZERO_VOLUME for i in issues)


def test_validate_candles_clean_frame_has_no_issues():
    issues = validate_candles("CDX_TESTINR", _frame())
    assert issues == []


def test_validate_candles_handles_empty_frame():
    empty = _frame(rows=0)
    issues = validate_candles("CDX_TESTINR", empty)
    assert any(i.kind == IssueKind.NO_DATA for i in issues)
