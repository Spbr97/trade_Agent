"""Phase 1's data layer, wired to the existing `CandleStore`/`MarketDataClient`/`health.py`
instead of a new Parquet-based fetch pipeline - see the harness plan's Context for why: the
source-adapter interface the template asks for already exists
(`data.history_loader.MarketDataClient`, implemented by both `broker.indstocks` and
`broker.coindcx`), duplicate timestamps are structurally impossible (a DuckDB PRIMARY KEY on
`(scrip_code, interval, ts)`), and the gap/OHLC/zero-volume/big-jump checks already exist in
`data.health`. What's genuinely new here is `snapshot_hash`: proof of exactly which rows a
run used, the "frozen fixture, reproducible run" guarantee, without migrating off DuckDB.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pandas as pd

from tradedesk.data.candle_store import ist_dates
from tradedesk.data.health import check_series
from tradedesk.data.models import QualityIssue


def snapshot_hash(df: pd.DataFrame) -> str:
    """Hashes the exact OHLCV values and index a harness run saw, adapting
    `tradedesk_lab.artifacts.digest`'s file-hashing pattern to a DataFrame - a run's report
    can carry this so a later re-run can prove it used identical data."""
    payload = pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes()
    return hashlib.sha256(payload).hexdigest()


def validate_candles(
    code: str, df: pd.DataFrame, *, jump_threshold: float = 0.30
) -> list[QualityIssue]:
    """Runs the project's own gap/OHLC/zero-volume/big-jump checks
    (`data.health.check_series`) against ANY interval's candles, not just daily -
    `run_quality_report`'s own convenience wrapper hardcodes `Interval.D1`, so this calls
    `check_series` directly against a calendar built from the data's own span (every
    calendar day between its first and last bar - crypto trades every day; a `check_series`
    caller for a market with real non-trading days would pass its own calendar instead).
    Staleness is deliberately not meaningful here: this validates an already-loaded, frozen
    historical slice, not a live feed, so the calendar never extends past the data's own tail.
    """
    if df.empty:
        return check_series(code, df, [])
    dates = ist_dates(df)
    first, last = dates[0], dates[-1]
    calendar = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return check_series(code, df, calendar, jump_threshold=jump_threshold)
