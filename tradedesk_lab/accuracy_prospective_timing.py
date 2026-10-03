"""M11 prospective same-stock random-timing control for the frozen M8 candidate.

Daily bars do not provide multiple valid entry times inside one session.  Therefore each
selected M8 call receives 1,000 frozen placebo timings drawn from the next 1-20 NSE
sessions on the same stock.  Placebos use causal ATR, the exact M8 geometry, sizing and
costs, and are assigned before the model entry can occur.
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
from tradedesk.markets.market import nse_market
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.accuracy_prospective_shadow import (
    DEFAULT_OUTPUT as SHADOW_OUTPUT,
)
from tradedesk_lab.accuracy_prospective_shadow import _index_codes, _load_bars, _quick_outcome
from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json

VERSION = "accuracy-prospective-timing-v1"
DEFAULT_OUTPUT = OUTPUT / "accuracy_prospective_timing"
STATE_PATH = SHADOW_OUTPUT / "state.json"
SEED = 20261005
N_COHORTS = 1000
MIN_OFFSET = 1
MAX_OFFSET = 20
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
            raise RuntimeError("another M11 timing collector owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _record_seed(signal_id: str) -> int:
    digest = hashlib.sha256(f"{SEED}:{signal_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**32)


def freeze_offsets(signal_id: str, n_cohorts: int = N_COHORTS) -> list[int]:
    """Freeze 1-20-session placebo timings before the model call can enter."""
    rng = np.random.default_rng(_record_seed(signal_id))
    return [
        int(value)
        for value in rng.integers(MIN_OFFSET, MAX_OFFSET + 1, size=n_cohorts)
    ]


def _new_record(m8_record: dict[str, Any], assigned_at: datetime) -> dict[str, Any]:
    deadline = datetime.fromisoformat(m8_record["score_deadline"])
    prospective = bool(
        m8_record.get("selected") is True
        and m8_record.get("prospective_eligible") is True
        and assigned_at < deadline
    )
    offsets = freeze_offsets(m8_record["signal_id"])
    placebos = [
        {
            "offset_sessions": offset,
            "status": "pending" if prospective else "excluded_late",
            "decision_date": None,
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
        for offset in range(MIN_OFFSET, MAX_OFFSET + 1)
    ]
    record = {
        "signal_id": m8_record["signal_id"],
        "scrip_code": m8_record["scrip_code"],
        "symbol": m8_record["symbol"],
        "armed_on": m8_record["armed_on"],
        "assigned_at": assigned_at.isoformat(),
        "score_deadline": deadline.isoformat(),
        "prospective_eligible": prospective,
        "prediction_sha256": m8_record["prediction_sha256"],
        "cohort_offsets": offsets,
        "placebos": placebos,
    }
    record["assignment_sha256"] = _hash(
        {
            "signal_id": record["signal_id"],
            "scrip_code": record["scrip_code"],
            "armed_on": record["armed_on"],
            "prediction_sha256": record["prediction_sha256"],
            "cohort_offsets": offsets,
        }
    )
    return record


def _verify_record(record: dict[str, Any], m8_record: dict[str, Any] | None) -> None:
    expected = _hash(
        {
            "signal_id": record["signal_id"],
            "scrip_code": record["scrip_code"],
            "armed_on": record["armed_on"],
            "prediction_sha256": record["prediction_sha256"],
            "cohort_offsets": record["cohort_offsets"],
        }
    )
    if expected != record["assignment_sha256"]:
        raise ValueError(f"M11 timing assignment changed for {record['signal_id']}")
    if m8_record is None:
        raise ValueError(f"M8 selected call disappeared for {record['signal_id']}")
    for field in ("scrip_code", "armed_on", "prediction_sha256"):
        if record[field] != m8_record.get(field):
            raise ValueError(f"M8 {field} changed for {record['signal_id']}")
    if m8_record.get("selected") is not True:
        raise ValueError(f"M8 selection changed for {record['signal_id']}")
    if len(record["cohort_offsets"]) != N_COHORTS or not all(
        MIN_OFFSET <= int(offset) <= MAX_OFFSET
        for offset in record["cohort_offsets"]
    ):
        raise ValueError(f"M11 timing offsets invalid for {record['signal_id']}")


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


def _exclude(placebo: dict[str, Any], reason: str) -> bool:
    placebo.update(status=f"excluded_{reason}", outcome=reason, resolved_at=_now().isoformat())
    return True


def _resolve_placebo(
    placebo: dict[str, Any],
    *,
    armed_on: str,
    bars: pd.DataFrame,
    sessions: list[date],
    root: Path,
) -> bool:
    if placebo["status"] == "resolved":
        exit_on = date.fromisoformat(placebo["exit_date"])
        source = bars.loc[bars.index.date <= exit_on]
        if _frame_hash(source) != placebo["source_sha256"]:
            raise ValueError(
                f"resolved timing source changed for offset {placebo['offset_sessions']}"
            )
        return False
    if placebo["status"] != "pending":
        return False
    armed = date.fromisoformat(armed_on)
    try:
        armed_index = sessions.index(armed)
    except ValueError as exc:
        raise ValueError(f"arming session {armed_on} is absent from benchmark") from exc
    offset = int(placebo["offset_sessions"])
    decision_index = armed_index + offset
    entry_index = decision_index + 1
    maturity_index = entry_index + 3
    if maturity_index >= len(sessions):
        return False
    decision_on = sessions[decision_index]
    entry_on = sessions[entry_index]
    through_decision = bars.loc[bars.index.date <= decision_on]
    if through_decision.empty or through_decision.index[-1].date() != decision_on:
        return _exclude(placebo, "missing_decision_bar")
    atr_value = float(atr(through_decision, 14).iloc[-1])
    if not np.isfinite(atr_value) or atr_value <= 0:
        return _exclude(placebo, "invalid_atr")
    future = bars.loc[bars.index.date >= entry_on]
    if future.empty or future.index[0].date() != entry_on:
        return _exclude(placebo, "missing_entry_bar")
    if len(future) < 4:
        return _exclude(placebo, "missing_outcome_window")
    settings = load_config(root)
    market = nse_market(settings)
    fill = float(future.iloc[0].open) * (1 + float(market.costs.slippage_pct))
    stop = fill - atr_value
    target = fill + 0.5 * (fill - stop)
    size = position_size(
        SizeInputs(
            equity=float(settings.risk.trading_capital),
            entry=fill,
            stop=stop,
            max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
            max_position_value_pct=float(settings.risk.max_position_value_pct),
            size_multiplier=float(settings.risk.regime_size_multiplier.neutral),
            gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
            gap95_pct=gap95_pct(through_decision),
            available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
        )
    )
    if size.qty <= 0:
        return _exclude(placebo, "unsizeable")
    result = _quick_outcome(
        future,
        fill=fill,
        stop=stop,
        target=target,
        max_hold=3,
        qty=size.qty,
        costs=market.costs,
    )
    if result is None:
        return False
    label, net_r, exit_price, exit_on, outcome = result
    source = bars.loc[bars.index.date <= exit_on]
    placebo.update(
        status="resolved",
        decision_date=decision_on.isoformat(),
        entry_date=entry_on.isoformat(),
        fill_price=fill,
        atr=atr_value,
        stop=stop,
        target=target,
        qty=size.qty,
        label=label,
        net_r=net_r,
        outcome=outcome,
        exit_price=exit_price,
        exit_date=exit_on.isoformat(),
        source_sha256=_frame_hash(source),
        resolved_at=_now().isoformat(),
    )
    return True


def summarize_timing(state: dict[str, Any], m8_state: dict[str, Any]) -> dict[str, Any]:
    m8 = {row["signal_id"]: row for row in m8_state.get("records", [])}
    mature: list[tuple[dict[str, Any], dict[str, Any]]] = []
    excluded_records = 0
    for record in state.get("records", []):
        if not record.get("prospective_eligible"):
            continue
        actual = m8.get(record["signal_id"])
        if actual is None or actual.get("status") != "resolved":
            continue
        statuses = {placebo["status"] for placebo in record["placebos"]}
        if any(status.startswith("excluded_") for status in statuses):
            excluded_records += 1
            continue
        if statuses == {"resolved"}:
            mature.append((record, actual))

    actual_labels = [int(actual["label"]) for _, actual in mature]
    actual_returns = [float(actual["net_r"]) for _, actual in mature]
    cohort_labels: list[list[int]] = [[] for _ in range(N_COHORTS)]
    cohort_returns: list[list[float]] = [[] for _ in range(N_COHORTS)]
    for record, _ in mature:
        placebos = {
            int(placebo["offset_sessions"]): placebo for placebo in record["placebos"]
        }
        for index, offset in enumerate(record["cohort_offsets"]):
            placebo = placebos[int(offset)]
            cohort_labels[index].append(int(placebo["label"]))
            cohort_returns[index].append(float(placebo["net_r"]))
    cohort_mean_r = np.asarray(
        [np.mean(values) if values else np.nan for values in cohort_returns], dtype=float
    )
    cohort_accuracy = np.asarray(
        [np.mean(values) if values else np.nan for values in cohort_labels], dtype=float
    )
    actual_mean_r = float(np.mean(actual_returns)) if actual_returns else None
    null_mean_r = (
        float(np.nanmean(cohort_mean_r)) if np.isfinite(cohort_mean_r).any() else None
    )
    advantage = (
        actual_mean_r - null_mean_r
        if actual_mean_r is not None and null_mean_r is not None
        else None
    )
    finite = cohort_mean_r[np.isfinite(cohort_mean_r)]
    p_value = (
        float((np.sum(finite >= actual_mean_r) + 1) / (len(finite) + 1))
        if actual_mean_r is not None and len(finite)
        else None
    )
    active_sessions = len({actual["armed_on"] for _, actual in mature})
    checks = {
        "resolved_calls": len(actual_returns) >= MIN_CALLS,
        "active_sessions": active_sessions >= MIN_SESSIONS,
        "advantage_r": advantage is not None and advantage >= MIN_ADVANTAGE_R,
        "p_value": p_value is not None and p_value <= MAX_P_VALUE,
    }
    enough = checks["resolved_calls"] and checks["active_sessions"]
    status = (
        "random_timing_pass"
        if enough and all(checks.values())
        else "random_timing_fail" if enough else "collecting_insufficient_evidence"
    )
    return {
        "status": status,
        "registered_model_calls": sum(
            record.get("prospective_eligible") is True
            for record in state.get("records", [])
        ),
        "paired_resolved_calls": len(actual_returns),
        "excluded_timing_calls": excluded_records,
        "active_sessions": active_sessions,
        "model_accuracy": float(np.mean(actual_labels)) if actual_labels else None,
        "model_expectancy_r": actual_mean_r,
        "random_timing_accuracy_mean": (
            float(np.nanmean(cohort_accuracy))
            if np.isfinite(cohort_accuracy).any()
            else None
        ),
        "random_timing_expectancy_r_mean": null_mean_r,
        "random_timing_expectancy_r_std": (
            float(np.nanstd(cohort_mean_r)) if np.isfinite(cohort_mean_r).any() else None
        ),
        "timing_advantage_r": advantage,
        "p_value": p_value,
        "gate_checks": checks,
        "n_cohorts": N_COHORTS,
        "offset_sessions": [MIN_OFFSET, MAX_OFFSET],
        "control_scope": "same_stock_random_future_session_timing",
        "is_broader_random_timing_control": True,
        "random_timing_gate_passed": status == "random_timing_pass",
        "eligible_for_live": False,
        "updated_at": _now().isoformat(),
    }


def collect_timing(
    root: Path = ROOT,
    output: Path = DEFAULT_OUTPUT,
    m8_path: Path = STATE_PATH,
) -> dict[str, Any]:
    """Freeze new timings, resolve mature placebos, and publish paired evidence."""
    with _lock(output):
        if not m8_path.exists():
            raise FileNotFoundError("M8 state does not exist")
        m8_state = json.loads(m8_path.read_text(encoding="utf-8"))
        activation = m8_state["activation"]
        state_path = output / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                state.get("version") != VERSION
                or state.get("seed") != SEED
                or state.get("n_cohorts") != N_COHORTS
                or state.get("offset_sessions") != [MIN_OFFSET, MAX_OFFSET]
            ):
                raise ValueError("M11 timing registration does not match frozen protocol")
            if state["m8_activation_sha256"] != _hash(activation):
                raise ValueError("M8 activation changed after M11 timing registration")
        else:
            state = {
                "version": VERSION,
                "registered_at": _now().isoformat(),
                "m8_activation_sha256": _hash(activation),
                "seed": SEED,
                "n_cohorts": N_COHORTS,
                "offset_sessions": [MIN_OFFSET, MAX_OFFSET],
                "scope": "prospective_same_stock_random_future_session_timing",
                "records": [],
                "errors": [],
            }
        selected = {
            row["signal_id"]: row
            for row in m8_state.get("records", [])
            if row.get("selected") is True
        }
        existing = {record["signal_id"] for record in state["records"]}
        assigned_at = _now()
        for signal_id in sorted(selected):
            if signal_id not in existing:
                state["records"].append(_new_record(selected[signal_id], assigned_at))

        settings = load_config(root)
        errors = []
        cache: dict[str, pd.DataFrame] = {}
        with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
            benchmark_code = _index_codes(con).get(settings.universe.benchmark.upper())
            if benchmark_code is None:
                raise ValueError("NSE benchmark code is unavailable")

            def bars(code: str) -> pd.DataFrame:
                if code not in cache:
                    cache[code] = _load_bars(con, code)
                return cache[code]

            sessions = list(bars(benchmark_code).index.date)
            for record in state["records"]:
                try:
                    _verify_record(record, selected.get(record["signal_id"]))
                    frame = bars(record["scrip_code"])
                    for placebo in record["placebos"]:
                        _resolve_placebo(
                            placebo,
                            armed_on=record["armed_on"],
                            bars=frame,
                            sessions=sessions,
                            root=root,
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
        state["summary"] = summarize_timing(state, m8_state)
        state["source_integrity"] = {"passed": not errors, "errors": len(errors)}
        if errors:
            state["summary"]["status"] = "degraded"
        write_json(state_path, state)
        return state
