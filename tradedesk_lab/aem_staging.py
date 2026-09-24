"""Insert-only, explicitly isolated M1 research storage with observed-slot coverage.

This is not the production CandleStore: conflicting observations are never replaced,
HTTP success is never evidence of completeness, and only the regular NSE minute grid
is admitted. A regular full session has 375 opens, 09:15 through 15:29 IST.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, date, datetime, time
from numbers import Real
from pathlib import Path

import duckdb
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle, Interval

_VERSION = "1"
_COLUMNS = ["scrip_code", "interval", "ts", "open", "high", "low", "close", "volume"]
_PRICE_COLUMNS = ("open", "high", "low", "close", "volume")
_FULL_SESSION_MASK = (1 << 375) - 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    scrip_code VARCHAR NOT NULL, interval VARCHAR NOT NULL, ts BIGINT NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
    PRIMARY KEY (scrip_code, interval, ts)
);
CREATE TABLE IF NOT EXISTS aem_stage_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS aem_stage_ingests (
    recorded_at VARCHAR NOT NULL, origin VARCHAR NOT NULL, statistics VARCHAR NOT NULL
);
"""


def _safe_path(path: Path | str, output_root: Path | str, source_path: Path | str | None) -> Path:
    destination = Path(os.path.abspath(path))
    allowed = Path(os.path.abspath(output_root)) / "aem_history"
    if not destination.is_relative_to(allowed) or destination == allowed:
        raise ValueError("stage_path_must_be_inside_output_root_aem_history")
    # Check lexical components as well as resolved containment: even aliases that
    # currently point inside the lab are refused to keep the write boundary explicit.
    for candidate in (destination, *destination.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("stage_path_must_not_use_symlink_or_junction")
    if not destination.resolve().is_relative_to(allowed.resolve()):
        raise ValueError("stage_path_escapes_research_directory")
    if destination.exists() and (not destination.is_file() or destination.stat().st_nlink != 1):
        raise ValueError("stage_path_must_be_a_single_link_regular_file")
    if source_path is not None:
        source = Path(source_path)
        if destination.resolve() == source.resolve() or (
            destination.exists() and source.exists() and destination.samefile(source)
        ):
            raise ValueError("stage_path_is_source_database")
    return destination


def _request(codes: list[str], sessions: list[date]) -> tuple[list[str], list[date]]:
    if not codes or len(set(codes)) != len(codes) or any(not code for code in codes):
        raise ValueError("codes_must_be_nonempty_and_unique")
    if any(type(day) is not date for day in sessions) or len(set(sessions)) != len(sessions):
        raise ValueError("sessions_must_be_unique_dates")
    return list(codes), sorted(sessions)


def _values(values: tuple) -> tuple[float, float, float, float, int]:
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in values):
        raise ValueError("invalid_ohlcv_type")
    opening, high, low, close, volume = values
    if not all(math.isfinite(value) and value > 0 for value in values[:4]):
        raise ValueError("prices_must_be_finite_and_positive")
    if low > min(opening, close) or high < max(opening, close) or low > high:
        raise ValueError("invalid_ohlc_geometry")
    if not math.isfinite(volume) or volume < 0 or volume != int(volume) or volume > 2**63 - 1:
        raise ValueError("volume_must_be_nonnegative_int64")
    return float(opening), float(high), float(low), float(close), int(volume)


