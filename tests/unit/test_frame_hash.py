"""random_timing_control._frame_hash: the fast version gives the row-by-row original's result."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from tradedesk import random_timing_control as rtc
from tradedesk.prediction_ledger import canonical_sha256

COLUMNS = ["open", "high", "low", "close", "volume"]


def _original(frame: pd.DataFrame) -> str:
    """The implementation that sealed the stored evidence, kept here as the reference."""

    values = [
        [
            pd.Timestamp(str(stamp)).isoformat(),
            *(None if pd.isna(value) else float(value) for value in row[COLUMNS]),
        ]
        for stamp, row in frame.iterrows()
    ]
    return canonical_sha256(values)


def _bars(index: pd.Index, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = rng.uniform(1.0, 5000.0, size=(len(index), 4)).round(2)
    frame = pd.DataFrame(prices, index=index, columns=COLUMNS[:4])
    frame["volume"] = rng.integers(0, 10**9, size=len(index))
    frame["extra"] = "ignored"
    return frame


def _cases() -> dict[str, pd.DataFrame]:
    days = pd.date_range("2025-01-01", periods=40, freq="B")
    gaps = _bars(days, 2)
    gaps.iloc[3, 0] = np.nan
    gaps.iloc[7, 3] = np.inf
    floats = _bars(days, 3)
    floats["volume"] = floats["volume"].astype(float)
    floats.iloc[5, 4] = np.nan
    objects = _bars(days, 4).astype({"close": object, "volume": object})
    objects.iloc[2, 3] = None
    objects.iloc[4, 3] = Decimal("101.35")
    nullable = _bars(days, 5).astype({"volume": "Int64", "open": "Float64"})
    nullable.iloc[1, 4] = pd.NA
    nullable.iloc[6, 0] = pd.NA
    small = _bars(days, 6).astype({"open": "float32", "volume": "int32"})
    return {
        "plain": _bars(days),
        "kolkata": _bars(days.tz_localize("Asia/Kolkata")),
        "minutes": _bars(pd.date_range("2026-11-11 09:15", periods=40, freq="15min", tz="UTC")),
        "seconds_unit": _bars(days.as_unit("s")),
        "dates": _bars(pd.Index([date(2026, 1, 1) + timedelta(days=i) for i in range(40)])),
        "strings": _bars(pd.Index([f"2026-02-{i:02d}" for i in range(1, 29)])),
        "gaps": gaps,
        "float_volume": floats,
        "objects": objects,
        "nullable": nullable,
        "narrow_types": small,
        "whole_numbers": _bars(days)[COLUMNS].round(0).astype("int64"),
        "huge_volume": _bars(days).assign(volume=2**60 + 12345),
        "one_row": _bars(days[:1]),
        "empty": _bars(days[:0]),
        "reordered": _bars(days)[["volume", "close", "extra", "low", "high", "open"]],
    }


@pytest.mark.parametrize("name", sorted(_cases()))
def test_fast_hash_equals_the_original(name: str) -> None:
    frame = _cases()[name]
    assert rtc._frame_hash(frame) == _original(frame)


def test_a_changed_bar_changes_the_hash() -> None:
    frame = _cases()["plain"]
    other = frame.copy()
    other.iloc[10, 3] += 0.01
    assert rtc._frame_hash(frame) != rtc._frame_hash(other)
    assert rtc._frame_hash(frame) != rtc._frame_hash(frame.iloc[:-1])


@pytest.mark.parametrize("market", ["nse", "bse", "crypto"])
def test_fast_hash_equals_the_original_on_stored_bars(market: str) -> None:
    path = Path(rtc._database_path(market))
    if not path.exists():
        pytest.skip(f"{path} is not on this machine")
    try:
        connection, loader = rtc._duckdb_loader(path)
    except duckdb.Error as exc:
        pytest.skip(f"{path} is busy: {exc}")
    try:
        codes = [
            row[0]
            for row in connection.execute(
                "SELECT scrip_code FROM candles WHERE interval='1day' "
                "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 12"
            ).fetchall()
        ]
        frames = [loader(code) for code in codes]
    finally:
        connection.close()
    assert frames
    for frame in frames:
        assert rtc._frame_hash(frame) == _original(frame)
        assert rtc._frame_hash(frame.iloc[:37]) == _original(frame.iloc[:37])
