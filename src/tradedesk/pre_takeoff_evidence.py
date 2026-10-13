"""Frozen forward-evidence qualification and collector health for pre-takeoff plans."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from tradedesk.broker.indstocks.models import IST
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "pre-takeoff-evidence-v1"
SUPPORTED_MARKETS = frozenset({"nse", "bse", "crypto"})
RESOLVED_STATES = frozenset({"target", "stop", "timeout"})
EQUITY_EARLY_CALLS = 20
EQUITY_EARLY_SESSIONS = 10
QUALIFICATION_CALLS = 60
QUALIFICATION_SESSIONS = 40
MIN_EARLY_ACCURACY = 0.50
MIN_QUALIFICATION_ACCURACY = 0.70
MIN_QUALIFICATION_WILSON = 0.55
MAX_QUALIFICATION_LOSS_STREAK = 6
CRYPTO_REQUIRED_CONTROLS = (
    "matched_random_accuracy_tail_probability",
    "matched_random_net_r_tail_probability",
)


def _load_hash_chain(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous = "GENESIS"
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        value = json.loads(line)
        supplied = value.get("event_sha256")
        body = dict(value)
        body.pop("event_sha256", None)
        if value.get("previous_sha256") != previous or supplied != canonical_sha256(body):
            raise ValueError(f"{label} hash-chain failure at line {line_number}")
        events.append(value)
        previous = str(supplied)
    return events


def _append_hash_event(path: Path, payload: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    events = _load_hash_chain(path, label=label)
    previous = str(events[-1]["event_sha256"]) if events else "GENESIS"
    event = {**dict(payload), "previous_sha256": previous}
    event["event_sha256"] = canonical_sha256(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
    return event


def _latest_plans(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        latest[str(event["plan_id"])] = dict(event)
    return latest


def _as_ist(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _wilson(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    probability = successes / total
    denominator = 1.0 + z * z / total
    centre = (probability + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(probability * (1.0 - probability) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _maximum_losing_streak(values: Sequence[bool]) -> int:
    maximum = 0
    current = 0
    for value in values:
        current = 0 if value else current + 1
        maximum = max(maximum, current)
    return maximum


def _metrics(latest: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    resolved = sorted(
        (plan for plan in latest.values() if str(plan.get("state")) in RESOLVED_STATES),
        key=lambda plan: (str(plan.get("resolved_at", "")), str(plan.get("plan_id", ""))),
    )
    successes = sum(bool(plan.get("strict_success")) for plan in resolved)
    lower, upper = _wilson(successes, len(resolved))
    net_values = [float(plan["net_r"]) for plan in resolved if plan.get("net_r") is not None]
    sessions = {
        _as_ist(str(plan.get("decision_bar") or plan["armed_at"])).date().isoformat()
        for plan in resolved
    }
    return {
        "resolved_trades": len(resolved),
        "resolved_sessions": len(sessions),
        "first_resolved_session": min(sessions) if sessions else None,
        "last_resolved_session": max(sessions) if sessions else None,
        "strict_successes": successes,
        "strict_accuracy": successes / len(resolved) if resolved else None,
        "wilson_95_lower": lower,
        "wilson_95_upper": upper,
        "mean_net_r": float(np.mean(net_values)) if net_values else None,
        "net_r_available": len(net_values),
        "maximum_losing_streak": _maximum_losing_streak(
            [bool(plan.get("strict_success")) for plan in resolved]
        ),
    }


def _gate_checks(metrics: Mapping[str, Any], *, early: bool) -> dict[str, bool]:
    if early:
        return {
            "minimum_resolved_trades": int(metrics["resolved_trades"]) >= EQUITY_EARLY_CALLS,
            "minimum_resolved_sessions": int(metrics["resolved_sessions"]) >= EQUITY_EARLY_SESSIONS,
            "strict_accuracy": float(metrics.get("strict_accuracy") or 0.0) >= MIN_EARLY_ACCURACY,
            "positive_mean_net_r": float(metrics.get("mean_net_r") or 0.0) > 0.0,
            "complete_net_r": int(metrics["net_r_available"]) == int(metrics["resolved_trades"]),
        }
    return {
        "minimum_resolved_trades": int(metrics["resolved_trades"]) >= QUALIFICATION_CALLS,
        "minimum_resolved_sessions": int(metrics["resolved_sessions"]) >= QUALIFICATION_SESSIONS,
        "strict_accuracy": float(metrics.get("strict_accuracy") or 0.0)
        >= MIN_QUALIFICATION_ACCURACY,
        "wilson_95_lower": float(metrics.get("wilson_95_lower") or 0.0) >= MIN_QUALIFICATION_WILSON,
        "positive_mean_net_r": float(metrics.get("mean_net_r") or 0.0) > 0.0,
        "maximum_losing_streak": int(metrics["maximum_losing_streak"])
        <= MAX_QUALIFICATION_LOSS_STREAK,
        "complete_net_r": int(metrics["net_r_available"]) == int(metrics["resolved_trades"]),
    }


def _event_payload(
    *,
    market: str,
    stage: str,
    status: str,
    terminal: bool,
    checks: Mapping[str, bool],
    metrics: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "event": "qualification_gate_evaluated",
        "market": market,
        "stage": stage,
        "status": status,
        "terminal": terminal,
        "observed_at": observed_at.astimezone(IST).isoformat(),
        "checks": dict(checks),
        "blockers": [name for name, passed in checks.items() if not passed],
        "metrics": dict(metrics),
    }


def evaluate_pre_takeoff_evidence(
    market: str,
    latest: Mapping[str, Mapping[str, Any]],
    *,
    gate_events: Sequence[Mapping[str, Any]] = (),
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate one market without mutating; ``event_due`` freezes the first valid look."""

    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported pre-takeoff evidence market: {market}")
    now = (observed_at or datetime.now(IST)).astimezone(IST)
    metrics = _metrics(latest)
    terminal = next(
        (dict(event) for event in reversed(gate_events) if bool(event.get("terminal"))),
        None,
    )
    early_event = next(
        (dict(event) for event in reversed(gate_events) if event.get("stage") == "early"),
        None,
    )
    core_event = next(
        (
            dict(event)
            for event in reversed(gate_events)
            if event.get("stage") == "qualification_core"
        ),
        None,
    )
    thresholds = {
        "early": (
            {"resolved_trades": EQUITY_EARLY_CALLS, "resolved_sessions": EQUITY_EARLY_SESSIONS}
            if market in {"nse", "bse"}
            else None
        ),
        "qualification": {
            "resolved_trades": QUALIFICATION_CALLS,
            "resolved_sessions": QUALIFICATION_SESSIONS,
            "strict_accuracy": MIN_QUALIFICATION_ACCURACY,
            "wilson_95_lower": MIN_QUALIFICATION_WILSON,
            "positive_mean_net_r": True,
            "maximum_losing_streak": MAX_QUALIFICATION_LOSS_STREAK,
        },
    }
    base: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "metrics": metrics,
        "thresholds": thresholds,
        "terminal": terminal is not None,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "event_due": None,
        "control_requirements": (list(CRYPTO_REQUIRED_CONTROLS) if market == "crypto" else []),
    }
    if terminal is not None:
        base.update(
            {
                "status": str(terminal["status"]),
                "stage": str(terminal["stage"]),
                "blockers": list(terminal.get("blockers") or ()),
                "first_look": terminal,
            }
        )
        return base

    if market in {"nse", "bse"} and early_event is None:
        ready = (
            int(metrics["resolved_trades"]) >= EQUITY_EARLY_CALLS
            and int(metrics["resolved_sessions"]) >= EQUITY_EARLY_SESSIONS
        )
        if not ready:
            blockers = []
            if int(metrics["resolved_trades"]) < EQUITY_EARLY_CALLS:
                blockers.append("minimum_resolved_trades")
            if int(metrics["resolved_sessions"]) < EQUITY_EARLY_SESSIONS:
                blockers.append("minimum_resolved_sessions")
            base.update(
                {
                    "status": "collecting_early_gate",
                    "stage": "early",
                    "blockers": blockers,
                }
            )
            return base
        checks = _gate_checks(metrics, early=True)
        passed = all(checks.values())
        base["event_due"] = _event_payload(
            market=market,
            stage="early",
            status="early_gate_passed" if passed else "rejected",
            terminal=not passed,
            checks=checks,
            metrics=metrics,
            observed_at=now,
        )
        base.update(
            {
                "status": "collecting_qualification" if passed else "rejected",
                "stage": "early",
                "blockers": [name for name, value in checks.items() if not value],
            }
        )
        return base

    qualification_ready = (
        int(metrics["resolved_trades"]) >= QUALIFICATION_CALLS
        and int(metrics["resolved_sessions"]) >= QUALIFICATION_SESSIONS
    )
    if not qualification_ready:
        blockers = []
        if int(metrics["resolved_trades"]) < QUALIFICATION_CALLS:
            blockers.append("minimum_resolved_trades")
        if int(metrics["resolved_sessions"]) < QUALIFICATION_SESSIONS:
            blockers.append("minimum_resolved_sessions")
        base.update(
            {
                "status": "collecting_qualification",
                "stage": "qualification",
                "blockers": blockers,
            }
        )
        return base

    if core_event is None:
        checks = _gate_checks(metrics, early=False)
        passed = all(checks.values())
        if market == "crypto" and passed:
            status = "controls_pending"
            terminal_gate = False
        else:
            status = "qualified_for_review" if passed else "rejected"
            terminal_gate = True
        base["event_due"] = _event_payload(
            market=market,
            stage="qualification_core",
            status=status,
            terminal=terminal_gate,
            checks=checks,
            metrics=metrics,
            observed_at=now,
        )
        blockers = [name for name, value in checks.items() if not value]
        if market == "crypto" and passed:
            blockers.extend(CRYPTO_REQUIRED_CONTROLS)
        base.update({"status": status, "stage": "qualification", "blockers": blockers})
        return base

    base.update(
        {
            "status": str(core_event["status"]),
            "stage": "qualification",
            "blockers": (
                list(CRYPTO_REQUIRED_CONTROLS)
                if core_event.get("status") == "controls_pending"
                else list(core_event.get("blockers") or ())
            ),
            "first_look": core_event,
        }
    )
    return base