class StageStore:
    """A lab-only database; caller must additionally validate its trusted output root.

    Existing databases without this module's marker are refused *before* opening
    them writable. ``source_path`` adds a direct production-source identity guard.
    The collector owns the cross-process run lock; DuckDB owns its database lock.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        output_root: Path | str,
        source_path: Path | str | None = None,
    ) -> None:
        self.path = _safe_path(path, output_root, source_path)
        if self.path.exists():
            with duckdb.connect(str(self.path), read_only=True) as existing:
                try:
                    marker = existing.execute(
                        "SELECT value FROM aem_stage_meta WHERE key='schema_version'"
                    ).fetchone()
                except duckdb.Error as error:
                    raise ValueError("not_an_aem_stage_database") from error
                if marker != (_VERSION,):
                    raise ValueError("unsupported_aem_stage_database_version")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        try:
            self.con.execute(_SCHEMA)
            self.con.execute(
                "INSERT INTO aem_stage_meta VALUES ('schema_version', ?) ON CONFLICT DO NOTHING",
                [_VERSION],
            )
        except Exception:
            self.con.close()
            raise

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> StageStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ingest(
        self,
        candles_by_code: dict[str, list[Candle]],
        *,
        codes: list[str],
        start: date,
        end_exclusive: date,
        sessions: list[date],
        origin: str,
    ) -> dict:
        """Validate an entire response, then atomically insert without replacement.

        Empty and partial responses are valid observations but never mark a session
        complete. Extra dates and the feed's 15:30 marker are counted and excluded.
        Unknown symbols and wrong intervals are rejected even on an extra date.
        """
        codes, sessions = _request(codes, sessions)
        if type(start) is not date or type(end_exclusive) is not date or start >= end_exclusive:
            raise ValueError("request_requires_increasing_date_bounds")
        if not origin or not isinstance(origin, str):
            raise ValueError("origin_must_be_nonempty")
        requested = set(codes)
        if set(candles_by_code) - requested:
            raise ValueError("unexpected_response_code")
        planned = set(sessions)
        stats = {
            "received_rows": 0,
            "out_of_window_rows": 0,
            "non_session_rows": 0,
            "outside_regular_rows": 0,
            "duplicate_payload_rows": 0,
            "duplicate_stored_rows": 0,
            "accepted_unique_rows": 0,
            "inserted_rows": 0,
        }
        unique: dict[tuple[str, str, int], tuple] = {}
        for response_code, candles in candles_by_code.items():
            for candle in candles:
                stats["received_rows"] += 1
                if candle.scrip_code != response_code or candle.scrip_code not in requested:
                    raise ValueError("unexpected_candle_code")
                if candle.interval != Interval.M1:
                    raise ValueError("unexpected_candle_interval")
                if candle.ts.tzinfo is None or candle.ts.utcoffset() is None:
                    raise ValueError("candle_timestamp_requires_timezone")
                local = candle.ts.astimezone(IST)
                if not start <= local.date() < end_exclusive:
                    stats["out_of_window_rows"] += 1
                    continue
                values = _values(
                    (candle.open, candle.high, candle.low, candle.close, candle.volume)
                )
                if local.second or local.microsecond:
                    raise ValueError("candle_open_must_align_to_exact_minute")
                if local.date() not in planned:
                    stats["non_session_rows"] += 1
                    continue
                if not time(9, 15) <= local.time() < time(15, 30):
                    stats["outside_regular_rows"] += 1
                    continue
                key = (candle.scrip_code, Interval.M1.value, int(local.timestamp()))
                if key in unique:
                    if unique[key] != values:
                        raise ValueError("conflicting_payload_candle")
                    stats["duplicate_payload_rows"] += 1
                else:
                    unique[key] = values
        stats["accepted_unique_rows"] = len(unique)
        frame = pd.DataFrame([(*key, *values) for key, values in unique.items()], columns=_COLUMNS)
        registered = False
        self.con.execute("BEGIN TRANSACTION")
        try:
            if not frame.empty:
                self.con.register("_aem_incoming", frame)
                registered = True
                conflict = " OR ".join(
                    f"c.{name} IS DISTINCT FROM i.{name}" for name in _PRICE_COLUMNS
                )
                if self.con.execute(
                    "SELECT 1 FROM candles c JOIN _aem_incoming i "
                    "USING (scrip_code, interval, ts) WHERE " + conflict + " LIMIT 1"
                ).fetchone():
                    raise ValueError("conflicting_stored_candle")
                stats["duplicate_stored_rows"] = self.con.execute(
                    "SELECT count(*) FROM candles JOIN _aem_incoming "
                    "USING (scrip_code, interval, ts)"
                ).fetchone()[0]
                self.con.execute(
                    "INSERT INTO candles SELECT i.* FROM _aem_incoming i WHERE NOT EXISTS "
                    "(SELECT 1 FROM candles c WHERE c.scrip_code=i.scrip_code "
                    "AND c.interval=i.interval AND c.ts=i.ts)"
                )
                stats["inserted_rows"] = len(unique) - stats["duplicate_stored_rows"]
            self.con.execute(
                "INSERT INTO aem_stage_ingests VALUES (?, ?, ?)",
                [datetime.now(UTC).isoformat(), origin, json.dumps(stats, sort_keys=True)],
            )
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        finally:
            if registered:
                self.con.unregister("_aem_incoming")
        return stats

    def coverage(self, codes: list[str], sessions: list[date]) -> dict:
        """Re-inspect stored timestamps and OHLCV, not request logs or row counts.

        A compact 375-bit slot mask checks every expected minute without retaining
        millions of Python timestamp objects for a whole-universe coverage audit.
        """
        codes, sessions = _request(codes, sessions)
        planned = set(sessions)
        masks: dict[tuple[str, date], int] = {}
        invalid: dict[tuple[str, date], int] = {}
        counts = dict.fromkeys(codes, 0)
        if sessions:
            lower = int(datetime.combine(sessions[0], time(), IST).timestamp())
            upper = int(datetime.combine(sessions[-1], time.max, IST).timestamp()) + 1
            placeholders = ",".join("?" for _ in codes)
            cursor = self.con.execute(
                f"SELECT scrip_code,ts,open,high,low,close,volume FROM candles "
                f"WHERE interval=? AND scrip_code IN ({placeholders}) AND ts>=? AND ts<?",
                [Interval.M1.value, *codes, lower, upper],
            )
            while rows := cursor.fetchmany(20_000):
                for code, epoch, opening, high, low, close, volume in rows:
                    local = datetime.fromtimestamp(epoch, IST)
                    if local.date() not in planned:
                        continue
                    key = (code, local.date())
                    counts[code] += 1
                    slot = local.hour * 60 + local.minute - (9 * 60 + 15)
                    try:
                        _values((opening, high, low, close, volume))
                        if local.second or local.microsecond or not 0 <= slot < 375:
                            raise ValueError("invalid_stored_minute_slot")
                    except (ValueError, TypeError):
                        invalid[key] = invalid.get(key, 0) + 1
                        continue
                    masks[key] = masks.get(key, 0) | (1 << slot)
        by_code = {}
        for code in codes:
            complete = [
                day
                for day in sessions
                if masks.get((code, day), 0) == _FULL_SESSION_MASK
                and not invalid.get((code, day), 0)
            ]
            complete_set = set(complete)
            by_code[code] = {
                "complete_sessions": [day.isoformat() for day in complete],
                "missing_sessions": [
                    day.isoformat() for day in sessions if day not in complete_set
                ],
                "total_rows": counts[code],
                "invalid_rows": sum(invalid.get((code, day), 0) for day in sessions),
            }
        complete_count = sum(len(item["complete_sessions"]) for item in by_code.values())
        return {
            "codes": len(codes),
            "sessions": len(sessions),
            "expected_bars_per_session": 375,
            "expected_rows": len(codes) * len(sessions) * 375,
            "total_rows": sum(counts.values()),
            "complete_sessions": complete_count,
            "missing_sessions": len(codes) * len(sessions) - complete_count,
            "invalid_rows": sum(invalid.values()),
            "by_code": by_code,
        }
