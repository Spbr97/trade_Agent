"""Prospective same-coin random-timing evidence for crypto accuracy work.

This collector activates from the existing live crypto log, excludes every pre-activation
call, and registers later calls before any control entry.  Each setup is evaluated
separately; crypto evidence is never pooled with NSE/BSE or across setup mechanisms.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk.config import load_config
from tradedesk.engine.indicators import atr
from tradedesk.markets import crypto_market
from tradedesk_lab.accuracy_prospective_shadow import _quick_outcome
from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json

VERSION = "crypto-accuracy-timing-v1"
DEFAULT_OUTPUT = OUTPUT / "crypto_accuracy_timing"
DEFAULT_LOG = ROOT / "data/reports/crypto_signal_tracking.jsonl"
DEFAULT_DB = ROOT / "data/crypto.duckdb"
SEED = 20261006
N_COHORTS = 1000
MIN_OFFSET = 1
MAX_OFFSET = 20
MODEL_OFFSET = 0
MAX_HOLD = 10
NOTIONAL_INR = 10_000.0
MIN_CALLS = 100
MIN_SESSIONS = 30
MIN_ADVANTAGE_R = 0.10
MAX_P_VALUE = 0.05


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


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
            raise RuntimeError("another crypto timing collector owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        row.setdefault("source", "backfill")
    return rows


def _load_crypto_bars(
    con: duckdb.DuckDBPyConnection, code: str
) -> pd.DataFrame:
    """Load daily crypto candles without erasing their 05:30 IST open time."""
    frame = con.execute(
        "SELECT ts,open,high,low,close,volume FROM candles "
        "WHERE scrip_code=? AND interval='1day' ORDER BY ts",
        [code],
    ).df()
    if frame.empty:
        return frame
    frame.index = pd.to_datetime(frame.pop("ts"), unit="s", utc=True).dt.tz_convert(
        "Asia/Kolkata"
    )
    frame["volume"] = frame.volume.astype("int64")
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _definition(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "signal_id",
            "scrip_code",
            "symbol",
            "setup",
            "armed_on",
            "entry",
            "stop",
            "t1",
            "source",
            "logged_at",
        )
    }


def _record_seed(signal_id: str) -> int:
    digest = hashlib.sha256(f"{SEED}:{signal_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**32)


def freeze_offsets(signal_id: str, n_cohorts: int = N_COHORTS) -> list[int]:
    rng = np.random.default_rng(_record_seed(signal_id))
    return [
        int(value)
        for value in rng.integers(MIN_OFFSET, MAX_OFFSET + 1, size=n_cohorts)
    ]


def _frame_hash(frame: pd.DataFrame) -> str:
    rows = [
        [
            stamp.isoformat(),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
        ]
        for stamp, row in frame[["open", "high", "low", "close"]].iterrows()
    ]
    return _hash(rows)


def _closed_bars(frame: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of)
    closes_at = frame.index + pd.Timedelta(days=1)
    return frame.loc[closes_at <= cutoff]


def _new_record(
    row: dict[str, Any],
    bars: pd.DataFrame,
    assigned_at: datetime,
    activated_at: datetime,
) -> dict[str, Any]:
    logged_at = datetime.fromisoformat(row["logged_at"])
    armed = date.fromisoformat(row["armed_on"])
    known = bars.loc[bars.index.date <= armed]
    atr_value = float(atr(known, 14).iloc[-1]) if not known.empty else float("nan")
    risk = float(row["entry"]) - float(row["stop"])
    target_r = (
        (float(row["t1"]) - float(row["entry"])) / risk if risk > 0 else float("nan")
    )
    stop_atr = risk / atr_value if atr_value > 0 else float("nan")
    prospective = bool(
        row.get("source") == "live"
        and row.get("outcome") is None
        and logged_at > activated_at
        and np.isfinite(stop_atr)
        and stop_atr > 0
        and np.isfinite(target_r)
        and target_r > 0
    )
    offsets = freeze_offsets(row["signal_id"])
    timings = [
        {
            "offset_sessions": offset,
            "status": "pending" if prospective else "excluded_registration",
            "entry_date": None,
            "fill_price": None,
            "atr": None,
            "stop": None,
            "target": None,
            "qty": None,
            "label": None,
            "net_r": None,
            "outcome": None,
            "exit_price": None,
            "exit_date": None,
            "source_sha256": None,
            "resolved_at": None,
        }
        for offset in range(MODEL_OFFSET, MAX_OFFSET + 1)
    ]
    record = {
        **_definition(row),
        "assigned_at": assigned_at.isoformat(),
        "prospective_eligible": prospective,
        "definition_sha256": _hash(_definition(row)),
        "armed_atr": atr_value if np.isfinite(atr_value) else None,
        "stop_atr": stop_atr if np.isfinite(stop_atr) else None,
        "target_r": target_r if np.isfinite(target_r) else None,
        "cohort_offsets": offsets,
        "timings": timings,
    }
    record["assignment_sha256"] = _hash(
        {
            "signal_id": record["signal_id"],
            "definition_sha256": record["definition_sha256"],
            "assigned_at": record["assigned_at"],
            "stop_atr": record["stop_atr"],
            "target_r": record["target_r"],
            "cohort_offsets": offsets,
        }
    )
    return record


def _verify_record(record: dict[str, Any], row: dict[str, Any] | None) -> None:
    if row is None:
        raise ValueError(f"crypto call disappeared for {record['signal_id']}")
    if _hash(_definition(row)) != record["definition_sha256"]:
        raise ValueError(f"crypto call definition changed for {record['signal_id']}")
    expected = _hash(
        {
            "signal_id": record["signal_id"],
            "definition_sha256": record["definition_sha256"],
            "assigned_at": record["assigned_at"],
            "stop_atr": record["stop_atr"],
            "target_r": record["target_r"],
            "cohort_offsets": record["cohort_offsets"],
        }
    )
    if expected != record["assignment_sha256"]:
        raise ValueError(f"crypto timing assignment changed for {record['signal_id']}")
    if len(record["cohort_offsets"]) != N_COHORTS or not all(
        MIN_OFFSET <= int(offset) <= MAX_OFFSET
        for offset in record["cohort_offsets"]
    ):
        raise ValueError(f"crypto timing offsets invalid for {record['signal_id']}")


def _resolve_timing(
    timing: dict[str, Any],
    record: dict[str, Any],
    bars: pd.DataFrame,
    *,
    as_of: datetime,
    costs: Any,
) -> bool:
    if timing["status"] == "resolved":
        exit_on = date.fromisoformat(timing["exit_date"])
        closed = _closed_bars(bars, as_of)
        source = closed.loc[closed.index.date <= exit_on]
        if _frame_hash(source) != timing["source_sha256"]:
            raise ValueError(
                f"resolved crypto timing source changed at offset {timing['offset_sessions']}"
            )
        return False
    if timing["status"] != "pending":
        return False
    closed = _closed_bars(bars, as_of)
    assigned = pd.Timestamp(record["assigned_at"])
    future_entries = closed.loc[closed.index > assigned]
    offset = int(timing["offset_sessions"])
    if len(future_entries) <= offset + MAX_HOLD:
        return False
    entry_stamp = future_entries.index[offset]
    through_decision = closed.loc[closed.index < entry_stamp]
    atr_value = float(atr(through_decision, 14).iloc[-1])
    if not np.isfinite(atr_value) or atr_value <= 0:
        timing.update(
            status="excluded_invalid_atr",
            outcome="invalid_atr",
            resolved_at=_now().isoformat(),
        )
        return True
    future = closed.loc[closed.index >= entry_stamp]
    fill = float(future.iloc[0].open) * (1 + float(costs.slippage_pct))
    stop = fill - float(record["stop_atr"]) * atr_value
    target = fill + float(record["target_r"]) * (fill - stop)
    qty = NOTIONAL_INR / fill
    result = _quick_outcome(
        future,
        fill=fill,
        stop=stop,
        target=target,
        max_hold=MAX_HOLD,
        qty=qty,
        costs=costs,
    )
    if result is None:
        return False
    label, net_r, exit_price, exit_on, outcome = result
    source = closed.loc[closed.index.date <= exit_on]
    timing.update(
        status="resolved",
        entry_date=pd.Timestamp(entry_stamp).date().isoformat(),
        fill_price=fill,
        atr=atr_value,
        stop=stop,
        target=target,
        qty=qty,
        label=label,
        net_r=net_r,
        outcome=outcome,
        exit_price=exit_price,
        exit_date=exit_on.isoformat(),
        source_sha256=_frame_hash(source),
        resolved_at=_now().isoformat(),
    )
    return True


def _setup_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    mature = []
    excluded = 0
    for record in records:
        if not record.get("prospective_eligible"):
            continue
        statuses = {timing["status"] for timing in record["timings"]}
        if any(status.startswith("excluded_") for status in statuses):
            excluded += 1
            continue
        if statuses == {"resolved"}:
            mature.append(record)
    model_labels: list[int] = []
    model_returns: list[float] = []
    cohort_labels: list[list[int]] = [[] for _ in range(N_COHORTS)]
    cohort_returns: list[list[float]] = [[] for _ in range(N_COHORTS)]
    for record in mature:
        timings = {
            int(timing["offset_sessions"]): timing for timing in record["timings"]
        }
        model = timings[MODEL_OFFSET]
        model_labels.append(int(model["label"]))
        model_returns.append(float(model["net_r"]))
        for index, offset in enumerate(record["cohort_offsets"]):
            control = timings[int(offset)]
            cohort_labels[index].append(int(control["label"]))
            cohort_returns[index].append(float(control["net_r"]))
    cohort_mean_r = np.asarray(
        [np.mean(values) if values else np.nan for values in cohort_returns], dtype=float
    )
    cohort_accuracy = np.asarray(
        [np.mean(values) if values else np.nan for values in cohort_labels], dtype=float
    )
    model_mean = float(np.mean(model_returns)) if model_returns else None
    random_mean = (
        float(np.nanmean(cohort_mean_r)) if np.isfinite(cohort_mean_r).any() else None
    )
    advantage = (
        model_mean - random_mean
        if model_mean is not None and random_mean is not None
        else None
    )
    finite = cohort_mean_r[np.isfinite(cohort_mean_r)]
    p_value = (
        float((np.sum(finite >= model_mean) + 1) / (len(finite) + 1))
        if model_mean is not None and len(finite)
        else None
    )
    sessions = len({record["armed_on"] for record in mature})
    checks = {
        "paired_calls": len(mature) >= MIN_CALLS,
        "active_sessions": sessions >= MIN_SESSIONS,
        "advantage_r": advantage is not None and advantage >= MIN_ADVANTAGE_R,
        "p_value": p_value is not None and p_value <= MAX_P_VALUE,
    }
    enough = checks["paired_calls"] and checks["active_sessions"]
    status = (
        "random_timing_pass"
        if enough and all(checks.values())
        else "random_timing_fail" if enough else "collecting_insufficient_evidence"
    )
    return {
        "status": status,
        "registered_calls": sum(
            record.get("prospective_eligible") is True for record in records
        ),
        "paired_calls": len(mature),
        "excluded_calls": excluded,
        "active_sessions": sessions,
        "model_accuracy": float(np.mean(model_labels)) if model_labels else None,
        "model_expectancy_r": model_mean,
        "random_timing_accuracy_mean": (
            float(np.nanmean(cohort_accuracy))
            if np.isfinite(cohort_accuracy).any()
            else None
        ),
        "random_timing_expectancy_r_mean": random_mean,
        "timing_advantage_r": advantage,
        "p_value": p_value,
        "gate_checks": checks,
    }


def summarize_timing(state: dict[str, Any]) -> dict[str, Any]:
    by_setup_records: dict[str, list[dict[str, Any]]] = {}
    for record in state.get("records", []):
        by_setup_records.setdefault(record["setup"], []).append(record)
    by_setup = [
        {"setup": setup, **_setup_summary(records)}
        for setup, records in sorted(by_setup_records.items())
    ]
    qualified = [
        row["setup"] for row in by_setup if row["status"] == "random_timing_pass"
    ]
    enough = any(
        row["gate_checks"]["paired_calls"] and row["gate_checks"]["active_sessions"]
        for row in by_setup
    )
    return {
        "status": (
            "candidate_timing_pass"
            if qualified
            else "candidate_timing_fail" if enough else "collecting_insufficient_evidence"
        ),
        "registered_calls": sum(
            record.get("prospective_eligible") is True
            for record in state.get("records", [])
        ),
        "setups_monitored": len(by_setup),
        "qualified_setups": qualified,
        "by_setup": by_setup,
        "evidence_pooled_across_setups": False,
        "n_cohorts": N_COHORTS,
        "offset_sessions": [MIN_OFFSET, MAX_OFFSET],
        "max_hold": MAX_HOLD,
        "control_scope": "same_coin_random_future_daily_timing",
        "eligible_for_live": False,
        "updated_at": _now().isoformat(),
    }


def collect_crypto_timing(
    root: Path = ROOT,
    output: Path = DEFAULT_OUTPUT,
    log_path: Path = DEFAULT_LOG,
    db_path: Path = DEFAULT_DB,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Activate or update crypto's forward-only, setup-separated timing evidence."""
    run_at = as_of or _now()
    with _lock(output):
        rows = _load_rows(log_path)
        live_rows = {row["signal_id"]: row for row in rows if row["source"] == "live"}
        state_path = output / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                state.get("version") != VERSION
                or state.get("seed") != SEED
                or state.get("n_cohorts") != N_COHORTS
            ):
                raise ValueError("crypto timing state does not match frozen protocol")
        else:
            baseline_ids = sorted(live_rows)
            activation = {
                "activated_at": run_at.isoformat(),
                "existing_live_signal_ids": baseline_ids,
                "existing_live_signal_ids_sha256": _hash(baseline_ids),
                "existing_live_calls": len(baseline_ids),
                "forward_only": True,
            }
            state = {
                "version": VERSION,
                "registered_at": run_at.isoformat(),
                "seed": SEED,
                "n_cohorts": N_COHORTS,
                "offset_sessions": [MIN_OFFSET, MAX_OFFSET],
                "scope": "prospective_same_coin_random_future_daily_timing",
                "activation": activation,
                "records": [],
                "errors": [],
            }
        activation = state["activation"]
        baseline_ids = activation["existing_live_signal_ids"]
        if _hash(baseline_ids) != activation["existing_live_signal_ids_sha256"]:
            raise ValueError("crypto timing activation IDs changed")
        if not set(baseline_ids).issubset(live_rows):
            raise ValueError("pre-activation crypto calls disappeared")
        existing = {record["signal_id"] for record in state["records"]}
        activated_at = datetime.fromisoformat(activation["activated_at"])
        settings = load_config(root)
        market = crypto_market(settings)
        errors = []
        cache: dict[str, pd.DataFrame] = {}
        with duckdb.connect(str(db_path), read_only=True) as con:

            def bars(code: str) -> pd.DataFrame:
                if code not in cache:
                    cache[code] = _load_crypto_bars(con, code)
                return cache[code]

            for signal_id, row in sorted(live_rows.items()):
                if signal_id in baseline_ids or signal_id in existing:
                    continue
                state["records"].append(
                    _new_record(row, bars(row["scrip_code"]), run_at, activated_at)
                )
            for record in state["records"]:
                try:
                    _verify_record(record, live_rows.get(record["signal_id"]))
                    frame = bars(record["scrip_code"])
                    for timing in record["timings"]:
                        _resolve_timing(
                            timing,
                            record,
                            frame,
                            as_of=run_at,
                            costs=market.costs,
                        )
                except Exception as exc:
                    errors.append(
                        {
                            "signal_id": record["signal_id"],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        state["errors"] = errors
        state["records"].sort(key=lambda row: (row["armed_on"], row["signal_id"]))
        state["summary"] = summarize_timing(state)
        state["source_integrity"] = {"passed": not errors, "errors": len(errors)}
        if errors:
            state["summary"]["status"] = "degraded"
        write_json(state_path, state)
        return state
