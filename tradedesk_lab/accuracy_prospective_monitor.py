"""M9 integrity, availability, uncertainty and stress monitor for frozen M8 evidence.

This module observes the M8 cohort without writing its state or changing its frozen
selection/outcome contract.  Its own hash-chained JSONL audit is supplementary evidence,
never an input to signal selection.
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
from tradedesk_lab.accuracy_prospective_shadow import (
    DEFAULT_OUTPUT as SHADOW_OUTPUT,
)
from tradedesk_lab.accuracy_prospective_shadow import (
    M7_ARTIFACT,
    MODEL_FILE,
    _contract_hashes,
    _index_codes,
    _load_bars,
    _prediction_hash,
    _quick_outcome,
    _score_deadline,
    summarize,
)
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

VERSION = "accuracy-prospective-integrity-v1"
DEFAULT_OUTPUT = OUTPUT / "accuracy_prospective_monitor"
STATE_PATH = SHADOW_OUTPUT / "state.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@contextmanager
def _lock(output: Path) -> Iterator[None]:
    path = output / "monitor.lock"
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
            raise RuntimeError("another M9 monitor owns the lock") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_audit(path: Path) -> list[dict[str, Any]]:
    """Read and verify every hash-chain link; any damaged line fails closed."""
    if not path.exists():
        return []
    events = []
    previous: str | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        event = json.loads(line)
        claimed = event.pop("sha256", None)
        if event.get("previous_sha256") != previous:
            raise ValueError(f"audit chain link {number} has the wrong predecessor")
        actual = _sha(event)
        if claimed != actual:
            raise ValueError(f"audit chain link {number} has an invalid hash")
        event["sha256"] = claimed
        events.append(event)
        previous = claimed
    return events


def _append_events(path: Path, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = read_audit(path)
    known = {event["event_id"]: event for event in events}
    previous = events[-1]["sha256"] if events else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for addition in additions:
            existing = known.get(addition["event_id"])
            if existing is not None:
                if existing["payload"] != addition["payload"]:
                    raise ValueError(f"immutable audit event changed: {addition['event_id']}")
                continue
            event = {
                "event_id": addition["event_id"],
                "kind": addition["kind"],
                "recorded_at": _now(),
                "previous_sha256": previous,
                "payload": addition["payload"],
            }
            event["sha256"] = _sha(event)
            stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            events.append(event)
            known[event["event_id"]] = event
            previous = event["sha256"]
    return events


def _audit_additions(state: dict[str, Any]) -> list[dict[str, Any]]:
    activation = state["activation"]
    additions = [
        {
            "event_id": f"activation:{activation['activated_at']}",
            "kind": "activation",
            "payload": activation,
        }
    ]
    prediction_names = (
        "signal_id",
        "scrip_code",
        "symbol",
        "setup",
        "armed_on",
        "scored_at",
        "score_deadline",
        "source_watchlist",
        "source_sha256",
        "features_sha256",
        "probability",
        "rank",
        "selected",
        "prospective_eligible",
        "selection_reason",
        "prediction_sha256",
    )
    resolution_names = (
        "signal_id",
        "status",
        "entry_date",
        "fill_price",
        "stop",
        "target",
        "qty",
        "outcome",
        "label",
        "net_r",
        "exit_price",
        "exit_date",
        "resolved_at",
    )
    for record in state.get("records", []):
        additions.append(
            {
                "event_id": f"prediction:{record['signal_id']}",
                "kind": "prediction",
                "payload": {name: record.get(name) for name in prediction_names},
            }
        )
        if record.get("status") in {"resolved", "excluded"}:
            additions.append(
                {
                    "event_id": f"resolution:{record['signal_id']}",
                    "kind": "resolution",
                    "payload": {name: record.get(name) for name in resolution_names},
                }
            )
    return additions


def _watchlist_availability(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    activation = state["activation"]
    cutoff = date.fromisoformat(activation["forward_after"])
    activated = datetime.fromisoformat(activation["activated_at"])
    records = state.get("records", [])
    selected_days = {
        row["armed_on"]
        for row in records
        if row.get("prospective_eligible") is True and row.get("selected") is True
    }
    evaluated_days = {
        row["armed_on"] for row in records if row.get("prospective_eligible") is True
    }
    watchlists: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "data/watchlists").glob("*.json")):
        try:
            raw = path.read_bytes()
            watchlist = json.loads(raw)
            armed = date.fromisoformat(watchlist["on"])
            generated = datetime.fromisoformat(watchlist["generated_at"])
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=activated.tzinfo)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if armed <= cutoff or generated < activated:
            continue
        watchlist["_registration_valid"] = (
            generated.astimezone(UTC) < _score_deadline(armed).astimezone(UTC)
        )
        watchlists[armed.isoformat()] = watchlist

    settings = load_config(root)
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        benchmark_code = _index_codes(con).get(settings.universe.benchmark.upper())
        if benchmark_code is None:
            raise ValueError("NSE benchmark code is unavailable for M9 availability")
        benchmark = _load_bars(con, benchmark_code)
    expected_days = sorted(
        {day.isoformat() for day in benchmark.index.date if day > cutoff}
    )

    sessions = []
    missing_watchlists = []
    missing_collections = []
    late_watchlists = []
    for armed_on in expected_days:
        watchlist = watchlists.get(armed_on)
        if watchlist is None:
            sessions.append(
                {
                    "on": armed_on,
                    "watchlist_available": False,
                    "candidate_count": None,
                    "evaluated": False,
                    "selected": False,
                }
            )
            missing_watchlists.append(armed_on)
            continue
        candidates = [
            entry
            for entry in watchlist.get("entries", [])
            if (entry.get("signal") or {}).get("setup")
            == activation["candidate"]["setup"]
        ]
        row = {
            "on": armed_on,
            "watchlist_available": True,
            "registration_valid": watchlist.get("_registration_valid") is True,
            "candidate_count": len(candidates),
            "evaluated": armed_on in evaluated_days,
            "selected": armed_on in selected_days,
        }
        sessions.append(row)
        if not row["registration_valid"]:
            late_watchlists.append(armed_on)
            continue
        if candidates and not row["evaluated"]:
            missing_collections.append(armed_on)
    selected_sessions = sum(row["selected"] for row in sessions)
    return {
        "expected_sessions": len(expected_days),
        "watchlist_sessions": len(watchlists),
        "observed_sessions": len(sessions),
        "evaluated_sessions": sum(row["evaluated"] for row in sessions),
        "selected_sessions": selected_sessions,
        "zero_selected_sessions": len(sessions) - selected_sessions,
        "selected_session_coverage": selected_sessions / len(sessions) if sessions else None,
        "missing_watchlist_sessions": missing_watchlists,
        "late_watchlist_sessions": late_watchlists,
        "missing_collection_sessions": missing_collections,
        "sessions": sessions[-100:],
    }


def clustered_lower_bound(
    records: list[dict[str, Any]],
    *,
    grouping: str,
    resamples: int = 10_000,
    seed: int = 20261003,
) -> float | None:
    """Non-parametric lower 95% bound with whole sessions or ISO weeks resampled."""
    resolved = [
        row
        for row in records
        if row.get("prospective_eligible") is True
        and row.get("selected") is True
        and row.get("status") == "resolved"
    ]
    buckets: dict[str, list[int]] = {}
    for row in resolved:
        armed = date.fromisoformat(row["armed_on"])
        iso = armed.isocalendar()
        key = armed.isoformat() if grouping == "session" else f"{iso.year}-W{iso.week:02d}"
        buckets.setdefault(key, []).append(int(row["label"]))
    minimum = 10 if grouping == "session" else 4
    if len(buckets) < minimum:
        return None
    groups = [np.asarray(values, dtype=float) for values in buckets.values()]
    rng = np.random.default_rng(seed)
    estimates = np.empty(resamples, dtype=float)
    for index in range(resamples):
        sample = rng.integers(0, len(groups), size=len(groups))
        labels = np.concatenate([groups[number] for number in sample])
        estimates[index] = float(labels.mean())
    return float(np.quantile(estimates, 0.025))


class _StressedCosts:
    def __init__(self, base: Any, multiplier: float) -> None:
        self.base = base
        self.multiplier = multiplier

    @property
    def slippage_pct(self) -> float:
        return float(self.base.slippage_pct) * self.multiplier

    def leg_cost(self, **kwargs: Any) -> Any:
        return self.base.leg_cost(**kwargs)


def _stress_summary(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    resolved = [
        row
        for row in state.get("records", [])
        if row.get("prospective_eligible") is True
        and row.get("selected") is True
        and row.get("status") == "resolved"
    ]
    if not resolved:
        return {
            "status": "insufficient_evidence",
            "slippage_multiplier": 2.0,
            "resolved_calls": 0,
            "accuracy": None,
            "expectancy_r": None,
        }
    settings = load_config(root)
    market = nse_market(settings)
    base_slippage = float(market.costs.slippage_pct)
    stressed_costs = _StressedCosts(market.costs, 2.0)
    labels, returns, errors = [], [], []
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        cache: dict[str, pd.DataFrame] = {}
        for row in resolved:
            try:
                code = row["scrip_code"]
                if code not in cache:
                    cache[code] = _load_bars(con, code)
                bars = cache[code]
                entry_date = date.fromisoformat(row["entry_date"])
                future = bars.loc[bars.index.date >= entry_date]
                raw_open = float(row["fill_price"]) / (1 + base_slippage)
                fill = raw_open * (1 + stressed_costs.slippage_pct)
                stop = fill - float(row["atr"])
                target = fill + 0.5 * (fill - stop)
                result = _quick_outcome(
                    future,
                    fill=fill,
                    stop=stop,
                    target=target,
                    max_hold=3,
                    qty=float(row["qty"]),
                    costs=stressed_costs,
                )
                if result is None:
                    raise ValueError("stressed outcome did not mature")
                label, net_r, _, _, _ = result
                labels.append(label)
                returns.append(net_r)
            except Exception as exc:
                errors.append({"signal_id": row.get("signal_id"), "error": str(exc)})
    return {
        "status": "complete" if not errors else "degraded",
        "slippage_multiplier": 2.0,
        "resolved_calls": len(labels),
        "accuracy": float(np.mean(labels)) if labels else None,
        "expectancy_r": float(np.mean(returns)) if returns else None,
        "positive_expectancy": bool(returns and np.mean(returns) > 0),
        "errors": errors,
    }


def _integrity_checks(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    activation = state["activation"]
    records = state.get("records", [])
    failures = []
    model_path = SHADOW_OUTPUT / MODEL_FILE
    if not model_path.exists() or digest(model_path) != activation["model_sha256"]:
        failures.append("frozen_model_hash")
    if not M7_ARTIFACT.exists() or digest(M7_ARTIFACT) != activation["m7_artifact_sha256"]:
        failures.append("m7_artifact_hash")
    if _contract_hashes() != activation["contract_sha256"]:
        failures.append("m8_contract_hash")
    ids = [row.get("signal_id") for row in records]
    if len(ids) != len(set(ids)):
        failures.append("duplicate_signal_id")
    threshold = float(activation["candidate"]["probability_threshold"])
    top_k = int(activation["candidate"]["top_k_per_arming_session"])
    cutoff = date.fromisoformat(activation["forward_after"])
    for row in records:
        signal_id = row.get("signal_id", "unknown")
        if row.get("prediction_sha256") != _prediction_hash(row):
            failures.append(f"prediction_hash:{signal_id}")
        source = root / "data/watchlists" / str(row.get("source_watchlist", ""))
        if not source.is_file() or digest(source) != row.get("source_sha256"):
            failures.append(f"source_watchlist_hash:{signal_id}")
        if (
            row.get("prospective_eligible") is True
            and date.fromisoformat(row["armed_on"]) <= cutoff
        ):
            failures.append(f"preactivation_record:{signal_id}")
        expected_selected = float(row["probability"]) >= threshold and int(row["rank"]) <= top_k
        if bool(row.get("selected")) != expected_selected:
            failures.append(f"selection_rule:{signal_id}")
    recomputed = summarize({"records": records})
    stored = state.get("summary") or {}
    compared = set(recomputed) - {"updated_at"}
    if any(stored.get(name) != recomputed.get(name) for name in compared):
        failures.append("summary_mismatch")
    availability = _watchlist_availability(root, state)
    if availability["missing_watchlist_sessions"]:
        failures.append("missing_watchlist_session")
    if availability["late_watchlist_sessions"]:
        failures.append("late_watchlist_registration")
    if availability["missing_collection_sessions"]:
        failures.append("uncollected_watchlist_session")
    return {
        "status": "healthy" if not failures else "degraded",
        "passed": not failures,
        "failures": failures,
        "checks": 9,
    }


def run_monitor(
    root: Path = ROOT,
    output: Path = DEFAULT_OUTPUT,
    state_path: Path = STATE_PATH,
) -> dict[str, Any]:
    """Verify M8, extend the append-only audit, and publish a read-only scorecard."""
    with _lock(output):
        if not state_path.exists():
            raise FileNotFoundError("M8 state does not exist")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        audit_path = output / "audit.jsonl"
        events = _append_events(audit_path, _audit_additions(state))
        integrity = _integrity_checks(root, state)
        availability = _watchlist_availability(root, state)
        stress = _stress_summary(root, state)
        records = state.get("records", [])
        uncertainty = {
            "session_cluster_lower_95": clustered_lower_bound(
                records, grouping="session"
            ),
            "week_cluster_lower_95": clustered_lower_bound(records, grouping="week"),
            "minimum_sessions_for_session_bootstrap": 10,
            "minimum_weeks_for_week_bootstrap": 4,
        }
        shadow_summary = state["summary"]
        review_ready = bool(
            integrity["passed"]
            and shadow_summary.get("status") == "prospective_pass"
            and stress.get("positive_expectancy") is True
            and uncertainty["session_cluster_lower_95"] is not None
            and uncertainty["session_cluster_lower_95"] >= 0.70
            and uncertainty["week_cluster_lower_95"] is not None
            and uncertainty["week_cluster_lower_95"] >= 0.70
        )
        report = {
            "version": VERSION,
            "created_at": _now(),
            "shadow_activation": state["activation"],
            "shadow_summary": shadow_summary,
            "integrity": integrity,
            "audit": {
                "path": str(audit_path.relative_to(root)),
                "events": len(events),
                "predictions": sum(event["kind"] == "prediction" for event in events),
                "resolutions": sum(event["kind"] == "resolution" for event in events),
                "head_sha256": events[-1]["sha256"] if events else None,
            },
            "availability": availability,
            "uncertainty": uncertainty,
            "double_slippage_stress": stress,
            "review_ready": review_ready,
            "eligible_for_live": False,
            "authority": "read_only_evidence_monitor",
            "detail": (
                "M9 does not change the frozen M8 model, selection policy, outcomes, "
                "canonical baseline, or live authority."
            ),
        }
        write_json(output / "latest.json", report)
        return report