def record_pre_takeoff_evidence(
    market: str,
    latest: Mapping[str, Mapping[str, Any]],
    *,
    gate_path: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    """Persist any first-look gate exactly once, then return the fail-closed state."""

    events = _load_hash_chain(gate_path, label="pre-takeoff qualification")
    for _ in range(2):
        report = evaluate_pre_takeoff_evidence(
            market, latest, gate_events=events, observed_at=observed_at
        )
        due = report.get("event_due")
        if not due:
            return report
        events.append(_append_hash_event(gate_path, due, label="pre-takeoff qualification"))
    return evaluate_pre_takeoff_evidence(
        market, latest, gate_events=events, observed_at=observed_at
    )


def load_pre_takeoff_evidence(
    market: str, *, output_root: Path, observed_at: datetime | None = None
) -> dict[str, Any]:
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported pre-takeoff evidence market: {market}")
    plan_events = _load_hash_chain(output_root / market / "plans.jsonl", label="pre-takeoff plan")
    gate_events = _load_hash_chain(
        output_root / market / "qualification.jsonl",
        label="pre-takeoff qualification",
    )
    return evaluate_pre_takeoff_evidence(
        market,
        _latest_plans(plan_events),
        gate_events=gate_events,
        observed_at=observed_at,
    )


def _previous_weekday(value: datetime) -> datetime:
    candidate = value
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _equity_schedule(now: datetime) -> tuple[datetime, datetime, bool]:
    first = datetime.combine(now.date(), time(9, 31), tzinfo=IST)
    last = datetime.combine(now.date(), time(15, 31), tzinfo=IST)
    in_collection_window = now.weekday() < 5 and first <= now <= last + timedelta(minutes=15)
    if now.weekday() < 5 and now >= first:
        capped = min(now, last)
        elapsed = max(0, int((capped - first).total_seconds() // 60))
        previous = first + timedelta(minutes=15 * (elapsed // 15))
    else:
        prior = _previous_weekday(now - timedelta(days=1))
        previous = datetime.combine(prior.date(), time(15, 31), tzinfo=IST)
    if now.weekday() < 5 and now < first:
        next_due = first
    elif now.weekday() < 5 and now < last:
        elapsed = max(0, int((now - first).total_seconds() // 60))
        next_due = first + timedelta(minutes=15 * (elapsed // 15 + 1))
    else:
        candidate = now + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        next_due = datetime.combine(candidate.date(), time(9, 31), tzinfo=IST)
    return previous, next_due, in_collection_window


def pre_takeoff_collector_health(
    market: str,
    *,
    last_observed_at: str | None,
    latest_completed_bar: str | None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported pre-takeoff health market: {market}")
    now = (observed_at or datetime.now(IST)).astimezone(IST)
    if not last_observed_at:
        return {
            "status": "not_collected",
            "healthy": False,
            "last_observed_at": None,
            "latest_completed_bar": latest_completed_bar,
            "age_minutes": None,
            "next_expected_at": None,
            "detail": "No collector artifact exists; unavailable is not a pass.",
        }
    last = _as_ist(last_observed_at)
    age_minutes = max(0.0, (now - last).total_seconds() / 60.0)
    if market == "crypto":
        healthy = age_minutes <= 45.0
        return {
            "status": "healthy" if healthy else "stale",
            "healthy": healthy,
            "last_observed_at": last.isoformat(),
            "latest_completed_bar": latest_completed_bar,
            "age_minutes": age_minutes,
            "next_expected_at": (last + timedelta(minutes=15)).isoformat(),
            "detail": (
                "Crypto evidence is within the 45-minute freshness allowance."
                if healthy
                else "Crypto evidence missed the 45-minute freshness allowance."
            ),
        }
    previous_due, next_due, in_window = _equity_schedule(now)
    healthy = last >= previous_due - timedelta(minutes=45)
    status = "healthy" if healthy and in_window else "waiting_for_market_session"
    if not healthy:
        status = "stale"
    return {
        "status": status,
        "healthy": healthy,
        "last_observed_at": last.isoformat(),
        "latest_completed_bar": latest_completed_bar,
        "age_minutes": age_minutes,
        "previous_expected_at": previous_due.isoformat(),
        "next_expected_at": next_due.isoformat(),
        "detail": (
            "Collector is current for the active equity window."
            if status == "healthy"
            else (
                "Collector is current through the previous scheduled equity window; "
                "waiting for the next market session."
                if status == "waiting_for_market_session"
                else "Equity evidence is older than the latest expected scheduled run."
            )
        ),
    }


__all__ = [
    "evaluate_pre_takeoff_evidence",
    "load_pre_takeoff_evidence",
    "pre_takeoff_collector_health",
    "record_pre_takeoff_evidence",
]
