"""Frozen, point-in-time crypto dataset and quick-profit label contract.

This module is deliberately research-only.  It records exact CoinDCX universe
observations from activation onward, never invents earlier membership, materializes only
fully closed daily candles, makes missingness explicit, and computes deterministic labels
for a small preregistered geometry set.  Crypto evidence is never pooled with equities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from tradedesk.config import load_config
from tradedesk.engine.indicators import atr
from tradedesk.markets import crypto_market
from tradedesk.markets.tax import after_tax_r, tds_share_of_costs
from tradedesk.models import TradeType, price_decimal
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

VERSION = "crypto-accuracy-dataset-v1"
DEFAULT_OUTPUT = OUTPUT / "crypto_accuracy_dataset"
DEFAULT_DB = ROOT / "data/crypto.duckdb"
DEFAULT_UNIVERSE_REPORT = ROOT / "data/reports/crypto_universe_latest.json"
HISTORY_NAME = "universe_observations.jsonl"
SECONDS_PER_DAY = 86_400
ATR_PERIOD = 14
NOTIONAL_INR = 10_000.0
MIN_POINT_IN_TIME_SESSIONS = 30


@dataclass(frozen=True)
class CryptoGeometry:
    name: str
    stop_atr: float
    target_r: float
    max_hold_sessions: int

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("geometry name must be non-empty snake-like text")
        if not math.isfinite(self.stop_atr) or self.stop_atr <= 0:
            raise ValueError("stop_atr must be positive and finite")
        if not math.isfinite(self.target_r) or self.target_r <= 0:
            raise ValueError("target_r must be positive and finite")
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be positive")


DEFAULT_GEOMETRIES = (
    CryptoGeometry("quick_075atr_050r_3d", 0.75, 0.50, 3),
    CryptoGeometry("quick_100atr_075r_5d", 1.00, 0.75, 5),
    CryptoGeometry("quick_125atr_100r_7d", 1.25, 1.00, 7),
)


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


@contextmanager
def _lock(output: Path) -> Iterator[None]:
    path = output / "collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another crypto dataset collector owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_observations(output: Path) -> list[dict[str, Any]]:
    path = output / HISTORY_NAME
    if not path.exists():
        return []
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    previous: str | None = None
    prior_time: datetime | None = None
    for event in events:
        payload = {key: value for key, value in event.items() if key != "event_sha256"}
        if event.get("event_sha256") != _json_hash(payload):
            raise ValueError("crypto universe observation hash changed")
        if event.get("previous_event_sha256") != previous:
            raise ValueError("crypto universe observation chain is broken")
        observed = datetime.fromisoformat(event["observed_at"])
        if prior_time is not None and observed <= prior_time:
            raise ValueError("crypto universe observations are not strictly chronological")
        if event.get("active_codes_sha256") != _json_hash(event.get("active_codes", [])):
            raise ValueError("crypto universe active-code hash changed")
        previous = event["event_sha256"]
        prior_time = observed
    return events


def _universe_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[str, dict[str, Any]] = {}
    for event in events:
        active = set(event["active_codes"])
        inactive = set(event.get("known_inactive_codes", []))
        observed_at = event["observed_at"]
        for code in active:
            row = pairs.setdefault(
                code,
                {
                    "first_observed_active_at": observed_at,
                    "last_observed_active_at": observed_at,
                    "first_observed_inactive_at": None,
                },
            )
            if row["first_observed_active_at"] is None:
                row["first_observed_active_at"] = observed_at
            row["last_observed_active_at"] = observed_at
            row["status"] = "active"
        for code in inactive | set(event.get("removed_since_previous", [])):
            row = pairs.setdefault(
                code,
                {
                    "first_observed_active_at": None,
                    "last_observed_active_at": None,
                    "first_observed_inactive_at": observed_at,
                },
            )
            if row["first_observed_inactive_at"] is None:
                row["first_observed_inactive_at"] = observed_at
            if code not in active:
                row["status"] = "inactive_or_delisted_observed"
    latest = events[-1] if events else None
    return {
        "version": VERSION,
        "status": "collecting" if events else "not_activated",
        "activated_at": events[0]["observed_at"] if events else None,
        "observations": len(events),
        "current_active_pairs": len(latest["active_codes"]) if latest else 0,
        "current_known_inactive_pairs": (
            len(latest.get("known_inactive_codes", [])) if latest else 0
        ),
        "listing_transitions": sum(len(event["added_since_previous"]) for event in events),
        "removal_transitions": sum(len(event["removed_since_previous"]) for event in events),
        "latest_event_sha256": latest["event_sha256"] if latest else None,
        "membership_before_activation": "unknown_not_inferred",
        "pairs": dict(sorted(pairs.items())),
    }


def record_universe_observation(
    output: Path = DEFAULT_OUTPUT,
    active_codes: Iterable[str] = (),
    inactive_codes: Iterable[str] = (),
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Append one tamper-evident universe observation without backdating membership."""
    output = Path(output)
    observed = _aware_utc(observed_at or datetime.now(UTC))
    active = sorted(set(active_codes))
    inactive = sorted(set(inactive_codes) - set(active))
    if not active:
        raise ValueError("crypto universe observation cannot be empty")
    with _lock(output):
        events = _read_observations(output)
        if events and observed <= datetime.fromisoformat(events[-1]["observed_at"]):
            raise ValueError("universe observation must be newer than the previous event")
        prior = set(events[-1]["active_codes"]) if events else None
        payload = {
            "version": VERSION,
            "observed_at": observed.isoformat(),
            "active_codes": active,
            "active_codes_sha256": _json_hash(active),
            "known_inactive_codes": inactive,
            "added_since_previous": sorted(set(active) - prior) if prior is not None else [],
            "removed_since_previous": sorted(prior - set(active)) if prior is not None else [],
            "membership_before_first_observation": "unknown_not_inferred",
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
        }
        event = {**payload, "event_sha256": _json_hash(payload)}
        events.append(event)
        history = output / HISTORY_NAME
        history.parent.mkdir(parents=True, exist_ok=True)
        temporary = history.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in events),
            encoding="utf-8",
        )
        temporary.replace(history)
        write_json(output / "universe_state.json", _universe_state(events))
        return event


