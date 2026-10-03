"""M10 prospective matched-random selection control for the frozen M8 selector.

For each fresh M8 session, freeze 1,000 random selections from the same trend-pullback
candidate population with the same call count as the model.  Every candidate is later
resolved with M8's exact next-open geometry and costs.  This isolates ranking value; it is
not mislabeled as the broader same-stock random-timing control required for production.
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
from tradedesk.markets.market import nse_market
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.accuracy_prospective_shadow import (
    DEFAULT_OUTPUT as SHADOW_OUTPUT,
)
from tradedesk_lab.accuracy_prospective_shadow import _index_codes, _load_bars, _quick_outcome
from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json

VERSION = "accuracy-prospective-control-v1"
DEFAULT_OUTPUT = OUTPUT / "accuracy_prospective_control"
STATE_PATH = SHADOW_OUTPUT / "state.json"
SEED = 20261004
N_COHORTS = 1000
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
            raise RuntimeError("another M10 control collector owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _session_seed(armed_on: str) -> int:
    digest = hashlib.sha256(f"{SEED}:{armed_on}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**32)


def freeze_assignments(
    signal_ids: list[str],
    *,
    call_count: int,
    armed_on: str,
    n_cohorts: int = N_COHORTS,
) -> list[list[str]]:
    """Deterministic same-population selections, frozen before any next-session entry."""
    if call_count < 0 or call_count > len(signal_ids):
        raise ValueError("control call count must fit the candidate population")
    ordered = sorted(signal_ids)
    if call_count == 0:
        return [[] for _ in range(n_cohorts)]
    rng = np.random.default_rng(_session_seed(armed_on))
    values = np.asarray(ordered, dtype=object)
    return [
        sorted(str(value) for value in rng.choice(values, size=call_count, replace=False))
        for _ in range(n_cohorts)
    ]


def _new_session(records: list[dict[str, Any]], assigned_at: datetime) -> dict[str, Any]:
    armed_on = records[0]["armed_on"]
    if any(row["armed_on"] != armed_on for row in records):
        raise ValueError("control session mixed arming dates")
    selected = [row for row in records if row.get("selected") is True]
    deadline = min(datetime.fromisoformat(row["score_deadline"]) for row in records)
    prospective = (
        all(row.get("prospective_eligible") is True for row in records)
        and assigned_at < deadline
    )
    candidate_rows = [
        {
            "signal_id": row["signal_id"],
            "scrip_code": row["scrip_code"],
            "symbol": row["symbol"],
            "armed_on": armed_on,
            "atr": float(row["atr"]),
            "prediction_sha256": row["prediction_sha256"],
            "model_selected": bool(row.get("selected")),
            "status": "pending" if prospective else "excluded_late",
            "entry_date": None,
            "fill_price": None,
            "stop": None,
            "target": None,
            "qty": None,
            "label": None,
            "net_r": None,
            "outcome": None,
            "resolved_at": None,
        }
        for row in sorted(records, key=lambda item: item["signal_id"])
    ]
    assignments = freeze_assignments(
        [row["signal_id"] for row in records],
        call_count=len(selected),
        armed_on=armed_on,
    )
    frozen = {
        "armed_on": armed_on,
        "assigned_at": assigned_at.isoformat(),
        "score_deadline": deadline.isoformat(),
        "prospective_eligible": prospective,
        "candidate_count": len(records),
        "model_call_count": len(selected),
        "model_signal_ids": sorted(row["signal_id"] for row in selected),
        "cohort_assignments": assignments,
        "candidates": candidate_rows,
    }
    frozen["assignment_sha256"] = _hash(
        {
            "armed_on": armed_on,
            "candidate_ids": [row["signal_id"] for row in candidate_rows],
            "model_signal_ids": frozen["model_signal_ids"],
            "cohort_assignments": assignments,
        }
    )
    return frozen


def _verify_session(session: dict[str, Any], m8_records: dict[str, dict[str, Any]]) -> None:
    candidate_ids = [row["signal_id"] for row in session["candidates"]]
    expected = _hash(
        {
            "armed_on": session["armed_on"],
            "candidate_ids": candidate_ids,
            "model_signal_ids": session["model_signal_ids"],
            "cohort_assignments": session["cohort_assignments"],
        }
    )
    if expected != session["assignment_sha256"]:
        raise ValueError(f"M10 assignment hash changed for {session['armed_on']}")
    current_ids = sorted(
        signal_id
        for signal_id, row in m8_records.items()
        if row["armed_on"] == session["armed_on"]
    )
    if sorted(candidate_ids) != current_ids:
        raise ValueError(f"M8 candidate population changed for {session['armed_on']}")
    selected_ids = sorted(
        signal_id
        for signal_id in current_ids
        if m8_records[signal_id].get("selected") is True
    )
    if selected_ids != session["model_signal_ids"]:
        raise ValueError(f"M8 model selection changed for {session['armed_on']}")
    for candidate in session["candidates"]:
        current = m8_records.get(candidate["signal_id"])
        if current is None or current["prediction_sha256"] != candidate["prediction_sha256"]:
            raise ValueError(f"M8 prediction changed for {candidate['signal_id']}")


def _verify_selected_outcome(
    candidate: dict[str, Any], m8_record: dict[str, Any]
) -> bool:
    """Prove that independently recomputed M10 outcomes equal frozen M8 outcomes."""
    if not candidate["model_selected"] or candidate["status"] == "excluded_late":
        return False
    m8_status = m8_record.get("status")
    if m8_status == "pending":
        if candidate["status"] != "pending":
            raise ValueError(f"M8/M10 maturity differs for {candidate['signal_id']}")
        return False
    if m8_status == "excluded":
        if candidate["status"] != "excluded_unsizeable":
            raise ValueError(f"M8/M10 sizing differs for {candidate['signal_id']}")
        return True
    if m8_status != "resolved" or candidate["status"] != "resolved":
        raise ValueError(f"M8/M10 status differs for {candidate['signal_id']}")
    for field in ("entry_date", "exit_date", "label", "outcome", "qty"):
        if candidate.get(field) != m8_record.get(field):
            raise ValueError(f"M8/M10 {field} differs for {candidate['signal_id']}")
    for field in ("fill_price", "stop", "target", "exit_price", "net_r"):
        if not np.isclose(
            float(candidate[field]), float(m8_record[field]), rtol=1e-12, atol=1e-12
        ):
            raise ValueError(f"M8/M10 {field} differs for {candidate['signal_id']}")
    return True


def _resolve_candidate(
    candidate: dict[str, Any],
    bars: pd.DataFrame,
    sessions: list[date],
    root: Path,
) -> bool:
    if candidate["status"] != "pending":
        return False
    armed = date.fromisoformat(candidate["armed_on"])
    later = [day for day in sessions if day > armed]
    if not later:
        return False
    entry_on = later[0]
    future = bars.loc[bars.index.date >= entry_on]
    if future.empty or future.index[0].date() != entry_on:
        raise ValueError(f"missing next-session candle for {candidate['scrip_code']}")
    if len(future) < 4:
        return False
    settings = load_config(root)
    market = nse_market(settings)
    fill = float(future.iloc[0].open) * (1 + float(market.costs.slippage_pct))
    stop = fill - float(candidate["atr"])
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
            gap95_pct=gap95_pct(bars.loc[bars.index.date <= armed]),
            available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
        )
    )
    if size.qty <= 0:
        candidate.update(
            status="excluded_unsizeable", outcome="unsizeable", resolved_at=_now().isoformat()
        )
        return True
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
    candidate.update(
        status="resolved",
        entry_date=entry_on.isoformat(),
        fill_price=fill,
        stop=stop,
        target=target,
        qty=size.qty,
        label=label,
        net_r=net_r,
        outcome=outcome,
        exit_price=exit_price,
        exit_date=exit_on.isoformat(),
        resolved_at=_now().isoformat(),
    )
    return True


def summarize_control(state: dict[str, Any], m8_state: dict[str, Any]) -> dict[str, Any]:
    m8 = {row["signal_id"]: row for row in m8_state.get("records", [])}
    eligible_sessions = []
    for session in state.get("sessions", []):
        if not session.get("prospective_eligible") or session["model_call_count"] == 0:
            continue
        candidates = {row["signal_id"]: row for row in session["candidates"]}
        actual = [m8.get(signal_id) for signal_id in session["model_signal_ids"]]
        if not all(row and row.get("status") == "resolved" for row in actual):
            continue
        used = {signal_id for cohort in session["cohort_assignments"] for signal_id in cohort}
        if not all(candidates[signal_id]["status"] == "resolved" for signal_id in used):
            continue
        eligible_sessions.append((session, candidates, actual))

    actual_labels: list[int] = []
    actual_returns: list[float] = []
    unfiltered_labels: list[int] = []
    unfiltered_returns: list[float] = []
    cohort_labels: list[list[int]] = [[] for _ in range(N_COHORTS)]
    cohort_returns: list[list[float]] = [[] for _ in range(N_COHORTS)]
    for session, candidates, actual in eligible_sessions:
        actual_labels.extend(int(row["label"]) for row in actual if row is not None)
        actual_returns.extend(float(row["net_r"]) for row in actual if row is not None)
        resolved_candidates = [row for row in candidates.values() if row["status"] == "resolved"]
        unfiltered_labels.extend(int(row["label"]) for row in resolved_candidates)
        unfiltered_returns.extend(float(row["net_r"]) for row in resolved_candidates)
        for index, assignment in enumerate(session["cohort_assignments"]):
            cohort_labels[index].extend(
                int(candidates[signal_id]["label"]) for signal_id in assignment
            )
            cohort_returns[index].extend(
                float(candidates[signal_id]["net_r"]) for signal_id in assignment
            )
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
    p_value = (
        float(
            (
                np.sum(cohort_mean_r[np.isfinite(cohort_mean_r)] >= actual_mean_r)
                + 1
            )
            / (np.isfinite(cohort_mean_r).sum() + 1)
        )
        if actual_mean_r is not None and np.isfinite(cohort_mean_r).any()
        else None
    )
    checks = {
        "resolved_calls": len(actual_returns) >= MIN_CALLS,
        "active_sessions": len(eligible_sessions) >= MIN_SESSIONS,
        "advantage_r": advantage is not None and advantage >= MIN_ADVANTAGE_R,
        "p_value": p_value is not None and p_value <= MAX_P_VALUE,
    }
    return {
        "status": (
            "selection_control_pass"
            if all(checks.values())
            else "selection_control_fail"
            if checks["resolved_calls"] and checks["active_sessions"]
            else "collecting_insufficient_evidence"
        ),
        "resolved_model_calls": len(actual_returns),
        "mature_sessions": len(eligible_sessions),
        "model_accuracy": float(np.mean(actual_labels)) if actual_labels else None,
        "model_expectancy_r": actual_mean_r,
        "random_accuracy_mean": (
            float(np.nanmean(cohort_accuracy)) if np.isfinite(cohort_accuracy).any() else None
        ),
        "random_expectancy_r_mean": null_mean_r,
        "random_expectancy_r_std": (
            float(np.nanstd(cohort_mean_r)) if np.isfinite(cohort_mean_r).any() else None
        ),
        "selection_advantage_r": advantage,
        "p_value": p_value,
        "unfiltered_candidate_accuracy": (
            float(np.mean(unfiltered_labels)) if unfiltered_labels else None
        ),
        "unfiltered_candidate_expectancy_r": (
            float(np.mean(unfiltered_returns)) if unfiltered_returns else None
        ),
        "gate_checks": checks,
        "n_cohorts": N_COHORTS,
        "control_scope": "same_session_same_setup_population_random_candidate_selection",
        "satisfies_broader_random_timing_gate": False,
        "eligible_for_live": False,
        "updated_at": _now().isoformat(),
    }


def collect_control(
    root: Path = ROOT,
    output: Path = DEFAULT_OUTPUT,
    m8_path: Path = STATE_PATH,
) -> dict[str, Any]:
    """Freeze new controls before entry, resolve them later, and publish paired metrics."""
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
            ):
                raise ValueError("M10 control registration does not match the frozen protocol")
            if state["m8_activation_sha256"] != _hash(activation):
                raise ValueError("M8 activation changed after M10 control registration")
        else:
            state = {
                "version": VERSION,
                "registered_at": _now().isoformat(),
                "m8_activation_sha256": _hash(activation),
                "seed": SEED,
                "n_cohorts": N_COHORTS,
                "scope": "matched_random_candidate_selection_not_random_entry_timing",
                "sessions": [],
                "errors": [],
            }
        records_by_day: dict[str, list[dict[str, Any]]] = {}
        for row in m8_state.get("records", []):
            records_by_day.setdefault(row["armed_on"], []).append(row)
        existing = {session["armed_on"] for session in state["sessions"]}
        assigned_at = _now()
        for armed_on in sorted(records_by_day):
            if armed_on not in existing:
                state["sessions"].append(_new_session(records_by_day[armed_on], assigned_at))

        m8_records = {row["signal_id"]: row for row in m8_state.get("records", [])}
        settings = load_config(root)
        errors = []
        parity_checks = 0
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
            for session in state["sessions"]:
                try:
                    _verify_session(session, m8_records)
                    for candidate in session["candidates"]:
                        _resolve_candidate(candidate, bars(candidate["scrip_code"]), sessions, root)
                        if _verify_selected_outcome(
                            candidate, m8_records[candidate["signal_id"]]
                        ):
                            parity_checks += 1
                except Exception as exc:
                    errors.append(
                        {"armed_on": session["armed_on"], "error": f"{type(exc).__name__}: {exc}"}
                    )
        state["errors"] = errors
        state["outcome_parity"] = {
            "checked_selected_calls": parity_checks,
            "passed": not errors,
        }
        state["sessions"].sort(key=lambda row: row["armed_on"])
        state["summary"] = summarize_control(state, m8_state)
        if errors:
            state["summary"]["status"] = "degraded"
        write_json(state_path, state)
        return state
