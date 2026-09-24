"""Read a consistent AEM candle snapshot, bounded above by a completed session."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from tradedesk_lab.aem_contract import AemContract

IST = ZoneInfo("Asia/Kolkata")


def regular_minutes(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove the feed's 15:30 marker and non-regular-session observations."""
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        raise ValueError("minute candles require an explicit timezone")
    local = idx.tz_convert(IST)
    return frame.loc[(local.time >= time(9, 15)) & (local.time < time(15, 30))]


def frame_fingerprint(frame: pd.DataFrame) -> dict:
    return {
        "rows": len(frame),
        "from": str(frame.index[0]) if len(frame) else None,
        "through": str(frame.index[-1]) if len(frame) else None,
        "sha256": hashlib.sha256(
            pd.util.hash_pandas_object(frame, index=True).values.tobytes()
        ).hexdigest(),
    }


def read_aem_source(
    root: Path,
    *,
    contract: AemContract,
    sessions: int | None,
    benchmark_symbol: str,
    as_of: date | None = None,
    now: datetime | None = None,
) -> tuple[dict, dict, dict, list[date], list[date], dict]:
    """Use complete observed history and a common benchmark evaluation calendar.

    The instrument master is current, so historical membership remains survivorship
    biased; the returned manifest states that explicitly. All reads share one transaction.
    A shorter evaluation window must not shorten RVOL history: sparse same-time-slot
    observations may reach arbitrarily farther back than twenty exchange sessions.
    Loading all available M1 history costs additional memory; each consumed frame's
    exact row count and timestamp bounds are fingerprinted in the source manifest.
    """
    when = now or datetime.now(IST)
    if when.tzinfo is None:
        raise ValueError("now must be timezone aware")
    local = when.astimezone(IST)
    completed = local.date() if local.time() >= time(15, 30) else local.date() - timedelta(days=1)
    end = as_of or completed
    if end > completed:
        raise ValueError("as_of must be a completed daily session")
    if sessions is not None and sessions < 1:
        raise ValueError("sessions must be positive or None")
    upper = int(datetime.combine(end + timedelta(days=1), time(), IST).timestamp())
    daily, minute, symbols, fingerprint = {}, {}, {}, {}
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        con.execute("BEGIN TRANSACTION")
        benchmark = con.execute(
            "SELECT scrip_code FROM instruments WHERE trading_symbol=? AND exch='NSE' "
            "AND kind='index' ORDER BY scrip_code",
            [benchmark_symbol],
        ).fetchall()
        if len(benchmark) != 1:
            raise ValueError("missing_or_ambiguous_benchmark")
        timestamps = con.execute(
            "SELECT ts FROM candles WHERE scrip_code=? AND interval='1day' AND ts<? ORDER BY ts",
            [benchmark[0][0], upper],
        ).fetchall()
        calendar = sorted({datetime.fromtimestamp(row[0], IST).date() for row in timestamps})
        if not calendar:
            raise ValueError("missing_benchmark_sessions")
        evaluation_dates = calendar[-sessions:] if sessions else calendar
        evaluation_lower = int(datetime.combine(evaluation_dates[0], time(), IST).timestamp())
        instruments = con.execute(
            "SELECT i.scrip_code,i.trading_symbol FROM instruments i "
            "WHERE i.exch='NSE' AND i.kind='equity' AND i.series='EQ' "
            "AND EXISTS(SELECT 1 FROM candles c WHERE c.scrip_code=i.scrip_code "
            "AND c.interval='1day' AND c.ts<?) "
            "AND EXISTS(SELECT 1 FROM candles c WHERE c.scrip_code=i.scrip_code "
            "AND c.interval='1minute' AND c.ts>=? AND c.ts<?) ORDER BY i.scrip_code",
            [upper, evaluation_lower, upper],
        ).fetchall()
        for code, symbol in instruments:
            frames = {}
            for interval in ("1day", "1minute"):
                raw = con.execute(
                    "SELECT ts,open,high,low,close,volume FROM candles "
                    "WHERE scrip_code=? AND interval=? AND ts<? ORDER BY ts",
                    [code, interval, upper],
                ).df()
                raw.index = pd.DatetimeIndex(
                    pd.to_datetime(raw.pop("ts"), unit="s", utc=True).dt.tz_convert(IST)
                )
                if raw.index.has_duplicates:
                    raise ValueError(f"duplicate_{interval}_candles:{code}")
                frames[interval] = raw
            daily[code], minute[code] = frames["1day"], regular_minutes(frames["1minute"])
            symbols[code] = symbol
            fingerprint[code] = {
                "symbol": symbol,
                "daily": frame_fingerprint(daily[code]),
                "minute": frame_fingerprint(minute[code]),
            }
        con.execute("COMMIT")
    calendar_dates = [str(day) for day in calendar]
    source = {
        "as_of": str(end),
        "benchmark": benchmark_symbol,
        "benchmark_code": benchmark[0][0],
        "benchmark_latest_session": str(calendar[-1]),
        "benchmark_calendar": calendar_dates,
        "benchmark_calendar_sha256": hashlib.sha256(
            json.dumps(calendar_dates, separators=(",", ":")).encode()
        ).hexdigest(),
        "evaluation_dates": [str(day) for day in evaluation_dates],
        "rvol_lookback_observations_per_slot": contract.rvol_lookback_sessions,
        "rvol_minimum_observations_per_slot": contract.rvol_min_sessions,
        "minute_history_policy": "all available regular M1 history through as_of",
        "memory_tradeoff": (
            "Full observed M1 history is retained for selected symbols so missing sessions or "
            "slots cannot make historical features depend on the requested evaluation window."
        ),
        "instrument_filter": (
            "current NSE equity EQ instruments with D1 history and M1 data in the evaluation window"
        ),
        "historical_constituents_available": False,
        "symbols": fingerprint,
    }
    source["sha256"] = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return daily, minute, symbols, calendar, evaluation_dates, source
