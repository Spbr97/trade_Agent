"""Join frozen staged M1 candles to read-only daily/benchmark research inputs."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_data import IST, frame_fingerprint, regular_minutes
from tradedesk_lab.aem_history import _collection_lock, load_collection_plan
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest


def _frame(rows) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    if frame.empty:
        frame.index = pd.DatetimeIndex([], tz=IST)
        return frame.drop(columns="ts")
    frame.index = pd.DatetimeIndex(
        pd.to_datetime(frame.pop("ts"), unit="s", utc=True).dt.tz_convert(IST)
    )
    if frame.index.hasnans or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("source candles require unique ordered timestamps")
    return frame


def _complete_regular_sessions(frame: pd.DataFrame) -> set[date]:
    """Return complete regular sessions after one linear scan of the frame."""

    local = pd.DatetimeIndex(frame.index).tz_convert(IST)
    positions: dict[date, list[int]] = {}
    for position, day in enumerate(local.date):
        positions.setdefault(day, []).append(position)
    complete = set()
    for day, selected in positions.items():
        if len(selected) != 375:
            continue
        expected = pd.date_range(
            datetime.combine(day, time(9, 15), IST), periods=375, freq="min"
        )
        if local.take(selected).equals(expected):
            complete.add(day)
    return complete


def read_staged_aem_source(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    plan_id: str,
    contract: AemContract,
    benchmark_symbol: str,
) -> tuple[dict, dict, dict, list[date], list[date], dict]:
    """Read the pinned cohort without selecting symbols from later M1 availability.

    Daily candles and benchmark dates come from a single read-only production snapshot.
    Minute candles come from the insert-only lab stage while holding its collector lock.
    A stage gap stays in the returned frame and is listed in the source audit; downstream
    reconstruction must reject that session rather than manufacturing bars.
    """
    root, output = Path(root).resolve(), Path(output).resolve()
    plan, plan_artifact_sha256 = load_collection_plan(output, plan_id)
    if plan["contract_sha256"] != contract.sha256:
        raise ValueError("staged plan and AEM contract differ")
    warmup = [date.fromisoformat(value) for value in plan["warmup_dates"]]
    evaluation = [date.fromisoformat(value) for value in plan["evaluation_dates"]]
    sessions = warmup + evaluation
    codes = [row["scrip_code"] for row in plan["shortlist"]]
    expected_symbols = {row["scrip_code"]: row["symbol"] for row in plan["shortlist"]}
    stage_folder = output / "aem_history" / plan_id
    stage_database = stage_folder / "candles.duckdb"
    state_path = stage_folder / "state.json"
    if not stage_database.is_file() or not state_path.is_file():
        raise ValueError("staged candles and collection state are required")
    if stage_database.is_symlink() or state_path.is_symlink():
        raise ValueError("staged source aliases are prohibited")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("plan_artifact_sha256") != plan_artifact_sha256:
        raise ValueError("collection state does not pin the frozen plan")
    pending = [item for item in state.get("attempts", []) if item.get("status") == "pending"]
    if pending:
        raise ValueError("collection has an unconfirmed pending request")

    end = evaluation[-1]
    upper = int(datetime.combine(end + timedelta(days=1), time(), IST).timestamp())
    production = root / "data/tradedesk.duckdb"
    daily: dict[str, pd.DataFrame] = {}
    symbols: dict[str, str] = {}
    daily_fingerprints = {}
    with duckdb.connect(str(production), read_only=True) as con:
        con.execute("BEGIN TRANSACTION")
        benchmark = con.execute(
            "SELECT scrip_code FROM instruments WHERE trading_symbol=? AND exch='NSE' "
            "AND kind='index' ORDER BY scrip_code",
            [benchmark_symbol],
        ).fetchall()
        if len(benchmark) != 1:
            raise ValueError("missing_or_ambiguous_benchmark")
        calendar_rows = con.execute(
            "SELECT ts FROM candles WHERE scrip_code=? AND interval='1day' AND ts<? ORDER BY ts",
            [benchmark[0][0], upper],
        ).fetchall()
        calendar = sorted({datetime.fromtimestamp(row[0], IST).date() for row in calendar_rows})
        if [day for day in calendar if sessions[0] <= day <= sessions[-1]] != sessions:
            raise ValueError("current benchmark calendar differs from the frozen plan")
        placeholders = ",".join("?" for _ in codes)
        metadata = con.execute(
            "SELECT scrip_code,trading_symbol,exch,kind,series,instrument_name "
            f"FROM instruments WHERE scrip_code IN ({placeholders}) ORDER BY scrip_code",
            codes,
        ).fetchall()
        if len(metadata) != len(codes):
            raise ValueError("selected instrument metadata is missing")
        for code, symbol, exch, kind, series, instrument_name in metadata:
            if (
                exch != "NSE"
                or kind != "equity"
                or series != "EQ"
                or str(instrument_name).upper() != "EQUITY"
                or expected_symbols[code] != symbol
            ):
                raise ValueError(f"selected instrument metadata changed:{code}")
            rows = con.execute(
                "SELECT ts,open,high,low,close,volume FROM candles WHERE scrip_code=? "
                "AND interval='1day' AND ts<? ORDER BY ts",
                [code, upper],
            ).fetchall()
            daily[code] = _frame(rows)
            symbols[code] = symbol
            daily_fingerprints[code] = frame_fingerprint(daily[code])
        con.execute("ROLLBACK")

    lower = int(datetime.combine(sessions[0], time(), IST).timestamp())
    minute: dict[str, pd.DataFrame] = {}
    minute_fingerprints = {}
    coverage = {}
    with _collection_lock(output / "aem_history"):
        with duckdb.connect(str(stage_database), read_only=True) as con:
            marker = con.execute(
                "SELECT value FROM aem_stage_meta WHERE key='schema_version'"
            ).fetchone()
            if marker != ("1",):
                raise ValueError("unsupported staged candle schema")
            for code in codes:
                rows = con.execute(
                    "SELECT ts,open,high,low,close,volume FROM candles WHERE scrip_code=? "
                    "AND interval='1minute' AND ts>=? AND ts<? ORDER BY ts",
                    [code, lower, upper],
                ).fetchall()
                minute[code] = regular_minutes(_frame(rows))
                minute_fingerprints[code] = frame_fingerprint(minute[code])
                complete = _complete_regular_sessions(minute[code])
                missing = [str(day) for day in sessions if day not in complete]
                coverage[code] = {
                    "complete_sessions": len(sessions) - len(missing),
                    "missing_or_incomplete_sessions": missing,
                }

    source = {
        "source_version": "aem-staged-preperiod-m1-v1",
        "as_of": str(end),
        "benchmark": benchmark_symbol,
        "benchmark_code": benchmark[0][0],
        "benchmark_calendar": [str(day) for day in calendar],
        "benchmark_calendar_sha256": hashlib.sha256(
            json.dumps([str(day) for day in calendar], separators=(",", ":")).encode()
        ).hexdigest(),
        "warmup_dates": [str(day) for day in warmup],
        "evaluation_dates": [str(day) for day in evaluation],
        "minute_history_policy": (
            "Frozen twenty-session preperiod warmup plus 120 evaluation sessions; incomplete "
            "sessions are retained for explicit downstream rejection."
        ),
        "rvol_lookback_observations_per_slot": contract.rvol_lookback_sessions,
        "rvol_minimum_observations_per_slot": contract.rvol_min_sessions,
        "historical_constituents_available": False,
        "plan_id": plan_id,
        "plan_artifact_sha256": plan_artifact_sha256,
        "selection_sha256": plan["selection_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "collection_state_sha256": digest(state_path),
        "stage_database": str(stage_database),
        "production_daily_database": str(production),
        "requests_recorded": state["requests_recorded"],
        "coverage": coverage,
        "symbols": {
            code: {
                "symbol": symbols[code],
                "daily": daily_fingerprints[code],
                "minute": minute_fingerprints[code],
            }
            for code in codes
        },
        "limitations": [
            "Current instrument metadata is not historical membership; survivorship bias remains.",
            "Daily and staged minute sources are separately consistent snapshots, "
            "not one transaction.",
            "The bounded M1 warmup differs from the prior full-history development "
            "source contract.",
            "Revised historical candles cannot reproduce their original availability vintage.",
        ],
    }
    source["sha256"] = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return daily, minute, symbols, calendar, evaluation, source