def _closed_rows(
    con: duckdb.DuckDBPyConnection, codes: list[str], cutoff_epoch: int
) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame(
            columns=["scrip_code", "ts", "open", "high", "low", "close", "volume"]
        )
    placeholders = ",".join("?" for _ in codes)
    return con.execute(
        "SELECT scrip_code,ts,open,high,low,close,volume FROM candles "
        f"WHERE interval='1day' AND scrip_code IN ({placeholders}) "
        "AND ts + ? <= ? ORDER BY scrip_code,ts",
        [*codes, SECONDS_PER_DAY, cutoff_epoch],
    ).df()


def _source_hash(frame: pd.DataFrame) -> str:
    hasher = hashlib.sha256()
    for row in frame.itertuples(index=False):
        values = (
            str(row.scrip_code),
            str(int(row.ts)),
            float(row.open).hex(),
            float(row.high).hex(),
            float(row.low).hex(),
            float(row.close).hex(),
            str(int(row.volume)),
        )
        hasher.update(("|".join(values) + "\n").encode())
    return hasher.hexdigest()


def _canonicalize_daily_source(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep the last CoinDCX bar per pair and IST calendar day.

    CoinDCX's older daily history contains real duplicate-day filler bars: a flat,
    zero-volume row immediately before the real row.  The established backtester keeps
    the later row.  C1 applies the same rule before hashing, coverage, ATR, and labels,
    while retaining every discarded row in a separate audit artifact.
    """
    ordered = raw.sort_values(["scrip_code", "ts"]).reset_index(drop=True).copy()
    if ordered.empty:
        duplicates = ordered.copy()
        duplicates["session"] = pd.Series(dtype="object")
        duplicates["reason"] = pd.Series(dtype="object")
        return ordered, duplicates
    opened = pd.to_datetime(ordered["ts"], unit="s", utc=True)
    ordered["_session"] = opened.dt.tz_convert("Asia/Kolkata").dt.date.astype(str)
    duplicate_mask = ordered.duplicated(["scrip_code", "_session"], keep="last")
    duplicates = ordered.loc[duplicate_mask].rename(columns={"_session": "session"})
    duplicates = duplicates.assign(reason="duplicate_ist_session_keep_last")
    canonical = ordered.loc[~duplicate_mask].drop(columns="_session")
    return canonical.reset_index(drop=True), duplicates.reset_index(drop=True)


def _quality_mask(frame: pd.DataFrame) -> pd.Series:
    prices = frame[["open", "high", "low", "close"]].astype(float)
    finite = np.isfinite(prices.to_numpy()).all(axis=1)
    valid = (
        finite
        & (prices > 0).all(axis=1).to_numpy()
        & (frame["volume"].astype(float).to_numpy() >= 0)
        & (prices["high"] >= prices[["open", "low", "close"]].max(axis=1)).to_numpy()
        & (prices["low"] <= prices[["open", "high", "close"]].min(axis=1)).to_numpy()
    )
    return pd.Series(valid, index=frame.index)


def _membership_for_close(
    code: str,
    closed_at: datetime,
    events: list[dict[str, Any]],
    observed_times: list[datetime],
) -> str:
    index = bisect_right(observed_times, closed_at) - 1
    if index < 0:
        return "pre_observation_membership_unknown"
    return "active" if code in events[index]["active_codes"] else "inactive"


def _prepare_daily(
    raw: pd.DataFrame,
    events: list[dict[str, Any]],
    excluded_codes: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    frame = raw.copy()
    if frame.empty:
        for column in (
            "session",
            "open_at",
            "closed_at",
            "quality_status",
            "membership_status",
            "configured_excluded",
            "point_in_time_eligible",
        ):
            frame[column] = pd.Series(dtype="object")
        return frame
    opens = pd.to_datetime(frame["ts"], unit="s", utc=True)
    closes = opens + pd.Timedelta(days=1)
    observed_times = [datetime.fromisoformat(event["observed_at"]) for event in events]
    quality = _quality_mask(frame)
    membership = [
        _membership_for_close(code, closed.to_pydatetime(), events, observed_times)
        for code, closed in zip(frame["scrip_code"], closes, strict=True)
    ]
    frame["session"] = opens.dt.tz_convert("Asia/Kolkata").dt.date.astype(str)
    frame["open_at"] = opens.map(lambda value: value.isoformat())
    frame["closed_at"] = closes.map(lambda value: value.isoformat())
    frame["quality_status"] = np.where(quality, "valid", "invalid_ohlcv")
    frame["membership_status"] = membership
    frame["configured_excluded"] = frame["scrip_code"].isin(excluded_codes)
    frame["point_in_time_eligible"] = (
        (frame["membership_status"] == "active") & (frame["quality_status"] == "valid")
        & ~frame["configured_excluded"]
    )
    return frame


def _coverage(
    daily: pd.DataFrame,
    codes: list[str],
    latest_session: date,
    current_active_codes: set[str],
    duplicate_counts: dict[str, int],
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for code in codes:
        group = daily.loc[daily["scrip_code"] == code].sort_values("ts")
        if group.empty:
            rows.append(
                {
                    "scrip_code": code,
                    "status": "no_daily_history",
                    "first_session": None,
                    "last_session": None,
                    "observed_sessions": 0,
                    "expected_sessions_since_first_observed": 0,
                    "missing_sessions": 0,
                    "invalid_ohlcv_sessions": 0,
                    "zero_volume_sessions": 0,
                    "stale_trailing_sessions": None,
                    "duplicate_daily_rows": duplicate_counts.get(code, 0),
                    "latest_membership_status": (
                        "active" if code in current_active_codes else "inactive_or_delisted"
                    ),
                }
            )
            continue
        sessions = {date.fromisoformat(value) for value in group["session"]}
        first = min(sessions)
        expected = set(pd.date_range(first, latest_session, freq="D").date)
        absent = sorted(expected - sessions)
        for session in absent:
            missing.append(
                {
                    "scrip_code": code,
                    "session": session.isoformat(),
                    "reason": "absent_daily_candle_since_first_observed_bar",
                }
            )
        last = max(sessions)
        stale = (latest_session - last).days
        latest_membership = (
            "active" if code in current_active_codes else "inactive_or_delisted"
        )
        rows.append(
            {
                "scrip_code": code,
                "status": (
                    "inactive_or_delisted_observed"
                    if latest_membership != "active"
                    else ("current" if stale == 0 else "stale")
                ),
                "first_session": first.isoformat(),
                "last_session": last.isoformat(),
                "observed_sessions": len(sessions),
                "expected_sessions_since_first_observed": len(expected),
                "missing_sessions": len(absent),
                "invalid_ohlcv_sessions": int((group["quality_status"] != "valid").sum()),
                "zero_volume_sessions": int((group["volume"] == 0).sum()),
                "stale_trailing_sessions": stale,
                "duplicate_daily_rows": duplicate_counts.get(code, 0),
                "latest_membership_status": latest_membership,
                "configured_excluded": bool(group["configured_excluded"].iloc[0]),
            }
        )
    missing_frame = pd.DataFrame(
        missing, columns=["scrip_code", "session", "reason"]
    )
    return rows, missing_frame


def _label_base(
    code: str,
    decision: pd.Series,
    geometry: CryptoGeometry,
) -> dict[str, Any]:
    return {
        "scrip_code": code,
        "decision_at": decision["closed_at"],
        "decision_session": decision["session"],
        "entry_at": None,
        "entry_session": None,
        "geometry": geometry.name,
        "stop_atr": geometry.stop_atr,
        "target_r": geometry.target_r,
        "max_hold_sessions": geometry.max_hold_sessions,
        "membership_status": decision["membership_status"],
        "point_in_time_eligible": bool(decision["point_in_time_eligible"]),
        "status": None,
        "exclusion_reason": None,
        "entry_price": None,
        "stop": None,
        "target": None,
        "exit_at": None,
        "exit_price": None,
        "outcome": None,
        "label": None,
        "gross_r": None,
        "net_r": None,
        "after_tax_r": None,
        "eligible_for_live": False,
    }


def _labels_for_code(
    code: str,
    group: pd.DataFrame,
    geometries: tuple[CryptoGeometry, ...],
    *,
    costs: Any,
    tds_share: float,
) -> list[dict[str, Any]]:
    ordered = group.sort_values("ts").reset_index(drop=True)
    session_dates = [date.fromisoformat(value) for value in ordered["session"]]
    atr_values = np.full(len(ordered), np.nan, dtype=float)
    # ATR may never bridge a missing calendar day or an invalid OHLCV row.  Compute each
    # valid contiguous run independently so Wilder's state cannot carry contaminated
    # history into a later otherwise-valid decision.
    start = 0
    while start < len(ordered):
        if ordered.iloc[start]["quality_status"] != "valid":
            start += 1
            continue
        end = start + 1
        while (
            end < len(ordered)
            and ordered.iloc[end]["quality_status"] == "valid"
            and (session_dates[end] - session_dates[end - 1]).days == 1
        ):
            end += 1
        segment = ordered.iloc[start:end]
        indicator_frame = segment.set_index(
            pd.to_datetime(segment["ts"], unit="s", utc=True)
        )[["open", "high", "low", "close", "volume"]]
        atr_values[start:end] = atr(indicator_frame, ATR_PERIOD).to_numpy(float)
        start = end
    by_session = {value: index for index, value in enumerate(session_dates)}
    slip = float(costs.slippage_pct)
    records: list[dict[str, Any]] = []
    eligible_indices = np.flatnonzero(ordered["point_in_time_eligible"].to_numpy())
    for decision_index in eligible_indices:
        decision = ordered.iloc[int(decision_index)]
        for geometry in geometries:
            record = _label_base(code, decision, geometry)
            if decision["quality_status"] != "valid":
                record.update(status="excluded", exclusion_reason="invalid_decision_ohlcv")
                records.append(record)
                continue
            atr_value = atr_values[decision_index]
            if not np.isfinite(atr_value) or atr_value <= 0:
                record.update(
                    status="excluded",
                    exclusion_reason="atr_contiguous_valid_lookback_unavailable",
                )
                records.append(record)
                continue
            decision_session = date.fromisoformat(str(decision["session"]))
            entry_session = decision_session + timedelta(days=1)
            entry_index = by_session.get(entry_session)
            if entry_index is None:
                record.update(status="excluded", exclusion_reason="missing_next_session_entry")
                records.append(record)
                continue
            final_session = entry_session + timedelta(
                days=geometry.max_hold_sessions - 1
            )
            latest_session = session_dates[-1]
            if final_session > latest_session:
                record.update(status="pending", exclusion_reason="right_censored")
                records.append(record)
                continue
            entry_row = ordered.iloc[entry_index]
            record["entry_at"] = entry_row["open_at"]
            record["entry_session"] = entry_row["session"]
            fill = float(entry_row["open"]) * (1 + slip)
            stop = fill - geometry.stop_atr * float(atr_value)
            target = fill + geometry.target_r * (fill - stop)
            record.update(entry_price=fill, stop=stop, target=target)
            if not (0 < stop < fill < target):
                record.update(status="excluded", exclusion_reason="invalid_geometry")
                records.append(record)
                continue
            qty = NOTIONAL_INR / fill
            exit_price: float | None = None
            exit_at: str | None = None
            outcome: str | None = None
            target_hit = False
            data_error: str | None = None
            for offset in range(geometry.max_hold_sessions):
                expected_session = entry_session + timedelta(days=offset)
                bar_index = by_session.get(expected_session)
                if bar_index is None:
                    data_error = "missing_outcome_session"
                    break
                bar = ordered.iloc[bar_index]
                if bar["quality_status"] != "valid":
                    data_error = "invalid_outcome_ohlcv"
                    break
                if offset > 0 and float(bar["open"]) <= stop:
                    exit_price = float(bar["open"]) * (1 - slip)
                    exit_at = bar["open_at"]
                    outcome = "gap_stop"
                elif offset > 0 and float(bar["open"]) >= target:
                    # A target already cleared at the open is known to precede every
                    # later intrabar print.  Keep a conservative target-price fill;
                    # stop-wins applies only when OHLC cannot establish ordering.
                    exit_price = target * (1 - slip)
                    exit_at = bar["open_at"]
                    outcome = "target"
                    target_hit = True
                elif float(bar["low"]) <= stop:
                    exit_price = stop * (1 - slip)
                    outcome = "stop"
                elif float(bar["high"]) >= target:
                    exit_price = target * (1 - slip)
                    outcome = "target"
                    target_hit = True
                elif offset == geometry.max_hold_sessions - 1:
                    exit_price = float(bar["close"]) * (1 - slip)
                    outcome = "max_hold"
                if exit_price is not None:
                    if exit_at is None:
                        exit_at = bar["closed_at"]
                    break
            if data_error is not None:
                record.update(status="excluded", exclusion_reason=data_error)
                records.append(record)
                continue
            if exit_price is None or exit_at is None or outcome is None:
                raise AssertionError("mature geometry did not resolve")
            round_trip = costs.round_trip_cost(
                trade_type=TradeType.DELIVERY,
                qty=qty,
                entry_price=price_decimal(fill),
                exit_price=price_decimal(exit_price),
            )
            gross_pnl = (exit_price - fill) * qty
            net_pnl = gross_pnl - float(round_trip.total)
            risk = (fill - stop) * qty
            gross_r = gross_pnl / risk
            net_r = net_pnl / risk
            taxed_r = after_tax_r(
                SimpleNamespace(gross_r=gross_r, net_r=net_r), tds_share
            )
            record.update(
                status="resolved",
                exit_at=exit_at,
                exit_price=exit_price,
                outcome=outcome,
                label=int(target_hit and net_pnl > 0),
                gross_r=gross_r,
                net_r=net_r,
                after_tax_r=taxed_r,
            )
            records.append(record)
    return records


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with duckdb.connect(":memory:") as con:
        con.register("_artifact", frame)
        escaped = str(temporary).replace("'", "''")
        con.execute(
            f"COPY _artifact TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    temporary.replace(path)


class _LabelWriter:
    def __init__(self) -> None:
        self.con = duckdb.connect(":memory:")
        self.con.execute(
            """
            CREATE TABLE labels (
                scrip_code VARCHAR,
                decision_at VARCHAR,
                decision_session VARCHAR,
                entry_at VARCHAR,
                entry_session VARCHAR,
                geometry VARCHAR,
                stop_atr DOUBLE,
                target_r DOUBLE,
                max_hold_sessions BIGINT,
                membership_status VARCHAR,
                point_in_time_eligible BOOLEAN,
                status VARCHAR,
                exclusion_reason VARCHAR,
                entry_price DOUBLE,
                stop DOUBLE,
                target DOUBLE,
                exit_at VARCHAR,
                exit_price DOUBLE,
                outcome VARCHAR,
                label BIGINT,
                gross_r DOUBLE,
                net_r DOUBLE,
                after_tax_r DOUBLE,
                eligible_for_live BOOLEAN
            )
            """
        )
        self.rows = 0
        self.statuses: Counter[str] = Counter()

    def append(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        frame = pd.DataFrame(rows)
        self.con.register("_chunk", frame)
        self.con.execute("INSERT INTO labels SELECT * FROM _chunk")
        self.con.unregister("_chunk")
        self.rows += len(frame)
        self.statuses.update(frame["status"].astype(str))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        escaped = str(temporary).replace("'", "''")
        self.con.execute(
            f"COPY labels TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        temporary.replace(path)

    def close(self) -> None:
        self.con.close()


def _artifact(path: Path, rows: int, *, published_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(published_path or path),
        "rows": rows,
        "sha256": digest(path),
    }


def _load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("crypto universe report is unavailable")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("active_codes"):
        raise ValueError("crypto universe report predates exact-code observations")
    return report


def materialize_crypto_dataset(
    root: Path = ROOT,
    output: Path = DEFAULT_OUTPUT,
    db_path: Path = DEFAULT_DB,
    universe_report_path: Path = DEFAULT_UNIVERSE_REPORT,
    *,
    as_of: datetime | None = None,
    geometries: tuple[CryptoGeometry, ...] = DEFAULT_GEOMETRIES,
) -> dict[str, Any]:
    """Freeze closed all-pair candles, explicit gaps, and deterministic labels."""
    root, output, db_path = Path(root), Path(output), Path(db_path)
    frozen_at = _aware_utc(as_of or datetime.now(UTC))
    report = _load_report(Path(universe_report_path))
    events = _read_observations(output)
    if not events:
        raise ValueError("crypto universe history has not been activated")
    eligible_events = [
        event
        for event in events
        if datetime.fromisoformat(event["observed_at"]) <= frozen_at
    ]
    if not eligible_events:
        raise ValueError("no crypto universe observation exists at the requested cutoff")
    latest_event = eligible_events[-1]
    report_codes = sorted(set(report["active_codes"]))
    if report_codes != latest_event["active_codes"]:
        raise ValueError("crypto universe report and append-only observation disagree")
    if report.get("universe_history_error"):
        raise ValueError("crypto universe observation failed during the tracker run")
    if report.get("universe_event_sha256") != latest_event["event_sha256"]:
        raise ValueError("crypto universe report points to a different observation")
    codes = sorted(
        {code for event in eligible_events for code in event.get("active_codes", [])}
    )
    cutoff_epoch = int(frozen_at.timestamp())
    with duckdb.connect(str(db_path), read_only=True) as con:
        raw = _closed_rows(con, codes, cutoff_epoch)
    if raw.empty:
        raise ValueError("no fully closed daily crypto candles are available")
    daily_source, duplicate_rows = _canonicalize_daily_source(raw)
    source_sha = _source_hash(daily_source)
    settings = load_config(root)
    market = crypto_market(settings)
    excluded_codes = market.universe_rules.exclude_codes
    contract = {
        "version": VERSION,
        "atr_period": ATR_PERIOD,
        "notional_inr": NOTIONAL_INR,
        "daily_bar_seconds": SECONDS_PER_DAY,
        "daily_session_timezone": "Asia/Kolkata",
        "duplicate_day_policy": "keep_last_bar_per_pair_and_ist_calendar_day",
        "atr_lookback": "14_contiguous_valid_calendar_sessions_only",
        "readiness": (
            "every_current_nonexcluded_pair_has_30_point_in_time_sessions_and_"
            "no_blocking_missing_or_stale_history"
        ),
        "entry": "next_calendar_daily_open_after_decision_close_plus_slippage",
        "same_bar_ambiguity": "stop_wins",
        "gap_policy": "gap_through_stop_exits_at_open_then_exit_slippage",
        "success": "target_hit_and_positive_after_cost_pnl",
        "configured_exclusions": sorted(excluded_codes),
        "geometries": [asdict(item) for item in geometries],
    }
    contract_sha = _json_hash(contract)
    active_sha = _json_hash(latest_event["active_codes"])
    config_path = root / "config/markets/crypto.yaml"
    config_sha = digest(config_path)
    implementation_sha = digest(Path(__file__))
    protocol_sha = _json_hash(
        {
            "contract_sha256": contract_sha,
            "configuration_sha256": config_sha,
            "implementation_sha256": implementation_sha,
        }
    )
    daily = _prepare_daily(daily_source, eligible_events, excluded_codes)
    latest_session = date.fromisoformat(str(daily["session"].max()))
    dataset_id = (
        f"{latest_session.isoformat()}-{source_sha[:12]}-"
        f"{active_sha[:8]}-{protocol_sha[:8]}"
    )
    target = output / "runs" / dataset_id
    manifest_path = target / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verification = verify_crypto_dataset(output, db_path, dataset_id=dataset_id)
        if not verification["passed"]:
            raise ValueError("existing crypto dataset failed integrity verification")
        write_json(output / "state.json", manifest)
        write_json(output / "latest.json", {"id": dataset_id, "path": str(manifest_path)})
        return manifest
    if target.exists():
        # A pre-atomic implementation or external interruption may have left a partial
        # directory. Preserve it for diagnosis but move it out of the publication path so
        # this deterministic run can be rebuilt without manual deletion.
        quarantine = target.parent / f".{dataset_id}.incomplete.{uuid4().hex}"
        target.replace(quarantine)

    coverage, missing = _coverage(
        daily,
        codes,
        latest_session,
        set(latest_event["active_codes"]),
        {
            str(code): int(count)
            for code, count in duplicate_rows.groupby("scrip_code").size().items()
        },
    )
    schedule = market.costs.schedule
    tds_share = tds_share_of_costs(
        maker_taker_pct=float(schedule.maker_taker_pct),
        tds_pct=float(schedule.tds_pct),
        gst_pct=float(schedule.gst_pct),
        slippage_pct=float(schedule.slippage_pct),
    )
    runs = output / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    temporary_target = runs / f".{dataset_id}.{uuid4().hex}.tmp"
    temporary_target.mkdir(parents=False, exist_ok=False)
    daily_path = temporary_target / "daily.parquet"
    missing_path = temporary_target / "missing_sessions.parquet"
    duplicate_path = temporary_target / "duplicate_daily_rows.parquet"
    labels_path = temporary_target / "labels.parquet"
    final_daily_path = target / daily_path.name
    final_missing_path = target / missing_path.name
    final_duplicate_path = target / duplicate_path.name
    final_labels_path = target / labels_path.name
    writer: _LabelWriter | None = None
    try:
        _write_parquet(daily, daily_path)
        _write_parquet(missing, missing_path)
        _write_parquet(duplicate_rows, duplicate_path)
        writer = _LabelWriter()
        for code, group in daily.groupby("scrip_code", sort=True):
            writer.append(
                _labels_for_code(
                    str(code),
                    group,
                    geometries,
                    costs=market.costs,
                    tds_share=tds_share,
                )
            )
        writer.save(labels_path)
        label_rows = writer.rows
        label_statuses = dict(writer.statuses)
    except Exception:
        if writer is not None:
            writer.close()
            writer = None
        shutil.rmtree(temporary_target, ignore_errors=True)
        raise
    finally:
        if writer is not None:
            writer.close()

    point_sessions = int(
        daily.loc[daily["point_in_time_eligible"], "session"].nunique()
    )
    required_codes = set(latest_event["active_codes"]) - set(excluded_codes)
    eligible_session_counts = (
        daily.loc[daily["point_in_time_eligible"]]
        .groupby("scrip_code")["session"]
        .nunique()
        .to_dict()
    )
    required_session_counts = {
        code: int(eligible_session_counts.get(code, 0)) for code in required_codes
    }
    minimum_pair_sessions = min(required_session_counts.values(), default=0)
    pairs_meeting_minimum = sum(
        sessions >= MIN_POINT_IN_TIME_SESSIONS
        for sessions in required_session_counts.values()
    )
    no_history = sum(row["status"] == "no_daily_history" for row in coverage)
    stale = sum(row["status"] == "stale" for row in coverage)
    blocking_no_history = sum(
        row["scrip_code"] in required_codes and row["status"] == "no_daily_history"
        for row in coverage
    )
    blocking_stale = sum(
        row["scrip_code"] in required_codes and row["status"] == "stale"
        for row in coverage
    )
    invalid = sum(row["invalid_ohlcv_sessions"] for row in coverage)
    status = (
        "dataset_ready_research_only"
        if required_codes
        and pairs_meeting_minimum == len(required_codes)
        and blocking_no_history == 0
        and blocking_stale == 0
        else "not_ready_collecting_point_in_time_history"
    )
    manifest = {
        "version": VERSION,
        "id": dataset_id,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "frozen_as_of": frozen_at.isoformat(),
        "latest_closed_session": latest_session.isoformat(),
        "source_sha256": source_sha,
        "contract": contract,
        "contract_sha256": contract_sha,
        "protocol_sha256": protocol_sha,
        "configuration": {
            "path": str(config_path),
            "sha256": config_sha,
        },
        "implementation": {"path": str(Path(__file__)), "sha256": implementation_sha},
        "universe_report": {
            "path": str(universe_report_path),
            "sha256": digest(Path(universe_report_path)),
        },
        "membership": {
            "pre_activation": "unknown_not_inferred",
            "activated_at": eligible_events[0]["observed_at"],
            "observations": len(eligible_events),
            "latest_event_sha256": latest_event["event_sha256"],
            "current_active_pairs": len(latest_event["active_codes"]),
            "ever_observed_active_pairs": len(codes),
            "point_in_time_sessions": point_sessions,
            "minimum_point_in_time_sessions": MIN_POINT_IN_TIME_SESSIONS,
            "required_active_pairs": len(required_codes),
            "pairs_meeting_minimum_sessions": pairs_meeting_minimum,
            "minimum_pair_point_in_time_sessions": minimum_pair_sessions,
            "added_since_previous": latest_event["added_since_previous"],
            "removed_since_previous": latest_event["removed_since_previous"],
        },
        "coverage_summary": {
            "materialized_pairs": len(codes),
            "closed_daily_rows": len(daily),
            "raw_closed_daily_rows": len(raw),
            "duplicate_daily_rows": len(duplicate_rows),
            "explicit_missing_sessions": len(missing),
            "invalid_ohlcv_sessions": invalid,
            "no_history_pairs": no_history,
            "stale_pairs": stale,
            "readiness_blocking_no_history_pairs": blocking_no_history,
            "readiness_blocking_stale_pairs": blocking_stale,
            "configured_excluded_pairs": sum(
                bool(row.get("configured_excluded")) for row in coverage
            ),
            "label_rows": label_rows,
            "label_statuses": label_statuses,
        },
        "coverage": coverage,
        "artifacts": {
            "daily": _artifact(
                daily_path, len(daily), published_path=final_daily_path
            ),
            "missing_sessions": _artifact(
                missing_path, len(missing), published_path=final_missing_path
            ),
            "duplicate_daily_rows": _artifact(
                duplicate_path,
                len(duplicate_rows),
                published_path=final_duplicate_path,
            ),
            "labels": _artifact(
                labels_path, label_rows, published_path=final_labels_path
            ),
        },
        "source_integrity": {"passed": True, "errors": []},
        "evidence_scope": "crypto_only_never_pooled_with_nse_or_bse",
        "baseline_improved": False,
        "eligible_for_live": False,
        "next_checkpoint": "C2_anticipatory_mechanism_and_quick_profit_geometry_race",
    }
    temporary_manifest_path = temporary_target / "manifest.json"
    try:
        write_json(temporary_manifest_path, manifest)
        temporary_target.replace(target)
    except Exception:
        shutil.rmtree(temporary_target, ignore_errors=True)
        raise
    write_json(output / "state.json", manifest)
    write_json(output / "latest.json", {"id": dataset_id, "path": str(manifest_path)})
    return manifest


def verify_crypto_dataset(
    output: Path = DEFAULT_OUTPUT,
    db_path: Path = DEFAULT_DB,
    *,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Verify frozen artifacts, universe chain, and the exact closed source rows."""
    output, db_path = Path(output), Path(db_path)
    errors: list[str] = []
    try:
        events = _read_observations(output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"passed": False, "errors": [str(exc)]}
    if dataset_id is None:
        latest_path = output / "latest.json"
        if not latest_path.exists():
            return {"passed": False, "errors": ["crypto dataset has no latest pointer"]}
        dataset_id = json.loads(latest_path.read_text(encoding="utf-8"))["id"]
    manifest_path = output / "runs" / dataset_id / "manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "errors": ["crypto dataset manifest is unavailable"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != VERSION:
        errors.append("dataset version changed")
    if _json_hash(manifest.get("contract")) != manifest.get("contract_sha256"):
        errors.append("geometry/label contract changed")
    protocol_sha = _json_hash(
        {
            "contract_sha256": manifest.get("contract_sha256"),
            "configuration_sha256": (manifest.get("configuration") or {}).get("sha256"),
            "implementation_sha256": (manifest.get("implementation") or {}).get("sha256"),
        }
    )
    if protocol_sha != manifest.get("protocol_sha256"):
        errors.append("dataset protocol identity changed")
    for name in ("configuration", "implementation"):
        frozen = manifest.get(name) or {}
        path = Path(frozen.get("path", ""))
        if not path.exists():
            errors.append(f"{name} source is missing")
        elif digest(path) != frozen.get("sha256"):
            errors.append(f"{name} source changed")
    for name, artifact in (manifest.get("artifacts") or {}).items():
        path = Path(artifact["path"])
        if not path.exists():
            errors.append(f"{name} artifact is missing")
        elif digest(path) != artifact.get("sha256"):
            errors.append(f"{name} artifact hash changed")
    event_hash = manifest.get("membership", {}).get("latest_event_sha256")
    if not any(event.get("event_sha256") == event_hash for event in events):
        errors.append("frozen universe observation is missing")
    codes = sorted(
        row["scrip_code"] for row in manifest.get("coverage", [])
    )
    cutoff_epoch = int(datetime.fromisoformat(manifest["frozen_as_of"]).timestamp())
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            current_raw = _closed_rows(con, codes, cutoff_epoch)
        current, _ = _canonicalize_daily_source(current_raw)
        if _source_hash(current) != manifest.get("source_sha256"):
            errors.append("closed source candles changed")
    except Exception as exc:
        errors.append(f"source verification failed: {type(exc).__name__}: {exc}")
    return {
        "passed": not errors,
        "errors": errors,
        "dataset_id": dataset_id,
        "source_sha256": manifest.get("source_sha256"),
    }
