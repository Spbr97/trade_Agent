"""Forward-only M22 feed-aware crypto momentum-ignition challenger.

M22 preserves M21's feature, signal, economics, entry, and outcome contracts.  Its only
experimental change is to separate complete market-data coverage from a coin's current
feature/tradeability validity, so a dormant but still-listed pair cannot masquerade as a
feed outage or veto otherwise valid cross-sectional evidence.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle
from tradedesk.intraday_contract_race import _atomic_json, _hash_file
from tradedesk.momentum_ignition_watch import (
    ACTIVE_STATES,
    REGISTRATION_SIZE,
    RESOLVED_STATES,
    _append_hash_event,
    _append_plan_event,
    _economics_contract,
    _evaluate_plan,
    _features,
    _latest_plans,
    _load_hash_chain,
    _load_plan_events,
    _plan_ledger,
    _qualification_ledger,
    _registration_blocker,
    _snapshot_ledger,
    build_momentum_ignition_watch,
    load_momentum_ignition_watch,
    persist_momentum_ignition_watch,
)
from tradedesk.pre_takeoff_evidence import evaluate_pre_takeoff_evidence
from tradedesk.pre_takeoff_watch import _frame, _latest_completed_bar
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "momentum-ignition-watch-m22-v1"
PLAN_VERSION = "momentum-ignition-plan-m22-v1"
CONTRACT_VERSION = "momentum-ignition-m22-contract-v1"
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/momentum_ignition_watch_m22")

FEED_WINDOW_BARS = 96
MIN_REQUESTED_PAIRS = 300
MIN_ACTIVATION_OVERLAP = 0.90
MAX_ACTIVATION_GROWTH = 1.10
MIN_FEED_COVERAGE = 0.95
MIN_FEATURE_VALID_ROWS = 200
MIN_FEATURE_BREADTH = 0.60


def _contract() -> dict[str, Any]:
    inherited_implementation = Path(__file__).with_name("momentum_ignition_watch.py")
    return {
        "version": CONTRACT_VERSION,
        "feed_window_bars": FEED_WINDOW_BARS,
        "minimum_requested_pairs": MIN_REQUESTED_PAIRS,
        "minimum_activation_overlap": MIN_ACTIVATION_OVERLAP,
        "maximum_activation_growth": MAX_ACTIVATION_GROWTH,
        "minimum_feed_coverage": MIN_FEED_COVERAGE,
        "minimum_feature_valid_rows": MIN_FEATURE_VALID_ROWS,
        "minimum_feature_breadth": MIN_FEATURE_BREADTH,
        "signal_contract": "momentum-ignition-watch-v1-unchanged",
        "signal_implementation_sha256": _hash_file(inherited_implementation),
        "plan_contract": PLAN_VERSION,
        "authority": "research_watch_only",
    }


def _activation_path(output_root: Path) -> Path:
    return output_root / "crypto" / "activation.json"


def _load_activation(output_root: Path) -> dict[str, Any] | None:
    path = _activation_path(output_root)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("M22 activation manifest is not an object")
    value = {str(key): item for key, item in raw.items()}
    supplied = value.get("manifest_sha256")
    body = dict(value)
    body.pop("manifest_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M22 activation manifest hash mismatch")
    if value.get("contract_sha256") != canonical_sha256(_contract()):
        raise ValueError("M22 activation contract mismatch")
    return value


def _load_or_create_activation(
    codes: Sequence[str], *, observed_at: datetime, output_root: Path
) -> dict[str, Any]:
    existing = _load_activation(output_root)
    if existing is not None:
        return existing

    path = _activation_path(output_root)
    contract = _contract()
    contract_sha256 = canonical_sha256(contract)
    activation_codes = sorted({str(code) for code in codes})
    value: dict[str, Any] = {
        "version": "momentum-ignition-m22-activation-v1",
        "activated_at": observed_at.astimezone(IST).isoformat(),
        "codes": activation_codes,
        "activation_count": len(activation_codes),
        "contract": contract,
        "contract_sha256": contract_sha256,
    }
    value["manifest_sha256"] = canonical_sha256(value)
    _atomic_json(path, value)
    return value


def _aware_timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"M22 {label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"M22 {label} timestamp is timezone-naive")
    return parsed.astimezone(UTC)


def _artifact_paths(output_root: Path) -> tuple[Path, ...]:
    root = output_root / "crypto"
    return (
        root / "latest.json",
        root / "snapshots.jsonl",
        root / "plans.jsonl",
        root / "qualification.jsonl",
    )


def _validate_m22_artifacts(
    output_root: Path, activation: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reject valid-but-misplaced or pre-activation evidence before it can be used."""

    activation_hash = str(activation.get("manifest_sha256") or "")
    activated_at = _aware_timestamp(activation.get("activated_at"), label="activation")
    contract_hash = str(activation.get("contract_sha256") or "")

    snapshots = _load_hash_chain(
        _snapshot_ledger(output_root), label="M22 momentum-ignition snapshot"
    )
    for event in snapshots:
        if event.get("version") != VERSION:
            raise ValueError("M22 snapshot namespace mismatch")
        guard = event.get("activation_guard")
        if not isinstance(guard, Mapping) or guard.get(
            "activation_manifest_sha256"
        ) != activation_hash:
            raise ValueError("M22 snapshot activation mismatch")
        source = event.get("source")
        if not isinstance(source, Mapping) or source.get("contract_sha256") != contract_hash:
            raise ValueError("M22 snapshot contract mismatch")
        if _aware_timestamp(event.get("observed_at"), label="snapshot") < activated_at:
            raise ValueError("M22 snapshot predates activation")

    plans = _load_plan_events(_plan_ledger(output_root))
    for event in plans:
        if event.get("version") != PLAN_VERSION:
            raise ValueError("M22 plan namespace mismatch")
        if event.get("market") != "crypto":
            raise ValueError("M22 plan market mismatch")
        if _aware_timestamp(event.get("armed_at"), label="plan activation") < activated_at:
            raise ValueError("M22 plan predates activation")
        try:
            expected_plan_id = canonical_sha256(
                {
                    "version": PLAN_VERSION,
                    "scrip_code": event["scrip_code"],
                    "decision_bar": event["decision_bar"],
                    "mechanism": event["mechanism"],
                    "trigger": event["trigger"],
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("M22 plan identity is incomplete") from exc
        if event.get("plan_id") != expected_plan_id:
            raise ValueError("M22 plan identity mismatch")

    qualification_events = _load_hash_chain(
        _qualification_ledger(output_root), label="M22 momentum-ignition qualification"
    )
    for event in qualification_events:
        if event.get("evidence_namespace") != VERSION:
            raise ValueError("M22 qualification namespace mismatch")
        if event.get("activation_manifest_sha256") != activation_hash:
            raise ValueError("M22 qualification activation mismatch")
        if event.get("market") != "crypto":
            raise ValueError("M22 qualification market mismatch")
        if _aware_timestamp(event.get("observed_at"), label="qualification") < activated_at:
            raise ValueError("M22 qualification predates activation")
    return plans, qualification_events


def _record_m22_evidence(
    latest: Mapping[str, Mapping[str, Any]],
    *,
    activation: Mapping[str, Any],
    output_root: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    """Persist a first look bound to this M22 namespace and activation cohort."""

    _, events = _validate_m22_artifacts(output_root, activation)
    path = _qualification_ledger(output_root)
    for _ in range(2):
        report = evaluate_pre_takeoff_evidence(
            "crypto", latest, gate_events=events, observed_at=observed_at
        )
        due = report.get("event_due")
        if not due:
            return report
        payload = {
            **dict(due),
            "evidence_namespace": VERSION,
            "activation_manifest_sha256": activation["manifest_sha256"],
        }
        events.append(
            _append_hash_event(path, payload, label="M22 momentum-ignition qualification")
        )
    return evaluate_pre_takeoff_evidence(
        "crypto", latest, gate_events=events, observed_at=observed_at
    )


def _expected_index(observed_at: datetime) -> pd.DatetimeIndex:
    observed = pd.Timestamp(observed_at)
    if observed.tzinfo is None:
        observed = observed.tz_localize(IST)
    last_open = observed.tz_convert(UTC).floor("15min") - pd.Timedelta(minutes=15)
    return pd.date_range(
        end=last_open,
        periods=FEED_WINDOW_BARS,
        freq="15min",
        tz=UTC,
    )


def _feed_invalid_reason(
    source: Sequence[Candle] | pd.DataFrame, *, observed_at: datetime
) -> str | None:
    required = ("open", "high", "low", "close", "volume")
    if isinstance(source, pd.DataFrame):
        if any(name not in source.columns for name in required):
            return "missing_ohlcv_column"
        if source.index.duplicated().any():
            return "duplicate_timestamp"
    else:
        timestamps = [value.ts for value in source]
        if len(timestamps) != len(set(timestamps)):
            return "duplicate_timestamp"
    completed = _frame(source, observed_at)
    if len(completed) != FEED_WINDOW_BARS:
        return "incomplete_96_bar_window"
    index = pd.DatetimeIndex(completed.index)
    if index.tz is None:
        return "timezone_missing"
    if not index.tz_convert(UTC).equals(_expected_index(observed_at)):
        return "stale_gapped_or_misaligned_window"
    values = completed[list(required)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        return "non_finite_ohlcv"
    open_ = completed["open"].to_numpy(dtype=float)
    high = completed["high"].to_numpy(dtype=float)
    low = completed["low"].to_numpy(dtype=float)
    close = completed["close"].to_numpy(dtype=float)
    volume = completed["volume"].to_numpy(dtype=float)
    if any(np.any(value <= 0.0) for value in (open_, high, low, close)):
        return "non_positive_ohlc"
    if (
        np.any(high < low)
        or np.any(open_ < low)
        or np.any(open_ > high)
        or np.any(close < low)
        or np.any(close > high)
    ):
        return "invalid_ohlc_geometry"
    if np.any(volume < 0.0):
        return "negative_volume"
    return None


def _feature_invalid_reason(frame: pd.DataFrame) -> str | None:
    if _features(frame) is not None:
        return None
    values = frame.tail(32)
    close = values["close"].to_numpy(dtype=float)
    high = values["high"].to_numpy(dtype=float)
    low = values["low"].to_numpy(dtype=float)
    volume = values["volume"].to_numpy(dtype=float)
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    if float(np.mean(true_range[-14:])) <= 0.0:
        return "nonpositive_atr14"
    if float(np.mean(true_range[-21:-1])) <= 0.0:
        return "nonpositive_prior_tr20"
    if float(np.mean(volume[-21:-1])) <= 0.0:
        return "nonpositive_prior_volume20"
    if float(np.mean(volume[-4:-1])) <= 0.0:
        return "nonpositive_prior_volume3"
    if float(np.sum(volume[-24:])) <= 0.0:
        return "nonpositive_vwap24_volume"
    if float(np.sum(volume[-25:-1])) <= 0.0:
        return "nonpositive_previous_vwap24_volume"
    return "nonfinite_required_feature"


def build_momentum_ignition_watch_m22(
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    activation: Mapping[str, Any],
    plan_history: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build M22 while preserving M21 signal behavior on every valid row."""

    requested_codes = sorted({str(value) for value in shortlist["scrip_code"].tolist()})
    activation_codes = {str(value) for value in activation.get("codes") or ()}
    activation_count = int(activation.get("activation_count") or len(activation_codes))
    feed_reasons: Counter[str] = Counter()
    feature_reasons: Counter[str] = Counter()
    feed_valid = 0
    feature_valid = 0
    feature_valid_codes: set[str] = set()
    for code in requested_codes:
        source = frames.get(code, ())
        feed_reason = _feed_invalid_reason(source, observed_at=observed_at)
        if feed_reason is not None:
            feed_reasons[feed_reason] += 1
            continue
        feed_valid += 1
        completed = _frame(source, observed_at)
        feature_reason = _feature_invalid_reason(completed)
        if feature_reason is None:
            feature_valid += 1
            feature_valid_codes.add(code)
        else:
            feature_reasons[feature_reason] += 1

    requested = len(requested_codes)
    feed_coverage = feed_valid / requested if requested else 0.0
    feature_breadth = feature_valid / feed_valid if feed_valid else 0.0
    minimum_requested = max(
        MIN_REQUESTED_PAIRS,
        math.ceil(activation_count * MIN_ACTIVATION_OVERLAP),
    )
    maximum_requested = math.floor(activation_count * MAX_ACTIVATION_GROWTH)
    activation_overlap_count = len(set(requested_codes) & activation_codes)
    activation_overlap = (
        activation_overlap_count / activation_count if activation_count else 0.0
    )
    activation_guard_open = (
        minimum_requested <= requested <= maximum_requested
        and activation_overlap >= MIN_ACTIVATION_OVERLAP
    )
    feed_gate_open = feed_coverage >= MIN_FEED_COVERAGE
    feature_gate_open = (
        feature_valid >= MIN_FEATURE_VALID_ROWS
        and feature_breadth >= MIN_FEATURE_BREADTH
    )
    registration_blockers: list[str] = []
    if not activation_guard_open:
        registration_blockers.append("activation_universe_drift")
    if not feed_gate_open:
        registration_blockers.append("insufficient_feed_coverage")
    if not feature_gate_open:
        registration_blockers.append("insufficient_feature_breadth")

    feature_shortlist = shortlist[
        shortlist["scrip_code"].astype(str).isin(feature_valid_codes)
    ].copy()
    feature_frames = {code: frames[code] for code in feature_valid_codes if code in frames}
    report, registration = build_momentum_ignition_watch(
        feature_shortlist,
        feature_frames,
        observed_at=observed_at,
        plan_history=plan_history,
    )
    if registration_blockers:
        for candidate in report.get("candidates") or ():
            candidate["blockers"] = [
                *list(candidate.get("blockers") or ()),
                *registration_blockers,
            ]
            candidate["state"] = "observe"
            candidate["decision"] = "NO RESEARCH PLAN"
        registration = []
        report["status"] = registration_blockers[0]
        report["registration_candidates"] = 0
        report["registration_candidate_pool"] = 0
        report["watch_candidates"] = 0
        report["ignition_candidates"] = 0
        report["reclaim_candidates"] = 0
    report.update(
        {
            "version": VERSION,
            "program": "M22_feed_aware_momentum_ignition",
            "shortlist_rows": requested,
            "requested_pairs": requested,
            "intraday_valid_rows": feature_valid,
            "intraday_invalid_rows": requested - feature_valid,
            "universe_coverage": feed_coverage,
            "feature_universe_coverage": feature_valid / requested if requested else 0.0,
            "feed_window_bars": FEED_WINDOW_BARS,
            "feed_valid_rows": feed_valid,
            "feed_invalid_rows": requested - feed_valid,
            "feed_valid_pairs": feed_valid,
            "feed_invalid_pairs": requested - feed_valid,
            "feed_coverage": feed_coverage,
            "minimum_feed_coverage": MIN_FEED_COVERAGE,
            "feature_valid_rows": feature_valid,
            "feature_invalid_rows": feed_valid - feature_valid,
            "feature_valid_pairs": feature_valid,
            "feature_invalid_pairs": feed_valid - feature_valid,
            "feature_breadth": feature_breadth,
            "feature_coverage_of_feed": feature_breadth,
            "minimum_feature_valid_rows": MIN_FEATURE_VALID_ROWS,
            "minimum_feature_breadth": MIN_FEATURE_BREADTH,
            "minimum_feature_coverage_of_feed": MIN_FEATURE_BREADTH,
            "feed_invalid_reason_counts": dict(sorted(feed_reasons.items())),
            "feature_invalid_reason_counts": dict(sorted(feature_reasons.items())),
            "activation_guard": {
                "open": activation_guard_open,
                "activation_count": activation_count,
                "activation_manifest_sha256": activation.get("manifest_sha256"),
                "requested_pairs": requested,
                "minimum_requested_pairs": minimum_requested,
                "maximum_requested_pairs": maximum_requested,
                "activation_overlap_count": activation_overlap_count,
                "activation_overlap": activation_overlap,
                "minimum_activation_overlap": MIN_ACTIVATION_OVERLAP,
            },
            "feed_gate_open": feed_gate_open,
            "feature_gate_open": feature_gate_open,
            "registration_gate_open": not registration_blockers,
            "manifest_gate_passed": activation_guard_open,
            "feed_gate_passed": feed_gate_open,
            "feature_gate_passed": feature_gate_open,
            "coverage_gate_passed": not registration_blockers,
            "coverage_blockers": registration_blockers,
            "coverage_semantics": (
                "feed completeness gates registration; inactive rows remain feature-invalid "
                "and receive no percentile, rank, or plan"
            ),
            "minimum_universe_coverage": MIN_FEED_COVERAGE,
            "detail": (
                "M22 is a prospective feed-aware successor to frozen M21. It monitors "
                "every active CoinDCX INR pair, separates exact completed-bar feed "
                "coverage from feature-valid breadth, and otherwise preserves M21's "
                "signal, economics, confirmation, and outcome rules."
            ),
        }
    )
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    return report, registration


def _update_m22_plans(
    report: Mapping[str, Any],
    registration_candidates: Sequence[Mapping[str, Any]],
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    activation: Mapping[str, Any],
    observed_at: datetime,
    output_root: Path,
    allow_new_plans: bool,
) -> dict[str, Any]:
    """Run the frozen M21 lifecycle with an M22-namespaced plan identity."""

    path = _plan_ledger(output_root)
    events = _load_plan_events(path)
    latest = _latest_plans(events)
    frame_views = {code: _frame(value, observed_at) for code, value in frames.items()}
    for plan_id, plan in list(latest.items()):
        frame = frame_views.get(str(plan["scrip_code"]))
        if frame is None:
            frame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        update = _evaluate_plan(plan, frame, observed_at=observed_at)
        if update is not None:
            latest[plan_id] = _append_plan_event(path, update)

    evidence = _record_m22_evidence(
        latest,
        activation=activation,
        output_root=output_root,
        observed_at=observed_at,
    )
    registration_open = allow_new_plans and not bool(evidence["terminal"])
    new_plans_registered = 0
    registration_blockers: Counter[str] = Counter()
    for candidate in registration_candidates if registration_open else ():
        if new_plans_registered >= REGISTRATION_SIZE:
            break
        if candidate.get("state") != "arming":
            continue
        blocker = _registration_blocker(candidate, latest=latest, observed_at=observed_at)
        if blocker:
            registration_blockers[blocker] += 1
            continue
        decision_bar = str(candidate["features"]["last_closed_bar"])
        plan_id = canonical_sha256(
            {
                "version": PLAN_VERSION,
                "scrip_code": candidate["scrip_code"],
                "decision_bar": decision_bar,
                "mechanism": candidate["mechanism"],
                "trigger": candidate["trigger"],
            }
        )
        if plan_id in latest:
            registration_blockers["duplicate_plan"] += 1
            continue
        economics_contract = _economics_contract()
        new_plan: dict[str, Any] = {
            "version": PLAN_VERSION,
            "event": "plan_armed",
            "plan_id": plan_id,
            "market": "crypto",
            "scrip_code": str(candidate["scrip_code"]),
            "symbol": str(candidate["symbol"]),
            "mechanism": str(candidate["mechanism"]),
            "prior_failure_plan_id": candidate.get("prior_failure_plan_id"),
            "armed_at": observed_at.astimezone(IST).isoformat(),
            "decision_bar": decision_bar,
            "last_evaluated_bar": decision_bar,
            "state": "pending_trigger",
            "bars_seen": 0,
            "trigger_bars_seen": 0,
            "trigger": float(candidate["trigger"]),
            "stop": float(candidate["stop"]),
            "target": float(candidate["target"]),
            "target_r_multiple": float(candidate["target_r_multiple"]),
            "target_net_r": float(candidate["target_net_r"]),
            "stop_net_r": float(candidate["stop_net_r"]),
            "break_even_win_rate": float(candidate["break_even_win_rate"]),
            "features": dict(candidate["features"]),
            "economics_contract": economics_contract,
            "economics_contract_sha256": canonical_sha256(economics_contract),
            "momentum_score": float(candidate["momentum_score"]),
            "opportunity_probability": candidate.get("opportunity_probability"),
            "source_snapshot_sha256": report.get("snapshot_sha256"),
            "observed_at": observed_at.astimezone(IST).isoformat(),
            "authority": "research_watch_only",
        }
        latest[plan_id] = _append_plan_event(path, new_plan)
        new_plans_registered += 1

    states = Counter(str(plan["state"]) for plan in latest.values())
    resolved = [plan for plan in latest.values() if str(plan["state"]) in RESOLVED_STATES]
    successes = sum(bool(plan.get("strict_success")) for plan in resolved)
    net_values = [float(plan["net_r"]) for plan in resolved if plan.get("net_r") is not None]
    return {
        "total_plans": len(latest),
        "states": dict(sorted(states.items())),
        "active_plans": sum(count for state, count in states.items() if state in ACTIVE_STATES),
        "resolved_trades": len(resolved),
        "strict_successes": successes,
        "strict_accuracy": successes / len(resolved) if resolved else None,
        "mean_net_r": float(np.mean(net_values)) if net_values else None,
        "new_plan_registration_open": registration_open,
        "new_plans_registered": new_plans_registered,
        "registration_blockers": dict(sorted(registration_blockers.items())),
        "evidence": evidence,
        "plans": [
            {
                "plan_id": plan["plan_id"],
                "symbol": plan["symbol"],
                "mechanism": plan.get("mechanism"),
                "state": plan["state"],
                "armed_at": plan["armed_at"],
                "trigger": plan["trigger"],
                "stop": plan["stop"],
                "target": plan["target"],
                "target_r_multiple": plan.get("target_r_multiple"),
                "target_net_r": plan.get("target_net_r_at_entry", plan.get("target_net_r")),
                "break_even_win_rate": plan.get(
                    "break_even_win_rate_at_entry", plan.get("break_even_win_rate")
                ),
                "net_r": plan.get("net_r"),
                "strict_success": plan.get("strict_success"),
            }
            for plan in sorted(
                latest.values(), key=lambda value: str(value["armed_at"]), reverse=True
            )
        ][:20],
    }


def collect_momentum_ignition_watch_m22(
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect one M22 snapshot from the same already-fetched rows used by M20/M21."""

    # The base loader verifies the independent hash chains before any mutation. M22 then
    # enforces its own namespace and activation boundary over every historical event.
    load_momentum_ignition_watch(
        "crypto", output_root=output_root, observed_at=observed_at
    )
    codes = sorted({str(value) for value in shortlist["scrip_code"].tolist()})
    activation = _load_or_create_activation(
        codes, observed_at=observed_at, output_root=output_root
    )
    history, _ = _validate_m22_artifacts(output_root, activation)
    report, registration = build_momentum_ignition_watch_m22(
        shortlist,
        frames,
        observed_at=observed_at,
        activation=activation,
        plan_history=history,
    )
    report["source"] = {
        **dict(source or {}),
        "universe_contract": CONTRACT_VERSION,
        "contract_sha256": canonical_sha256(_contract()),
    }
    latest_bar = _latest_completed_bar(frames, observed_at)
    report["latest_completed_bar"] = latest_bar.isoformat() if latest_bar else None
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    evaluation_frames: dict[str, Sequence[Candle] | pd.DataFrame] = {}
    for code, frame in frames.items():
        if _feed_invalid_reason(frame, observed_at=observed_at) is None:
            evaluation_frames[code] = frame
        else:
            evaluation_frames[code] = pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"],
                index=pd.DatetimeIndex([], tz=UTC),
            )
    report["watch_plans"] = _update_m22_plans(
        report,
        registration,
        evaluation_frames,
        activation=activation,
        observed_at=observed_at,
        output_root=output_root,
        allow_new_plans=bool(report["registration_gate_open"]),
    )
    report["new_plan_registration_open"] = report["watch_plans"][
        "new_plan_registration_open"
    ]
    persisted = persist_momentum_ignition_watch(report, output_root=output_root)
    _validate_m22_artifacts(output_root, activation)
    return persisted


def load_momentum_ignition_watch_m22(
    market: str,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Load verified M22 state without pooling any M21 artifact."""

    if market != "crypto":
        return {
            "version": VERSION,
            "market": market,
            "status": "not_applicable",
            "candidates": [],
            "tradeable_calls": 0,
            "authority": "research_watch_only",
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
            "detail": "M22 is intentionally not applicable outside crypto.",
        }
    report = load_momentum_ignition_watch(
        market, output_root=output_root, observed_at=observed_at
    )
    activation = _load_activation(output_root)
    if activation is None:
        if any(path.exists() for path in _artifact_paths(output_root)):
            raise ValueError("M22 activation manifest is missing")
    else:
        plan_events, qualification_events = _validate_m22_artifacts(output_root, activation)
        latest = _latest_plans(plan_events)
        plans = dict(report.get("watch_plans") or {})
        plans["evidence"] = evaluate_pre_takeoff_evidence(
            "crypto",
            latest,
            gate_events=qualification_events,
            observed_at=observed_at,
        )
        report["watch_plans"] = plans
    if report.get("status") == "not_collected":
        report["version"] = VERSION
        report["detail"] = (
            "No forward M22 snapshot has been collected; unavailable is not a pass."
        )
    return report


__all__ = [
    "build_momentum_ignition_watch_m22",
    "collect_momentum_ignition_watch_m22",
    "load_momentum_ignition_watch_m22",
]
