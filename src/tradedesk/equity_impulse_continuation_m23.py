"""Forward-only M23 equity opening-impulse continuation research.

M20 deliberately looks for compression below resistance.  M23 is isolated from that
denominator and follows a different causal pattern: a liquid opening impulse that holds
above the previous close, then breaks the already-observed session high on a later bar.
It has no live-order authority.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle
from tradedesk.intraday_contract_race import (
    RESEARCH_CAPITAL,
    RISK_BUDGET,
    _atomic_json,
    _hash_file,
    _market_bundle,
)
from tradedesk.models import TradeType, price_decimal
from tradedesk.pre_takeoff_evidence import pre_takeoff_collector_health
from tradedesk.pre_takeoff_watch import _frame, _latest_completed_bar
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "equity-impulse-continuation-m23-v1"
PLAN_VERSION = "equity-impulse-continuation-plan-m23-v1"
CONTRACT_VERSION = "equity-impulse-continuation-m23-contract-v1"
ACTIVATION_VERSION = "equity-impulse-continuation-m23-activation-v1"
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/equity_impulse_continuation_m23")

MIN_SESSION_BARS = 4
REFERENCE_BARS = 32
MAX_NEW_PLANS = 3
DISPLAY_SIZE = 12
MIN_RESOLVED_TRADES = 60
MIN_RESOLVED_SESSIONS = 40
MIN_MEAN_NET_R = 0.05
MIN_PROFIT_FACTOR = 1.20
MIN_RANDOM_TIMING_MARGIN_R = 0.10
MAX_LOSING_STREAK = 6
RESOLVED_STATES = frozenset({"target", "stop", "timeout"})
ACTIVE_STATES = frozenset({"pending_trigger", "triggered"})
TERMINAL_STATES = frozenset({*RESOLVED_STATES, "expired", "invalidated"})


def _protocol_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "self-learning-m23-equity-impulse-continuation-protocol.md"
    )


def _contract() -> dict[str, Any]:
    return {
        "version": CONTRACT_VERSION,
        "markets": ["nse", "bse"],
        "timeframe": "M15_completed_bars_only",
        "minimum_session_bars": MIN_SESSION_BARS,
        "reference_bars": REFERENCE_BARS,
        "opening_return_bounds": [0.02, 0.15],
        "opening_volume_multiple": 3.0,
        "opening_close_location_minimum": 0.70,
        "maximum_distance_below_session_high": 0.06,
        "minimum_close_to_session_vwap": 0.985,
        "trigger_buffer": 0.001,
        "stop_vwap_multiple": 0.985,
        "target_r": 0.75,
        "maximum_new_plans_per_snapshot": MAX_NEW_PLANS,
        "one_plan_per_symbol_per_session": True,
        "minimum_resolved_trades": MIN_RESOLVED_TRADES,
        "minimum_resolved_sessions": MIN_RESOLVED_SESSIONS,
        "minimum_mean_net_r": MIN_MEAN_NET_R,
        "expectancy_confidence": "two_sided_normal_95_lower_bound",
        "minimum_profit_factor": MIN_PROFIT_FACTOR,
        "minimum_random_timing_margin_r": MIN_RANDOM_TIMING_MARGIN_R,
        "maximum_losing_streak": MAX_LOSING_STREAK,
        "accuracy_role": "descriptive_only",
        "authority": "research_watch_only",
    }


def _activation_path(output_root: Path, market: str) -> Path:
    return output_root / market / "activation.json"


def _latest_path(output_root: Path, market: str) -> Path:
    return output_root / market / "latest.json"


def _plan_path(output_root: Path, market: str) -> Path:
    return output_root / market / "plans.jsonl"


def _snapshot_path(output_root: Path, market: str) -> Path:
    return output_root / market / "snapshots.jsonl"


def _activation_body(market: str, activated_at: datetime) -> dict[str, Any]:
    protocol = _protocol_path()
    implementation = Path(__file__).resolve()
    return {
        "version": ACTIVATION_VERSION,
        "market": market,
        "activated_at": activated_at.astimezone(IST).isoformat(),
        "contract": _contract(),
        "contract_sha256": canonical_sha256(_contract()),
        "protocol_path": protocol.name,
        "protocol_sha256": _hash_file(protocol),
        "implementation_sha256": _hash_file(implementation),
        "forward_only": True,
        "examples_counted": False,
        "backfill_allowed": False,
    }


def _validate_activation(value: Mapping[str, Any], *, market: str) -> None:
    supplied = value.get("manifest_sha256")
    body = dict(value)
    body.pop("manifest_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M23 activation manifest hash mismatch")
    if value.get("version") != ACTIVATION_VERSION or value.get("market") != market:
        raise ValueError("M23 activation identity mismatch")
    if value.get("contract_sha256") != canonical_sha256(_contract()):
        raise ValueError("M23 activation contract mismatch")
    if value.get("protocol_sha256") != _hash_file(_protocol_path()):
        raise ValueError("M23 frozen protocol hash mismatch")
    if value.get("implementation_sha256") != _hash_file(Path(__file__).resolve()):
        raise ValueError("M23 frozen implementation hash mismatch")


def _load_or_create_activation(
    market: str, *, observed_at: datetime, output_root: Path
) -> dict[str, Any]:
    path = _activation_path(output_root, market)
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("M23 activation manifest is not an object")
        _validate_activation(value, market=market)
        return value
    value = _activation_body(market, observed_at)
    value["manifest_sha256"] = canonical_sha256(value)
    _atomic_json(path, value)
    return value


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
            raise ValueError(f"M23 {label} hash-chain failure at line {line_number}")
        events.append(value)
        previous = str(supplied)
    return events


def _append_hash_event(
    path: Path, payload: Mapping[str, Any], *, label: str
) -> dict[str, Any]:
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


def _local_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(IST)
    return timestamp.tz_convert(IST)


def _candidate_features(
    frame: pd.DataFrame, observed_at: datetime
) -> tuple[dict[str, Any] | None, str | None]:
    if len(frame) < REFERENCE_BARS + MIN_SESSION_BARS:
        return None, "insufficient_reference_bars"
    local_date = observed_at.astimezone(IST).date()
    local_index = pd.DatetimeIndex([_local_timestamp(value) for value in frame.index])
    current_mask = np.asarray([value.date() == local_date for value in local_index])
    current = frame.loc[current_mask]
    prior = frame.loc[~current_mask]
    if len(current) < MIN_SESSION_BARS:
        return None, "insufficient_current_session_bars"
    if len(prior) < REFERENCE_BARS:
        return None, "insufficient_prior_session_bars"
    first_time = _local_timestamp(current.index[0]).time().replace(tzinfo=None)
    if first_time != time(9, 15):
        return None, "opening_bar_missing"

    values = current[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    reference = prior.tail(REFERENCE_BARS)
    if not np.isfinite(values).all():
        return None, "non_finite_current_session_value"
    if (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        return None, "invalid_current_session_value"
    prior_close = float(reference["close"].iloc[-1])
    positive_volume = reference.loc[reference["volume"] > 0, "volume"].astype(float)
    if prior_close <= 0 or positive_volume.empty:
        return None, "invalid_reference_value"

    first = current.iloc[0]
    opening_range = float(first["high"] - first["low"])
    if opening_range <= 0:
        return None, "zero_opening_range"
    post_open = current.iloc[1:]
    total_volume = float(current["volume"].sum())
    if total_volume <= 0:
        return None, "zero_session_volume"
    typical = (current["high"] + current["low"] + current["close"]) / 3.0
    session_vwap = float((typical * current["volume"]).sum() / total_volume)
    session_high = float(current["high"].max())
    current_close = float(current["close"].iloc[-1])
    post_open_low = float(post_open["low"].min())
    opening_return = float(first["close"] / prior_close - 1.0)
    opening_volume_ratio = float(first["volume"] / positive_volume.median())
    opening_close_location = float((first["close"] - first["low"]) / opening_range)
    distance_below_high = float(current_close / session_high - 1.0)
    close_to_vwap = float(current_close / session_vwap)
    trigger = session_high * 1.001
    stop = min(post_open_low, session_vwap * 0.985)
    risk = trigger - stop
    features = {
        "decision_bar": _local_timestamp(current.index[-1]).isoformat(),
        "decision_session": local_date.isoformat(),
        "completed_session_bars": len(current),
        "prior_close": prior_close,
        "opening_close": float(first["close"]),
        "opening_return": opening_return,
        "opening_volume": float(first["volume"]),
        "reference_volume_median_32": float(positive_volume.median()),
        "opening_volume_ratio": opening_volume_ratio,
        "opening_close_location": opening_close_location,
        "post_open_low": post_open_low,
        "session_high": session_high,
        "session_vwap": session_vwap,
        "current_close": current_close,
        "distance_below_session_high": distance_below_high,
        "close_to_session_vwap": close_to_vwap,
        "trigger": trigger,
        "stop": stop,
        "target": trigger + 0.75 * risk,
    }
    checks = {
        "opening_return_minimum": opening_return >= 0.02,
        "opening_return_maximum": opening_return <= 0.15,
        "opening_volume_multiple": opening_volume_ratio >= 3.0,
        "opening_close_location": opening_close_location >= 0.70,
        "post_open_hold": post_open_low > prior_close,
        "session_high_proximity": distance_below_high >= -0.06,
        "session_vwap_hold": close_to_vwap >= 0.985,
        "valid_stop_geometry": stop < current_close and stop < trigger and risk > 0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    features["checks"] = checks
    features["failed_checks"] = failed
    if failed:
        return features, "candidate_checks_failed"
    return features, None


def build_equity_impulse_continuation(
    market: str,
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    """Build deterministic M23 candidates from only bars completed at ``observed_at``."""

    if market not in {"nse", "bse"}:
        raise ValueError("M23 supports NSE and BSE only")
    candidates: list[dict[str, Any]] = []
    invalid_reasons: Counter[str] = Counter()
    for row in shortlist.itertuples(index=False):
        code = str(row.scrip_code)
        frame = _frame(frames.get(code, ()), observed_at)
        features, invalid_reason = _candidate_features(frame, observed_at)
        if invalid_reason is not None:
            invalid_reasons[invalid_reason] += 1
            continue
        assert features is not None
        opening_return = float(features["opening_return"])
        volume_ratio = float(features["opening_volume_ratio"])
        retention = max(0.0, min(1.0, 1.0 + float(features["distance_below_session_high"]) / 0.06))
        vwap_hold = max(0.0, min(1.0, (float(features["close_to_session_vwap"]) - 0.985) / 0.04))
        score = (
            0.30 * min(opening_return / 0.15, 1.0)
            + 0.25 * min(volume_ratio / 8.0, 1.0)
            + 0.20 * float(features["opening_close_location"])
            + 0.15 * retention
            + 0.10 * vwap_hold
        )
        probability = getattr(row, "opportunity_probability", None)
        candidates.append(
            {
                "market": market,
                "scrip_code": code,
                "symbol": str(getattr(row, "symbol", code)),
                "opportunity_rank": int(getattr(row, "opportunity_rank", 0) or 0),
                "opportunity_probability": (
                    float(probability)
                    if probability is not None and math.isfinite(float(probability))
                    else None
                ),
                "state": "watch",
                "impulse_score": float(score),
                "trigger": float(features["trigger"]),
                "stop": float(features["stop"]),
                "target": float(features["target"]),
                "features": features,
                "tradeable_now": False,
                "decision": "WATCH SECOND LEG; later completed-bar trigger required",
            }
        )
    candidates.sort(key=lambda value: (-float(value["impulse_score"]), str(value["scrip_code"])))
    displayed = candidates[:DISPLAY_SIZE]
    report: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "observed_at": observed_at.astimezone(IST).isoformat(),
        "status": "watch_candidates_found" if candidates else "no_impulse_continuation_candidate",
        "shortlist_rows": len(shortlist),
        "valid_candidates": len(candidates),
        "displayed_candidates": len(displayed),
        "invalid_reason_counts": dict(sorted(invalid_reasons.items())),
        "candidates": displayed,
        "tradeable_calls": 0,
        "accuracy_role": "descriptive_only",
        "authority": "research_watch_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "detail": (
            "Opening impulses that hold above the prior close are watched for a later "
            "completed-bar second-leg trigger. A watch is not a trade or a live alert."
        ),
    }
    report["snapshot_sha256"] = canonical_sha256(report)
    return report


def _net_r(plan: Mapping[str, Any], exit_raw: float) -> float | None:
    market = str(plan["market"])
    bundle = _market_bundle(market)
    slippage = float(bundle.costs.slippage_pct)
    entry = float(plan["entry_execution"])
    stop = float(plan["stop"])
    risk_distance = entry - stop
    if risk_distance <= 0:
        return None
    raw_quantity = min(RISK_BUDGET / risk_distance, RESEARCH_CAPITAL / entry)
    quantity = float(math.floor(raw_quantity))
    if quantity <= 0 or quantity * entry < float(bundle.min_notional_inr):
        return None
    exit_execution = exit_raw * (1.0 - slippage)
    return float(
        bundle.costs.net_r_multiple(
            trade_type=TradeType.INTRADAY,
            qty=quantity,
            entry=price_decimal(entry),
            stop=price_decimal(stop),
            exit_price=price_decimal(exit_execution),
        )
    )


def _finish_plan(
    updated: dict[str, Any], *, state: str, resolved_at: pd.Timestamp, exit_raw: float | None = None
) -> dict[str, Any]:
    updated["state"] = state
    updated["resolved_at"] = _local_timestamp(resolved_at).isoformat()
    if exit_raw is not None:
        updated["exit_raw"] = float(exit_raw)
    if state in RESOLVED_STATES:
        net_r = _net_r(updated, float(updated["exit_raw"]))
        updated["net_r"] = net_r
        updated["strict_success"] = state == "target" and net_r is not None and net_r > 0
    return updated


def _evaluate_plan(
    plan: Mapping[str, Any], frame: pd.DataFrame, *, observed_at: datetime
) -> dict[str, Any] | None:
    state = str(plan["state"])
    if state in TERMINAL_STATES:
        return None
    decision_session = str(plan["decision_session"])
    last_evaluated = _local_timestamp(plan["last_evaluated_bar"])
    bars = _frame(frame, observed_at)
    if bars.empty:
        return None
    eligible_rows = [
        index
        for index in bars.index
        if _local_timestamp(index) > last_evaluated
        and _local_timestamp(index).date().isoformat() == decision_session
    ]
    updated = dict(plan)
    changed = False
    for index in eligible_rows:
        changed = True
        timestamp = _local_timestamp(index)
        bar = bars.loc[index]
        open_price = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        trigger = float(updated["trigger"])
        stop = float(updated["stop"])
        target = float(updated["target"])
        updated["last_evaluated_bar"] = timestamp.isoformat()

        if state == "pending_trigger" and high >= trigger:
            if low <= stop:
                updated["resolution_reason"] = "ambiguous_trigger_and_stop_same_bar"
                _finish_plan(updated, state="invalidated", resolved_at=timestamp)
                state = "invalidated"
                break
            raw_entry = max(trigger, open_price)
            if raw_entry >= target:
                updated["resolution_reason"] = "gap_above_frozen_target"
                _finish_plan(updated, state="invalidated", resolved_at=timestamp)
                state = "invalidated"
                break
            slippage = float(_market_bundle(str(updated["market"])).costs.slippage_pct)
            updated["entry_raw"] = raw_entry
            updated["entry_execution"] = raw_entry * (1.0 + slippage)
            updated["triggered_at"] = timestamp.isoformat()
            state = "triggered"
            updated["state"] = state
            if high >= target:
                _finish_plan(updated, state="target", resolved_at=timestamp, exit_raw=target)
                state = "target"
                break
        elif state == "triggered":
            if low <= stop:
                _finish_plan(updated, state="stop", resolved_at=timestamp, exit_raw=stop)
                state = "stop"
                break
            if high >= target:
                _finish_plan(updated, state="target", resolved_at=timestamp, exit_raw=target)
                state = "target"
                break

        if timestamp.time().replace(tzinfo=None) >= time(15, 15):
            if state == "triggered":
                _finish_plan(updated, state="timeout", resolved_at=timestamp, exit_raw=close)
                state = "timeout"
            elif state == "pending_trigger":
                updated["resolution_reason"] = "trigger_not_reached_by_session_end"
                _finish_plan(updated, state="expired", resolved_at=timestamp)
                state = "expired"
            break

    now_session = observed_at.astimezone(IST).date().isoformat()
    if state in ACTIVE_STATES and now_session > decision_session:
        same_session = bars.loc[
            [_local_timestamp(index).date().isoformat() == decision_session for index in bars.index]
        ]
        if not same_session.empty:
            last_index = _local_timestamp(same_session.index[-1])
            last_close = float(same_session["close"].iloc[-1])
            changed = True
            if state == "triggered":
                _finish_plan(updated, state="timeout", resolved_at=last_index, exit_raw=last_close)
            else:
                updated["resolution_reason"] = "trigger_not_reached_by_session_end"
                _finish_plan(updated, state="expired", resolved_at=last_index)

    if not changed:
        return None
    updated["event"] = "plan_evaluated"
    updated["observed_at"] = observed_at.astimezone(IST).isoformat()
    updated.pop("previous_sha256", None)
    updated.pop("event_sha256", None)
    return updated


def _wilson(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _maximum_losing_streak(values: Sequence[float]) -> int:
    current = maximum = 0
    for value in values:
        current = current + 1 if value <= 0 else 0
        maximum = max(maximum, current)
    return maximum


def _maximum_drawdown(values: Sequence[float]) -> float:
    peak = equity = maximum = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return float(maximum)


def evaluate_equity_impulse_evidence(
    latest: Mapping[str, Mapping[str, Any]],
    *,
    random_timing_margin_r: float | None = None,
    integrity_passed: bool = True,
) -> dict[str, Any]:
    """Evaluate M23 on profitability; accuracy is reported but cannot pass or fail it."""

    resolved = sorted(
        (dict(plan) for plan in latest.values() if str(plan.get("state")) in RESOLVED_STATES),
        key=lambda value: (str(value.get("resolved_at", "")), str(value.get("plan_id", ""))),
    )
    net_values = [float(plan["net_r"]) for plan in resolved if plan.get("net_r") is not None]
    successes = sum(bool(plan.get("strict_success")) for plan in resolved)
    wilson_lower, wilson_upper = _wilson(successes, len(resolved))
    mean_net_r = float(np.mean(net_values)) if net_values else None
    if len(net_values) >= 2:
        standard_error = float(np.std(net_values, ddof=1) / math.sqrt(len(net_values)))
        expectancy_lcb = float(mean_net_r - 1.959963984540054 * standard_error)  # type: ignore[operator]
    else:
        expectancy_lcb = None
    gross_profit = float(sum(value for value in net_values if value > 0))
    gross_loss = float(-sum(value for value in net_values if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    sessions = {
        str(plan.get("decision_session"))
        for plan in resolved
        if plan.get("decision_session")
    }
    metrics = {
        "resolved_trades": len(resolved),
        "resolved_sessions": len(sessions),
        "strict_successes": successes,
        "strict_accuracy": successes / len(resolved) if resolved else None,
        "wilson_95_lower": wilson_lower,
        "wilson_95_upper": wilson_upper,
        "accuracy_role": "descriptive_only",
        "net_r_available": len(net_values),
        "mean_net_r": mean_net_r,
        "expectancy_95_lower_r": expectancy_lcb,
        "gross_profit_r": gross_profit,
        "gross_loss_r": gross_loss,
        "profit_factor": profit_factor,
        "maximum_losing_streak": _maximum_losing_streak(net_values),
        "maximum_drawdown_r": _maximum_drawdown(net_values),
        "random_timing_margin_r": random_timing_margin_r,
    }
    checks = {
        "minimum_resolved_trades": len(resolved) >= MIN_RESOLVED_TRADES,
        "minimum_resolved_sessions": len(sessions) >= MIN_RESOLVED_SESSIONS,
        "complete_net_r": len(net_values) == len(resolved),
        "mean_net_r": mean_net_r is not None and mean_net_r >= MIN_MEAN_NET_R,
        "positive_expectancy_lower_bound": expectancy_lcb is not None and expectancy_lcb > 0,
        "profit_factor": (
            gross_profit > 0
            and (
                gross_loss == 0
                or (profit_factor is not None and profit_factor >= MIN_PROFIT_FACTOR)
            )
        ),
        "matched_random_timing_margin": (
            random_timing_margin_r is not None
            and random_timing_margin_r >= MIN_RANDOM_TIMING_MARGIN_R
        ),
        "maximum_losing_streak": _maximum_losing_streak(net_values) <= MAX_LOSING_STREAK,
        "integrity": integrity_passed,
    }
    enough_sample = checks["minimum_resolved_trades"] and checks["minimum_resolved_sessions"]
    core_checks = {
        key: value
        for key, value in checks.items()
        if key != "matched_random_timing_margin"
    }
    if not enough_sample:
        status = "collecting_profitability_evidence"
    elif not all(core_checks.values()):
        status = "profitability_gate_failed"
    elif not checks["matched_random_timing_margin"]:
        status = "matched_random_control_pending"
    else:
        status = "qualified_for_human_review"
    return {
        "version": VERSION,
        "status": status,
        "metrics": metrics,
        "checks": checks,
        "blockers": [name for name, passed in checks.items() if not passed],
        "thresholds": {
            "resolved_trades": MIN_RESOLVED_TRADES,
            "resolved_sessions": MIN_RESOLVED_SESSIONS,
            "mean_net_r": MIN_MEAN_NET_R,
            "expectancy_95_lower_r": 0.0,
            "profit_factor": MIN_PROFIT_FACTOR,
            "random_timing_margin_r": MIN_RANDOM_TIMING_MARGIN_R,
            "maximum_losing_streak": MAX_LOSING_STREAK,
            "accuracy_gate": "retired",
        },
        "eligible_for_live": False,
        "authority": "research_watch_only",
    }


def _registration_open(observed_at: datetime, latest_bar: datetime | None) -> bool:
    local = observed_at.astimezone(IST)
    return bool(
        local.weekday() < 5
        and time(10, 15) <= local.time() < time(15, 0)
        and latest_bar is not None
        and latest_bar.astimezone(IST).date() == local.date()
    )


def _update_plans(
    report: Mapping[str, Any],
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    output_root: Path,
    allow_new_plans: bool,
) -> dict[str, Any]:
    market = str(report["market"])
    path = _plan_path(output_root, market)
    latest = _latest_plans(_load_hash_chain(path, label="plan"))
    activation_sha256 = str(report["activation"]["manifest_sha256"])
    if any(
        plan.get("activation_manifest_sha256") != activation_sha256
        for plan in latest.values()
    ):
        raise ValueError("M23 plan activation binding mismatch")
    frame_views = {code: _frame(source, observed_at) for code, source in frames.items()}
    for plan_id, plan in list(latest.items()):
        frame = frame_views.get(str(plan["scrip_code"]))
        if frame is None or frame.empty:
            continue
        update = _evaluate_plan(plan, frame, observed_at=observed_at)
        if update is not None:
            latest[plan_id] = _append_hash_event(path, update, label="plan")

    current_session = observed_at.astimezone(IST).date().isoformat()
    used_codes = {
        str(plan["scrip_code"])
        for plan in latest.values()
        if str(plan.get("decision_session")) == current_session
    }
    active_codes = {
        str(plan["scrip_code"])
        for plan in latest.values()
        if str(plan.get("state")) in ACTIVE_STATES
    }
    new_plans = 0
    if allow_new_plans:
        for candidate in report.get("candidates", ()):
            if new_plans >= MAX_NEW_PLANS:
                break
            code = str(candidate["scrip_code"])
            if code in used_codes or code in active_codes:
                continue
            decision_bar = str(candidate["features"]["decision_bar"])
            plan_id = canonical_sha256(
                {
                    "version": PLAN_VERSION,
                    "market": market,
                    "scrip_code": code,
                    "decision_bar": decision_bar,
                    "trigger": candidate["trigger"],
                }
            )
            plan = {
                "version": PLAN_VERSION,
                "event": "plan_armed",
                "plan_id": plan_id,
                "market": market,
                "scrip_code": code,
                "symbol": candidate["symbol"],
                "armed_at": observed_at.astimezone(IST).isoformat(),
                "decision_bar": decision_bar,
                "decision_session": str(candidate["features"]["decision_session"]),
                "last_evaluated_bar": decision_bar,
                "state": "pending_trigger",
                "trigger": float(candidate["trigger"]),
                "stop": float(candidate["stop"]),
                "target": float(candidate["target"]),
                "impulse_score": float(candidate["impulse_score"]),
                "source_snapshot_sha256": report.get("snapshot_sha256"),
                "activation_manifest_sha256": activation_sha256,
                "observed_at": observed_at.astimezone(IST).isoformat(),
                "authority": "research_watch_only",
            }
            latest[plan_id] = _append_hash_event(path, plan, label="plan")
            used_codes.add(code)
            active_codes.add(code)
            new_plans += 1

    evidence = evaluate_equity_impulse_evidence(latest)
    states = Counter(str(plan["state"]) for plan in latest.values())
    return {
        "total_plans": len(latest),
        "states": dict(sorted(states.items())),
        "active_plans": sum(count for state, count in states.items() if state in ACTIVE_STATES),
        "new_plan_registration_open": allow_new_plans,
        "new_plans_registered": new_plans,
        "evidence": evidence,
        "plans": [
            {
                "plan_id": plan["plan_id"],
                "symbol": plan["symbol"],
                "state": plan["state"],
                "armed_at": plan["armed_at"],
                "trigger": plan["trigger"],
                "stop": plan["stop"],
                "target": plan["target"],
                "net_r": plan.get("net_r"),
                "strict_success": plan.get("strict_success"),
            }
            for plan in sorted(
                latest.values(),
                key=lambda value: str(value["armed_at"]),
                reverse=True,
            )
        ][:20],
    }


def _persist(report: Mapping[str, Any], output_root: Path) -> None:
    market = str(report["market"])
    _atomic_json(_latest_path(output_root, market), dict(report))
    ledger = _snapshot_path(output_root, market)
    existing = _load_hash_chain(ledger, label="snapshot")
    if existing and existing[-1].get("snapshot_sha256") == report.get("snapshot_sha256"):
        return
    _append_hash_event(ledger, report, label="snapshot")


def collect_equity_impulse_continuation(
    market: str,
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    """Collect M23 from the equity collector's already-fetched full-universe frames."""

    if market not in {"nse", "bse"}:
        raise ValueError("M23 supports NSE and BSE only")
    activation = _load_or_create_activation(
        market, observed_at=observed_at, output_root=output_root
    )
    report = build_equity_impulse_continuation(
        market, shortlist, frames, observed_at=observed_at
    )
    latest_bar = _latest_completed_bar(frames, observed_at)
    registration_open = _registration_open(observed_at, latest_bar)
    report.update(
        {
            "activation": {
                "activated_at": activation["activated_at"],
                "manifest_sha256": activation["manifest_sha256"],
                "contract_sha256": activation["contract_sha256"],
                "integrity_passed": True,
            },
            "active_pairs": len(shortlist),
            "requested_pairs": len(frames),
            "latest_completed_bar": latest_bar.isoformat() if latest_bar else None,
            "new_plan_registration_open": registration_open,
        }
    )
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    report["watch_plans"] = _update_plans(
        report,
        frames,
        observed_at=observed_at,
        output_root=output_root,
        allow_new_plans=registration_open,
    )
    report["new_plan_registration_open"] = report["watch_plans"][
        "new_plan_registration_open"
    ]
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    _persist(report, output_root)
    return report


