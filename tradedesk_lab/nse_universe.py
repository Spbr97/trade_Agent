"""Whole-local-NSE, as-of daily research screening and a download-free coverage plan.

Intraday availability never determines membership or ranking. These are daily research
candidates, not executable calls or estimated success probabilities.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.broker.indstocks.ratelimit import DAILY_CAP
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT
from tradedesk_lab.aem_features import daily_snapshot as aem_snapshot
from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json
from tradedesk_lab.mcb_contract import DEFAULT_MCB_CONTRACT
from tradedesk_lab.mcb_features import daily_snapshot as mcb_snapshot


def _epoch(on: date, clock: time = time.min) -> int:
    return int(datetime.combine(on, clock, tzinfo=IST).timestamp())


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns}


def _instrument_reason(row: dict) -> str | None:
    if not row.get("exch"):
        return "missing_instrument_metadata"
    if row["exch"].upper() != "NSE" or not row["scrip_code"].startswith("NSE_"):
        return "not_nse"
    if str(row.get("kind", "")).lower() != "equity":
        return "not_cash_equity"
    if str(row.get("instrument_name", "")).upper() != "EQUITY":
        return "not_cash_equity"
    if str(row.get("series", "")).upper() != "EQ":
        return "not_eq_series"
    return None


def _calendar(con, cutoff: int, benchmark_symbol: str) -> tuple[list[date], str]:
    rows = con.execute(
        "SELECT c.ts FROM candles c JOIN instruments i USING (scrip_code) "
        "WHERE c.interval='1day' AND c.ts<? AND upper(i.exch)='NSE' "
        "AND lower(i.kind)='index' AND upper(i.trading_symbol)=upper(?) ORDER BY c.ts",
        [cutoff, benchmark_symbol],
    ).fetchall()
    source = f"benchmark:{benchmark_symbol}"
    if not rows:
        rows = con.execute(
            "SELECT DISTINCT c.ts FROM candles c JOIN instruments i USING (scrip_code) "
            "WHERE c.interval='1day' AND c.ts<? AND upper(i.exch)='NSE' "
            "AND lower(i.kind)='equity' AND upper(i.series)='EQ' "
            "AND upper(i.instrument_name)='EQUITY' ORDER BY c.ts",
            [cutoff],
        ).fetchall()
        source = "union_of_stored_nse_eq_daily_sessions"
    return sorted({datetime.fromtimestamp(row[0], IST).date() for row in rows}), source


def _daily_quality(frame: pd.DataFrame, calendar: list[date], on: date) -> str | None:
    if frame.empty:
        return "no_daily_history_as_of"
    dates = pd.DatetimeIndex(frame.index).date
    if dates[-1] != on:
        return "stale_daily_close"
    if len(set(dates)) != len(dates):
        return "duplicate_daily_session"
    numeric = frame[["open", "high", "low", "close", "volume"]]
    if not np.isfinite(numeric.to_numpy()).all():
        return "nonfinite_daily_data"
    prices = frame[["open", "high", "low", "close"]]
    if (prices <= 0).any().any() or (frame.volume < 0).any():
        return "invalid_daily_data"
    if (frame.high < prices.max(axis=1)).any() or (frame.low > prices.min(axis=1)).any():
        return "invalid_daily_ohlc"
    # Recent missing sessions cannot silently turn a 20-session liquidity window into
    # 20 observations spread across suspended or intermittently traded days.
    if set(calendar[-20:]) - set(dates):
        return "incomplete_recent_daily_sessions"
    if (frame.volume.iloc[-20:] <= 0).any():
        return "nontrading_recent_daily_session"
    return None


def _screen_frame(row: dict, frame: pd.DataFrame, calendar: list[date], on: date) -> dict:
    audit = {
        "scrip_code": row["scrip_code"],
        "symbol": row.get("trading_symbol") or row["scrip_code"],
        "daily_sessions_as_of": len(frame),
        "last_daily_session": str(frame.index[-1].date()) if len(frame) else None,
    }
    reason = _daily_quality(frame, calendar, on)
    if reason:
        return {**audit, "status": "rejected", "reasons": [reason]}
    strategies, failed, features = [], {}, {}
    for name, snapshot, contract in (
        ("AEM_v1", aem_snapshot, DEFAULT_AEM_CONTRACT),
        ("MCB_v1", mcb_snapshot, DEFAULT_MCB_CONTRACT),
    ):
        try:
            values = snapshot(frame, on, contract)
        except ValueError as error:
            failed[name] = [str(error)]
            continue
        features[name] = values
        failed[name] = [key for key, passed in values["checks"].items() if not passed]
        if values["eligible"]:
            strategies.append(name)
    if not strategies:
        return {
            **audit,
            "status": "rejected",
            "reasons": ["no_daily_strategy_match"],
            "strategy_rejections": failed,
        }
    liquidity = max(values["median_turnover_inr"] for values in features.values())
    return {
        **audit,
        "status": "daily_research_candidate",
        "reasons": [],
        "strategies": strategies,
        "strategy_rejections": failed,
        "features": features,
        "median_turnover_inr": liquidity,
        "decision": "WATCH",
        "eligible_for_live": False,
        "success_probability": None,
    }


def _coverage(con, code: str, interval: Interval, sessions: list[date]) -> dict:
    minutes = interval.seconds // 60
    expected_per_session = 375 // minutes
    rows = con.execute(
        "SELECT ts,open,high,low,close,volume FROM candles "
        "WHERE scrip_code=? AND interval=? AND ts>=? AND ts<? ORDER BY ts",
        [code, interval.value, _epoch(sessions[0]), _epoch(sessions[-1] + timedelta(days=1))],
    ).fetchall()
    valid_slots: dict[date, set[int]] = {on: set() for on in sessions}
    invalid_bars = 0
    latest = None
    for ts, op, high, low, close, volume in rows:
        when = datetime.fromtimestamp(ts, IST)
        slot_seconds = int((when - datetime.combine(when.date(), time(9, 15), IST)).total_seconds())
        numeric = np.array([op, high, low, close, volume], dtype=float)
        if (
            when.date() not in valid_slots
            or not np.isfinite(numeric).all()
            or min(op, high, low, close) <= 0
            or volume < 0
            or high < max(op, low, close)
            or low > min(op, high, close)
            or slot_seconds < 0
            or slot_seconds >= 375 * 60
            or slot_seconds % interval.seconds
        ):
            invalid_bars += 1
            continue
        valid_slots[when.date()].add(slot_seconds // interval.seconds)
        latest = when
    deficient = [on for on, slots in valid_slots.items() if len(slots) < expected_per_session]
    start = sessions[0]
    # Only windows containing missing/invalid expected slots need a fetch. One code
    # per request is conservative; retries/auth/other API activity are not included.
    windows = sorted({(on - start).days // interval.max_window_days for on in deficient})
    all_windows = math.ceil(((sessions[-1] - start).days + 1) / interval.max_window_days)
    return {
        "interval": interval.value,
        "requested_sessions": len(sessions),
        "bars_present": len(rows),
        "valid_regular_session_bars": sum(map(len, valid_slots.values())),
        "invalid_or_nonregular_bars": invalid_bars,
        "expected_regular_session_bars": len(sessions) * expected_per_session,
        "complete_sessions": len(sessions) - len(deficient),
        "missing_or_incomplete_sessions": [str(on) for on in deficient],
        "latest_valid_bar": latest.isoformat() if latest else None,
        "latest_session_complete": len(valid_slots[sessions[-1]]) == expected_per_session,
        "stale_as_of": latest is None or latest.date() < sessions[-1],
        "request_window_days": interval.max_window_days,
        "planned_missing_window_requests": len(windows),
        "full_span_request_upper_bound_before_retries": all_windows,
        "request_windows": [
            {
                "start": str(start + timedelta(days=n * interval.max_window_days)),
                "end_exclusive": str(
                    min(
                        start + timedelta(days=(n + 1) * interval.max_window_days),
                        sessions[-1] + timedelta(days=1),
                    )
                ),
            }
            for n in windows
        ],
    }


def run_nse_screen(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    as_of: date | str | None = None,
    shortlist_size: int = 50,
    backfill_sessions: int = 120,
    now: datetime | None = None,
    benchmark_symbol: str = "NIFTY 50",
) -> dict:
    """Screen every locally stored NSE cash EQ stock at a completed daily close.

    A weekend cutoff selects the latest stored benchmark session on/before that date.
    Historical membership uses candles then available; the current instrument master
    is not versioned, so historical metadata/survivorship bias remains explicit.
    """
    if not 1 <= shortlist_size <= 500:
        raise ValueError("shortlist_size must be between 1 and 500")
    if not 1 <= backfill_sessions <= 1500:
        raise ValueError("backfill_sessions must be between 1 and 1500")
    when = now or datetime.now(IST)
    when = when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)
    latest_completed_day = when.date() - timedelta(days=when.time() < time(15, 30))
    requested = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    requested = requested or latest_completed_day
    if requested > latest_completed_day:
        raise ValueError("as_of must refer to a completed daily close in Asia/Kolkata")
    cutoff = _epoch(requested + timedelta(days=1))
    database = Path(root) / "data/tradedesk.duckdb"
    run_id = uuid4().hex
    folder = Path(output) / "nse_universe/runs" / run_id
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "artifact_path": str(folder / "report.json"),
        "database": str(database),
        "status": "historical_daily_screen_only",
        "eligible_for_live": False,
        "requested_as_of": str(requested),
        "ranking": "median 20-session turnover descending; symbol/code tie-break; diagnostic only",
        "shortlist_size_requested": shortlist_size,
        "backfill_sessions_requested": backfill_sessions,
        "contracts": {
            "AEM_v1": DEFAULT_AEM_CONTRACT.sha256,
            "MCB_v1": DEFAULT_MCB_CONTRACT.sha256,
        },
        "limitations": [
            "Local NSE cash EQ coverage is not proof of a complete exchange listing archive.",
            "Instrument metadata is not historically versioned; survivorship bias remains.",
            "Suspension, surveillance, spreads, circuits and future tradability are unverified.",
            "Stored adjusted history may be revised; this is an as-of slice, not a data vintage.",
            "Ranking is a liquidity diagnostic, not a calibrated success probability.",
            "Coverage assumes regular 09:15-15:30 sessions; special sessions need separate review.",
            "Backfill is a plan only: no API, credentials, training or order placement is invoked.",
        ],
    }
    try:
        report["source_before"] = _fingerprint(database)
        with duckdb.connect(str(database), read_only=True) as con:
            con.execute("BEGIN TRANSACTION")
            _populate_report(
                con, report, cutoff, requested, benchmark_symbol, shortlist_size, backfill_sessions
            )
            con.execute("ROLLBACK")
        report["source_after"] = _fingerprint(database)
        report["source_file_changed_during_read"] = (
            report["source_before"] != report["source_after"]
        )
    except (OSError, duckdb.Error) as error:
        report.update(
            status="blocked_source_unavailable",
            error=str(error),
            recovery="Retry after the local data writer releases DuckDB; no source copy was made.",
        )
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "report.json", report)
    return report


def _populate_report(
    con, report, cutoff, requested, benchmark_symbol, shortlist_size, backfill_sessions
):
    calendar, calendar_source = _calendar(con, cutoff, benchmark_symbol)
    if not calendar:
        report.update(status="blocked_no_daily_calendar", summary={"daily_candidates": 0})
        return
    on = calendar[-1]
    cutoff = _epoch(on + timedelta(days=1))
    report.update(
        as_of=str(on),
        calendar_source=calendar_source,
        source_lag_calendar_days=(requested - on).days,
    )
    metadata = (
        con.execute(
            "WITH d AS (SELECT DISTINCT scrip_code FROM candles WHERE interval='1day' AND ts<?) "
            "SELECT coalesce(i.scrip_code,d.scrip_code) AS scrip_code, "
            "i.exch,i.kind,i.trading_symbol,i.series,i.instrument_name "
            "FROM instruments i FULL OUTER JOIN d USING (scrip_code) ORDER BY scrip_code",
            [cutoff],
        )
        .df()
        .replace({np.nan: None})
        .to_dict("records")
    )
    audit, permitted = [], []
    for row in metadata:
        reason = _instrument_reason(row)
        if reason:
            audit.append(
                {
                    "scrip_code": row["scrip_code"],
                    "symbol": row["trading_symbol"],
                    "status": "rejected",
                    "reasons": [reason],
                }
            )
        else:
            permitted.append(row)
    for offset in range(0, len(permitted), 100):
        batch = permitted[offset : offset + 100]
        placeholders = ",".join("?" for _ in batch)
        data = con.execute(
            "SELECT scrip_code,ts,open,high,low,close,volume FROM candles "
            f"WHERE interval='1day' AND ts<? AND scrip_code IN ({placeholders}) "
            "ORDER BY scrip_code,ts",
            [cutoff, *[row["scrip_code"] for row in batch]],
        ).df()
        data.index = pd.DatetimeIndex(
            pd.to_datetime(data.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        )
        grouped = {
            code: group.drop(columns="scrip_code")
            for code, group in data.groupby("scrip_code", sort=False)
        }
        for row in batch:
            frame = grouped.get(row["scrip_code"], data.iloc[:0].drop(columns="scrip_code"))
            audit.append(_screen_frame(row, frame, calendar, on))
    audit.sort(key=lambda row: row["scrip_code"])
    candidates = sorted(
        [row for row in audit if row["status"] == "daily_research_candidate"],
        key=lambda row: (-row["median_turnover_inr"], row["symbol"], row["scrip_code"]),
    )
    for rank, row in enumerate(candidates, 1):
        row["diagnostic_rank"] = rank
    shortlist = candidates[:shortlist_size]
    sessions = calendar[-backfill_sessions:]
    coverage = [
        {
            "scrip_code": row["scrip_code"],
            "symbol": row["symbol"],
            "intervals": [
                _coverage(con, row["scrip_code"], interval, sessions)
                for interval in (Interval.M1, Interval.M5)
            ],
        }
        for row in shortlist
    ]
    checks = [interval for row in coverage for interval in row["intervals"]]
    planned = sum(row["planned_missing_window_requests"] for row in checks)
    upper = sum(row["full_span_request_upper_bound_before_retries"] for row in checks)
    report.update(
        summary={
            "instrument_records_considered": len(metadata),
            "nse_cash_eq_instruments_considered": len(permitted),
            "daily_candidates": len(candidates),
            "shortlisted": len(shortlist),
            "rejected": len(audit) - len(candidates),
            "rejection_reasons": dict(
                Counter(reason for row in audit for reason in row["reasons"])
            ),
            "strategy_candidates": dict(
                Counter(name for row in candidates for name in row["strategies"])
            ),
        },
        candidates=candidates,
        shortlist=shortlist,
        instrument_audit=audit,
        coverage=coverage,
        backfill_plan={
            "mode": "plan_only_no_network",
            "start": str(sessions[0]),
            "end_exclusive": str(on + timedelta(days=1)),
            "available_calendar_sessions": len(sessions),
            "calendar_shortfall_sessions": max(0, backfill_sessions - len(sessions)),
            "planned_missing_window_requests": planned,
            "full_span_request_upper_bound_before_retries": upper,
            "api_daily_cap": DAILY_CAP,
            "minimum_daily_budgets_for_full_span": math.ceil(upper / DAILY_CAP),
            "assumptions": "One code/request; interval caps from Interval.max_window_days; "
            "excludes retry/auth overhead and other consumers of the daily budget.",
        },
    )
    deterministic = {key: report[key] for key in ("as_of", "contracts", "candidates", "coverage")}
    report["screen_sha256"] = hashlib.sha256(
        json.dumps(deterministic, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