def load_equity_impulse_continuation(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    if market not in {"nse", "bse", "crypto"}:
        raise ValueError("unsupported M23 market")
    if market == "crypto":
        return {
            "version": VERSION,
            "market": market,
            "status": "not_applicable",
            "candidates": [],
            "eligible_for_live": False,
            "authority": "research_watch_only",
            "detail": "M23 is an NSE/BSE opening-session experiment; crypto is not applicable.",
        }
    try:
        activation_path = _activation_path(output_root, market)
        latest = _latest_path(output_root, market)
        if not latest.exists():
            return {
                "version": VERSION,
                "market": market,
                "status": "not_collected",
                "candidates": [],
                "eligible_for_live": False,
                "accuracy_role": "descriptive_only",
                "authority": "research_watch_only",
                "detail": "No prospective M23 snapshot exists; unavailable is not a pass.",
            }
        if not activation_path.exists():
            raise ValueError("M23 activation manifest is missing")
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
        _validate_activation(activation, market=market)
        report = json.loads(latest.read_text(encoding="utf-8"))
        supplied = report.get("snapshot_sha256")
        body = dict(report)
        body.pop("snapshot_sha256", None)
        if supplied != canonical_sha256(body):
            raise ValueError("M23 latest snapshot hash mismatch")
        _load_hash_chain(_snapshot_path(output_root, market), label="snapshot")
        latest_plans = _latest_plans(
            _load_hash_chain(_plan_path(output_root, market), label="plan")
        )
        activation_sha256 = activation["manifest_sha256"]
        if any(
            plan.get("activation_manifest_sha256") != activation_sha256
            for plan in latest_plans.values()
        ):
            raise ValueError("M23 plan activation binding mismatch")
        report["watch_plans"] = {
            **dict(report.get("watch_plans") or {}),
            "evidence": evaluate_equity_impulse_evidence(latest_plans),
        }
        report["collector_health"] = pre_takeoff_collector_health(
            market,
            last_observed_at=report.get("observed_at"),
            latest_completed_bar=report.get("latest_completed_bar"),
        )
        return report
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "version": VERSION,
            "market": market,
            "status": "integrity_failed",
            "candidates": [],
            "eligible_for_live": False,
            "accuracy_role": "descriptive_only",
            "authority": "research_watch_only",
            "detail": str(exc),
        }


__all__ = [
    "build_equity_impulse_continuation",
    "collect_equity_impulse_continuation",
    "evaluate_equity_impulse_evidence",
    "load_equity_impulse_continuation",
]
