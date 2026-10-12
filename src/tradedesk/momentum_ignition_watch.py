"""Forward-only M21 crypto momentum-ignition research challenger.

M21 is deliberately isolated from the M20 compression benchmark.  It evaluates the full
crypto universe on completed M15 bars, exposes only a small display table, and registers at
most three new research plans per snapshot.  Nothing in this module can place an order or
promote a rule into production.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle
from tradedesk.intraday_contract_race import (
    RESEARCH_CAPITAL,
    RISK_BUDGET,
    _atomic_json,
    _market_bundle,
)
from tradedesk.models import TradeType, price_decimal
from tradedesk.pre_takeoff_evidence import (
    load_pre_takeoff_evidence,
    pre_takeoff_collector_health,
    record_pre_takeoff_evidence,
)
from tradedesk.pre_takeoff_watch import _frame, _latest_completed_bar
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "momentum-ignition-watch-v1"
PLAN_VERSION = "momentum-ignition-plan-v1"
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/momentum_ignition_watch")

DISPLAY_SIZE = 12
REGISTRATION_SIZE = 3
MIN_COVERAGE = 0.85
WATCH_EXPIRY_BARS = 8
TRADE_TIMEOUT_BARS = 16
COOLDOWN_BARS = 4
MAX_PLANS_PER_CODE_PER_IST_DATE = 2
RECLAIM_LOOKBACK_BARS = 8
RECLAIM_COOLDOWN_HOURS = 24
TARGET_R_GRID = (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
MAX_BREAK_EVEN_WIN_RATE = 0.70
TRIGGER_VOLUME_RATIO = 1.25
TERMINAL_STATES = frozenset({"target", "stop", "timeout", "expired", "invalidated"})
RESOLVED_STATES = frozenset({"target", "stop", "timeout"})
ACTIVE_STATES = frozenset({"pending_trigger", "triggered"})
NON_DIRECTIONAL_CRYPTO_CODES = frozenset(
    {
        "CDX_DAIINR",
        "CDX_FDUSDINR",
        "CDX_PYUSDINR",
        "CDX_TUSDINR",
        "CDX_USDCINR",
        "CDX_USDTINR",
    }
)


def _plan_ledger(output_root: Path) -> Path:
    return output_root / "crypto" / "plans.jsonl"


def _qualification_ledger(output_root: Path) -> Path:
    return output_root / "crypto" / "qualification.jsonl"


def _snapshot_ledger(output_root: Path) -> Path:
    return output_root / "crypto" / "snapshots.jsonl"


def _load_hash_chain(path: Path, *, label: str) -> list[dict[str, Any]]:
    """Read a complete chain and reject any missing, reordered, or edited event."""

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


def _verify_snapshot_state(output_root: Path) -> dict[str, Any] | None:
    """Verify both durable snapshot views before any collector mutation."""

    events = _load_hash_chain(
        _snapshot_ledger(output_root), label="momentum-ignition snapshot"
    )
    path = output_root / "crypto" / "latest.json"
    if not path.exists():
        if events:
            raise ValueError("momentum-ignition latest snapshot is missing")
        return None
    raw_value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_value, dict):
        raise ValueError("momentum-ignition latest snapshot is not an object")
    value: dict[str, Any] = {str(key): item for key, item in raw_value.items()}
    supplied = value.get("snapshot_sha256")
    body = dict(value)
    body.pop("snapshot_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("momentum-ignition latest snapshot hash mismatch")
    if not events:
        raise ValueError("momentum-ignition snapshot ledger is missing")
    if events[-1].get("snapshot_sha256") != supplied:
        raise ValueError("momentum-ignition latest snapshot is not the ledger head")
    return value


def _load_plan_events(path: Path) -> list[dict[str, Any]]:
    return _load_hash_chain(path, label="momentum-ignition plan")


def _append_plan_event(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return _append_hash_event(path, payload, label="momentum-ignition plan")


def load_momentum_ignition_plan_events(
    *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> list[dict[str, Any]]:
    """Expose the verified M21 history for collector integration and audits."""

    return _load_plan_events(_plan_ledger(output_root))


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


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0.0 and math.isfinite(denominator) else math.nan


def _features(frame: pd.DataFrame) -> dict[str, Any] | None:
    """Compute causal features from one instrument's completed M15 bars."""

    if len(frame) < 32:
        return None
    values = frame.tail(32)
    close = values["close"].to_numpy(dtype=float)
    open_ = values["open"].to_numpy(dtype=float)
    high = values["high"].to_numpy(dtype=float)
    low = values["low"].to_numpy(dtype=float)
    volume = values["volume"].to_numpy(dtype=float)
    if not all(np.isfinite(item).all() for item in (open_, close, high, low, volume)):
        return None
    if (
        np.any(open_ <= 0.0)
        or np.any(close <= 0.0)
        or np.any(low <= 0.0)
        or np.any(high <= 0.0)
        or np.any(high < low)
        or np.any(open_ < low)
        or np.any(open_ > high)
        or np.any(close < low)
        or np.any(close > high)
        or np.any(volume < 0.0)
    ):
        return None

    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    atr_14 = float(np.mean(true_range[-14:]))
    prior_tr_20 = float(np.mean(true_range[-21:-1]))
    prior_volume_20 = float(np.mean(volume[-21:-1]))
    prior_volume_3 = float(np.mean(volume[-4:-1]))
    if min(atr_14, prior_tr_20, prior_volume_20, prior_volume_3) <= 0.0:
        return None

    typical = (high + low + close) / 3.0
    rolling_volume = float(np.sum(volume[-24:]))
    previous_rolling_volume = float(np.sum(volume[-25:-1]))
    if rolling_volume <= 0.0 or previous_rolling_volume <= 0.0:
        return None
    vwap = float(np.sum(typical[-24:] * volume[-24:]) / rolling_volume)
    previous_vwap = float(
        np.sum(typical[-25:-1] * volume[-25:-1]) / previous_rolling_volume
    )
    latest = float(close[-1])
    prior_high = float(np.max(high[-21:-1]))
    last_range = float(high[-1] - low[-1])
    return_1 = _safe_ratio(latest, float(close[-2])) - 1.0
    return_3 = _safe_ratio(latest, float(close[-4])) - 1.0
    feature_values: dict[str, Any] = {
        "last_closed_bar": pd.Timestamp(values.index[-1]).tz_convert(IST).isoformat(),
        "last_close": latest,
        "last_high": float(high[-1]),
        "last_low": float(low[-1]),
        "previous_close": float(close[-2]),
        "return_1_bar": return_1,
        "return_3_bar": return_3,
        "acceleration_1v3": return_1 - return_3 / 3.0,
        "prior_high_20": prior_high,
        "atr_14": atr_14,
        "prior_high_distance_atr": (latest - prior_high) / atr_14,
        "last_true_range": float(true_range[-1]),
        "true_range_expansion_1v20": _safe_ratio(float(true_range[-1]), prior_tr_20),
        "last_true_range_atr": _safe_ratio(float(true_range[-1]), atr_14),
        "volume_ratio_1v20": _safe_ratio(float(volume[-1]), prior_volume_20),
        "volume_acceleration_1v3": _safe_ratio(float(volume[-1]), prior_volume_3),
        "prior_volume_20": prior_volume_20,
        "close_location": (
            (latest - float(low[-1])) / last_range if last_range > 0.0 else 0.5
        ),
        "vwap_24": vwap,
        "previous_vwap_24": previous_vwap,
        "vwap_extension_atr": (latest - vwap) / atr_14,
        "recent_low_4": float(np.min(low[-4:])),
        "average_notional_20": float(np.mean(close[-20:] * volume[-20:])),
        "bars": len(values),
    }
    required = (
        "last_close",
        "return_1_bar",
        "return_3_bar",
        "acceleration_1v3",
        "prior_high_20",
        "atr_14",
        "prior_high_distance_atr",
        "last_true_range",
        "true_range_expansion_1v20",
        "last_true_range_atr",
        "volume_ratio_1v20",
        "volume_acceleration_1v3",
        "prior_volume_20",
        "close_location",
        "vwap_24",
        "previous_vwap_24",
        "vwap_extension_atr",
        "recent_low_4",
        "average_notional_20",
    )
    if not all(math.isfinite(float(feature_values[name])) for name in required):
        return None
    return feature_values


def _recent_structure_failure(
    events: Sequence[Mapping[str, Any]], *, code: str, decision_bar: str
) -> dict[str, Any] | None:
    current = _as_ist(decision_bar)
    failures = [
        dict(event)
        for event in _latest_plans(events).values()
        if str(event.get("scrip_code")) == code
        and event.get("state") == "invalidated"
        and event.get("resolution_reason") == "structure_failed_before_trigger"
        and event.get("resolved_at")
    ]
    failures.sort(key=lambda item: str(item["resolved_at"]), reverse=True)
    if not failures:
        return None
    resolved = _as_ist(str(failures[0]["resolved_at"]))
    elapsed_bars = (current - resolved).total_seconds() / (15.0 * 60.0)
    return failures[0] if 0.0 < elapsed_bars <= RECLAIM_LOOKBACK_BARS else None


def _economics_contract() -> dict[str, Any]:
    """Return the exact cost/sizing assumptions used for an M21 plan."""

    bundle = _market_bundle("crypto")
    schedule = bundle.costs.schedule
    return {
        "version": "momentum-ignition-economics-v1",
        "maker_taker_pct": str(schedule.maker_taker_pct),
        "tds_pct": str(schedule.tds_pct),
        "gst_pct": str(schedule.gst_pct),
        "slippage_pct": str(schedule.slippage_pct),
        "rounding": str(schedule.rounding),
        "qty_step": float(bundle.qty_step),
        "min_notional_inr": float(bundle.min_notional_inr),
        "risk_budget_inr": RISK_BUDGET,
        "research_capital_inr": RESEARCH_CAPITAL,
        "target_r_grid": list(TARGET_R_GRID),
        "maximum_break_even_win_rate": MAX_BREAK_EVEN_WIN_RATE,
    }


def _economics_contract_failure(plan: Mapping[str, Any]) -> str | None:
    supplied_contract = plan.get("economics_contract")
    supplied_hash = plan.get("economics_contract_sha256")
    if not isinstance(supplied_contract, Mapping) or not isinstance(supplied_hash, str):
        return "economics_contract_integrity_failure"
    if supplied_hash != canonical_sha256(dict(supplied_contract)):
        return "economics_contract_integrity_failure"
    if supplied_hash != canonical_sha256(_economics_contract()):
        return "economics_contract_changed"
    return None


def _net_r(*, entry_raw: float, stop: float, exit_raw: float) -> float | None:
    bundle = _market_bundle("crypto")
    slippage = float(bundle.costs.slippage_pct)
    entry_execution = entry_raw * (1.0 + slippage)
    risk_distance = entry_execution - stop
    if risk_distance <= 0.0:
        return None
    raw_qty = min(RISK_BUDGET / risk_distance, RESEARCH_CAPITAL / entry_execution)
    step = float(bundle.qty_step)
    quantity = math.floor(raw_qty / step) * step
    if quantity <= 0.0 or quantity * entry_execution < float(bundle.min_notional_inr):
        return None
    exit_execution = exit_raw * (1.0 - slippage)
    return float(
        bundle.costs.net_r_multiple(
            trade_type=TradeType.INTRADAY,
            qty=quantity,
            entry=price_decimal(entry_execution),
            stop=price_decimal(stop),
            exit_price=price_decimal(exit_execution),
        )
    )


def _target_economics(
    *, entry_raw: float, stop: float, target: float
) -> dict[str, float] | None:
    target_net_r = _net_r(entry_raw=entry_raw, stop=stop, exit_raw=target)
    stop_net_r = _net_r(entry_raw=entry_raw, stop=stop, exit_raw=stop)
    if target_net_r is None or stop_net_r is None:
        return None
    denominator = target_net_r - stop_net_r
    if target_net_r <= 0.0 or stop_net_r >= 0.0 or denominator <= 0.0:
        return None
    break_even = -stop_net_r / denominator
    return {
        "target_net_r": target_net_r,
        "stop_net_r": stop_net_r,
        "break_even_win_rate": break_even,
    }


def _select_target(*, trigger: float, stop: float) -> dict[str, float] | None:
    risk = trigger - stop
    if not all(math.isfinite(value) for value in (trigger, stop, risk)) or risk <= 0.0:
        return None
    for multiple in TARGET_R_GRID:
        target = trigger + multiple * risk
        economics = _target_economics(entry_raw=trigger, stop=stop, target=target)
        if economics is None:
            continue
        if economics["break_even_win_rate"] <= MAX_BREAK_EVEN_WIN_RATE:
            return {"target": target, "target_r_multiple": multiple, **economics}
    return None


def _rule_blockers(
    features: Mapping[str, Any], *, mechanism: str, recent_failure: bool
) -> list[str]:
    blockers: list[str] = []
    if float(features["close_location"]) < 0.65:
        blockers.append("close_location_below_0_65")
    if float(features["last_true_range_atr"]) > 2.50:
        blockers.append("blowoff_true_range_above_2_5_atr")
    if mechanism == "breakout_ignition":
        if float(features["acceleration_1v3"]) <= 0.0:
            blockers.append("non_positive_acceleration")
        if float(features["return_3_percentile"]) < 0.80:
            blockers.append("return_3_percentile_below_0_80")
        if float(features["volume_ratio_1v20"]) < 1.50:
            blockers.append("volume_ratio_below_1_50")
        if float(features["volume_ratio_percentile"]) < 0.75:
            blockers.append("volume_percentile_below_0_75")
        if float(features["true_range_expansion_1v20"]) < 1.10:
            blockers.append("true_range_expansion_below_1_10")
        if float(features["last_close"]) < float(features["vwap_24"]):
            blockers.append("below_vwap")
        if float(features["vwap_extension_atr"]) > 2.0:
            blockers.append("vwap_extension_above_2_atr")
        distance = float(features["prior_high_distance_atr"])
        if distance < -0.50 or distance > 0.75:
            blockers.append("outside_prior_high_ignition_band")
    elif mechanism == "shakeout_reclaim":
        if not recent_failure:
            blockers.append("no_recent_structure_failure")
        if not (
            float(features["previous_close"]) <= float(features["previous_vwap_24"])
            and float(features["last_close"]) > float(features["vwap_24"])
        ):
            blockers.append("no_below_to_above_vwap_reclaim")
        if float(features["return_1_bar"]) <= 0.0:
            blockers.append("non_positive_reclaim_return")
        if float(features["volume_acceleration_1v3"]) < 1.25:
            blockers.append("volume_acceleration_below_1_25")
        if float(features["volume_ratio_percentile"]) < 0.60:
            blockers.append("volume_percentile_below_0_60")
    else:
        raise ValueError(f"unknown M21 mechanism: {mechanism}")
    return blockers


def _candidate_geometry(
    features: Mapping[str, Any], *, mechanism: str
) -> dict[str, float] | None:
    close = float(features["last_close"])
    atr = float(features["atr_14"])
    if mechanism == "breakout_ignition":
        trigger = max(close, float(features["prior_high_20"])) * 1.001
    else:
        trigger = max(close, float(features["vwap_24"])) * 1.001
    stop = min(
        float(features["recent_low_4"]) * 0.999,
        float(features["vwap_24"]) * 0.995,
        close - atr,
    )
    if not math.isfinite(trigger) or not math.isfinite(stop) or stop <= 0.0 or stop >= trigger:
        return None
    target = _select_target(trigger=trigger, stop=stop)
    if target is None:
        return None
    return {"trigger": trigger, "stop": stop, **target}


def _empty_report(observed_at: datetime, *, shortlist_rows: int) -> dict[str, Any]:
    report: dict[str, Any] = {
        "version": VERSION,
        "market": "crypto",
        "observed_at": observed_at.astimezone(IST).isoformat(),
        "status": "unavailable",
        "timeframe": "M15_completed_bars_only",
        "shortlist_rows": shortlist_rows,
        "intraday_valid_rows": 0,
        "intraday_invalid_rows": shortlist_rows,
        "universe_coverage": 0.0,
        "minimum_universe_coverage": MIN_COVERAGE,
        "registration_candidates": 0,
        "watch_candidates": 0,
        "tradeable_calls": 0,
        "candidates": [],
        "blockers": ["no_instrument_with_32_completed_m15_bars"],
        "authority": "research_watch_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "detail": "M21 has no valid completed-M15 universe snapshot; unavailable is not a pass.",
    }
    report["snapshot_sha256"] = canonical_sha256(report)
    return report


def build_momentum_ignition_watch(
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    plan_history: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a full-universe M21 snapshot and a separate top-three registration set."""

    now = observed_at.astimezone(IST)
    rows: list[dict[str, Any]] = []
    for row in shortlist.itertuples(index=False):
        code = str(row.scrip_code)
        features = _features(_frame(frames.get(code, ()), now))
        if features is None:
            continue
        raw_probability = getattr(row, "opportunity_probability", None)
        probability = (
            float(raw_probability)
            if raw_probability is not None and math.isfinite(float(raw_probability))
            else None
        )
        rows.append(
            {
                "market": "crypto",
                "scrip_code": code,
                "symbol": str(row.symbol),
                "opportunity_probability": probability,
                "daily_rank_available": probability is not None,
                "features": features,
            }
        )
    if not rows:
        return _empty_report(now, shortlist_rows=len(shortlist)), []

    table = pd.DataFrame(rows)
    expanded = pd.json_normalize(list(table["features"])).set_index(table.index)
    expanded["return_3_percentile"] = expanded["return_3_bar"].rank(pct=True)
    expanded["volume_ratio_percentile"] = expanded["volume_ratio_1v20"].rank(pct=True)
    expanded["acceleration_percentile"] = expanded["acceleration_1v3"].rank(pct=True)
    expanded["true_range_expansion_percentile"] = expanded[
        "true_range_expansion_1v20"
    ].rank(pct=True)
    table["momentum_score"] = (
        0.30 * expanded["return_3_percentile"].clip(0.0, 1.0)
        + 0.25 * expanded["volume_ratio_percentile"].clip(0.0, 1.0)
        + 0.20 * expanded["acceleration_percentile"].clip(0.0, 1.0)
        + 0.15 * expanded["true_range_expansion_percentile"].clip(0.0, 1.0)
        + 0.10 * expanded["close_location"].clip(0.0, 1.0)
    )
    coverage = len(rows) / len(shortlist) if len(shortlist) else 0.0
    candidates: list[dict[str, Any]] = []
    for row_position, (_, source) in enumerate(table.iterrows()):
        features = dict(source["features"])
        for name in (
            "return_3_percentile",
            "volume_ratio_percentile",
            "acceleration_percentile",
            "true_range_expansion_percentile",
        ):
            features[name] = float(expanded.iloc[row_position][name])
        code = str(source["scrip_code"])
        failure = _recent_structure_failure(
            plan_history, code=code, decision_bar=str(features["last_closed_bar"])
        )
        reclaim_blockers = _rule_blockers(
            features, mechanism="shakeout_reclaim", recent_failure=failure is not None
        )
        ignition_blockers = _rule_blockers(
            features, mechanism="breakout_ignition", recent_failure=failure is not None
        )
        if not reclaim_blockers:
            mechanism = "shakeout_reclaim"
            blockers: list[str] = []
        else:
            mechanism = "breakout_ignition"
            blockers = ignition_blockers
        if code in NON_DIRECTIONAL_CRYPTO_CODES:
            blockers = [*blockers, "non_directional_stablecoin"]
        geometry = _candidate_geometry(features, mechanism=mechanism) if not blockers else None
        if not blockers and geometry is None:
            blockers.append("cost_or_geometry_gate_failed")
        if coverage < MIN_COVERAGE:
            blockers.append("insufficient_universe_coverage")
        raw_probability = source["opportunity_probability"]
        probability = (
            float(raw_probability)
            if raw_probability is not None
            and math.isfinite(float(raw_probability))
            else None
        )
        candidate: dict[str, Any] = {
            "market": "crypto",
            "scrip_code": code,
            "symbol": str(source["symbol"]),
            "opportunity_probability": probability,
            "daily_rank_available": probability is not None,
            "mechanism": mechanism,
            "state": "arming" if not blockers else "observe",
            "momentum_score": float(source["momentum_score"]),
            "blockers": blockers,
            "features": features,
            "prior_failure_plan_id": failure.get("plan_id") if failure else None,
            "trigger": geometry.get("trigger") if geometry else None,
            "stop": geometry.get("stop") if geometry else None,
            "target": geometry.get("target") if geometry else None,
            "target_r_multiple": geometry.get("target_r_multiple") if geometry else None,
            "target_net_r": geometry.get("target_net_r") if geometry else None,
            "stop_net_r": geometry.get("stop_net_r") if geometry else None,
            "break_even_win_rate": (
                geometry.get("break_even_win_rate") if geometry else None
            ),
            "tradeable_now": False,
            "decision": (
                "RESEARCH WATCH; a later completed M15 close and volume confirmation are required"
                if not blockers
                else "NO RESEARCH PLAN"
            ),
        }
        candidates.append(candidate)

    def sort_key(candidate: Mapping[str, Any]) -> tuple[float, float, str]:
        probability = candidate.get("opportunity_probability")
        probability_sort = (
            -float(probability)
            if probability is not None and math.isfinite(float(probability))
            else math.inf
        )
        return (-float(candidate["momentum_score"]), probability_sort, str(candidate["scrip_code"]))

    eligible = sorted(
        (candidate for candidate in candidates if candidate["state"] == "arming"),
        key=sort_key,
    )
    # Return the entire sorted eligible pool. The updater walks it until three plans have
    # actually registered, so an active/cooldown-blocked leader cannot waste a slot.
    registration = [dict(candidate) for candidate in eligible]
    display = sorted(candidates, key=sort_key)[:DISPLAY_SIZE]
    status = "watch_candidates_found" if registration else "no_momentum_ignition_candidate"
    if coverage < MIN_COVERAGE:
        status = "insufficient_universe_coverage"
    report: dict[str, Any] = {
        "version": VERSION,
        "market": "crypto",
        "observed_at": now.isoformat(),
        "status": status,
        "timeframe": "M15_completed_bars_only",
        "shortlist_rows": len(shortlist),
        "intraday_valid_rows": len(rows),
        "intraday_invalid_rows": len(shortlist) - len(rows),
        "universe_coverage": coverage,
        "minimum_universe_coverage": MIN_COVERAGE,
        "registration_candidates": min(REGISTRATION_SIZE, len(registration)),
        "registration_candidate_pool": len(registration),
        "maximum_new_registrations_per_snapshot": REGISTRATION_SIZE,
        "watch_candidates": len(eligible),
        "ignition_candidates": sum(
            candidate["mechanism"] == "breakout_ignition" for candidate in eligible
        ),
        "reclaim_candidates": sum(
            candidate["mechanism"] == "shakeout_reclaim" for candidate in eligible
        ),
        "tradeable_calls": 0,
        "candidates": display,
        "authority": "research_watch_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "detail": (
            "M21 is a forward-only crypto research challenger. Display is top 12; the "
            "separate eligible pool is walked in rank order until at most three plans "
            "register per completed M15 snapshot."
        ),
    }
    report["snapshot_sha256"] = canonical_sha256(report)
    return report, registration


def _confirmation_volume_ratio(frame: pd.DataFrame, timestamp: pd.Timestamp) -> float | None:
    locations = np.flatnonzero(frame.index == timestamp)
    if len(locations) != 1:
        return None
    location = int(locations[0])
    if location < 20:
        return None
    prior = frame.iloc[location - 20 : location]["volume"].to_numpy(dtype=float)
    denominator = float(np.mean(prior))
    if denominator <= 0.0:
        return None
    return float(frame.iloc[location]["volume"]) / denominator


def _evaluate_plan(
    plan: Mapping[str, Any], frame: pd.DataFrame, *, observed_at: datetime
) -> dict[str, Any] | None:
    state = str(plan["state"])
    if state in TERMINAL_STATES:
        return None
    contract_failure = _economics_contract_failure(plan)
    if contract_failure is not None:
        invalidated = dict(plan)
        invalidated.update(
            {
                "event": "plan_invalidated",
                "state": "invalidated",
                "resolution_reason": contract_failure,
                "resolved_at": observed_at.astimezone(IST).isoformat(),
                "observed_at": observed_at.astimezone(IST).isoformat(),
            }
        )
        invalidated.pop("event_sha256", None)
        invalidated.pop("previous_sha256", None)
        return invalidated
    after = datetime.fromisoformat(str(plan["last_evaluated_bar"]))
    completed = _frame(frame, observed_at)
    bars = completed[completed.index > pd.Timestamp(after)]
    if bars.empty:
        return None

    updated = dict(plan)
    bars_seen = int(updated.get("bars_seen", 0))
    trigger_bars_seen = int(updated.get("trigger_bars_seen", 0))
    for timestamp, bar in bars.iterrows():
        bar_timestamp = pd.Timestamp(str(timestamp))
        bars_seen += 1
        open_price = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        trigger = float(updated["trigger"])
        stop = float(updated["stop"])
        target = float(updated["target"])
        updated["last_evaluated_bar"] = bar_timestamp.isoformat()
        updated["bars_seen"] = bars_seen

        if state == "pending_trigger":
            volume_ratio = _confirmation_volume_ratio(completed, bar_timestamp)
            if (
                close >= trigger
                and volume_ratio is not None
                and volume_ratio >= TRIGGER_VOLUME_RATIO
            ):
                entry_raw = close
                economics = (
                    _target_economics(entry_raw=entry_raw, stop=stop, target=target)
                    if target > entry_raw
                    else None
                )
                if (
                    economics is None
                    or economics["break_even_win_rate"] > MAX_BREAK_EVEN_WIN_RATE
                ):
                    state = "invalidated"
                    updated["resolution_reason"] = "actual_entry_failed_cost_gate"
                    updated["resolved_at"] = bar_timestamp.isoformat()
                    break
                slippage = float(_market_bundle("crypto").costs.slippage_pct)
                updated.update(
                    {
                        "entry_raw": entry_raw,
                        "entry_execution": entry_raw * (1.0 + slippage),
                        "triggered_at": bar_timestamp.isoformat(),
                        "trigger_volume_ratio": volume_ratio,
                        "target_net_r_at_entry": economics["target_net_r"],
                        "stop_net_r_at_entry": economics["stop_net_r"],
                        "break_even_win_rate_at_entry": economics[
                            "break_even_win_rate"
                        ],
                    }
                )
                state = "triggered"
                # Entry occurs at this completed bar's close. Its earlier high/low cannot be
                # used as if they happened after entry; outcome evaluation starts next bar.
                continue
            if low <= stop:
                state = "invalidated"
                updated["resolution_reason"] = "structure_failed_before_trigger"
                updated["resolved_at"] = bar_timestamp.isoformat()
                break
            if bars_seen >= WATCH_EXPIRY_BARS:
                state = "expired"
                updated["resolution_reason"] = "trigger_not_reached_before_expiry"
                updated["resolved_at"] = bar_timestamp.isoformat()
                break

        elif state == "triggered":
            trigger_bars_seen += 1
            updated["trigger_bars_seen"] = trigger_bars_seen
            if open_price <= stop:
                state = "stop"
                updated["exit_raw"] = open_price
                updated["resolved_at"] = bar_timestamp.isoformat()
                break
            if open_price >= target:
                state = "target"
                updated["exit_raw"] = open_price
                updated["resolved_at"] = bar_timestamp.isoformat()
                break
            # With unknown intrabar order, a bar touching both barriers is a stop.
            if low <= stop:
                state = "stop"
                updated["exit_raw"] = stop
                updated["resolved_at"] = bar_timestamp.isoformat()
                break
            if high >= target:
                state = "target"
                updated["exit_raw"] = target
                updated["resolved_at"] = bar_timestamp.isoformat()
                break
            if trigger_bars_seen >= TRADE_TIMEOUT_BARS:
                state = "timeout"
                updated["exit_raw"] = close
                updated["resolved_at"] = bar_timestamp.isoformat()
                break

    updated["state"] = state
    if state in RESOLVED_STATES:
        net_r = _net_r(
            entry_raw=float(updated["entry_raw"]),
            stop=float(updated["stop"]),
            exit_raw=float(updated["exit_raw"]),
        )
        updated["net_r"] = net_r
        updated["strict_success"] = state == "target" and net_r is not None and net_r > 0.0
    updated["event"] = "plan_evaluated"
    updated["observed_at"] = observed_at.astimezone(IST).isoformat()
    updated.pop("event_sha256", None)
    updated.pop("previous_sha256", None)
    return updated


def _registration_blocker(
    candidate: Mapping[str, Any],
    *,
    latest: Mapping[str, Mapping[str, Any]],
    observed_at: datetime,
) -> str | None:
    code = str(candidate["scrip_code"])
    code_plans = [plan for plan in latest.values() if str(plan.get("scrip_code")) == code]
    decision_bar = str(candidate["features"]["last_closed_bar"])
    if (
        sum(str(plan.get("decision_bar")) == decision_bar for plan in latest.values())
        >= REGISTRATION_SIZE
    ):
        return "snapshot_plan_cap"
    if any(str(plan.get("state")) in ACTIVE_STATES for plan in code_plans):
        return "active_plan_exists"
    decision = _as_ist(decision_bar)
    terminal = [plan for plan in code_plans if str(plan.get("state")) in TERMINAL_STATES]
    if terminal:
        last_terminal = max(
            terminal,
            key=lambda plan: str(plan.get("resolved_at") or plan.get("observed_at") or ""),
        )
        terminal_at = _as_ist(
            str(last_terminal.get("resolved_at") or last_terminal.get("observed_at"))
        )
        if decision - terminal_at < timedelta(minutes=15 * COOLDOWN_BARS):
            return "four_bar_cooldown"
    today = decision.date()
    today_plans = [
        plan
        for plan in code_plans
        if plan.get("decision_bar")
        and _as_ist(str(plan["decision_bar"])).date() == today
    ]
    if len(today_plans) >= MAX_PLANS_PER_CODE_PER_IST_DATE:
        return "daily_plan_cap"
    if candidate.get("mechanism") == "shakeout_reclaim":
        cutoff = observed_at.astimezone(IST) - timedelta(hours=RECLAIM_COOLDOWN_HOURS)
        if any(
            plan.get("mechanism") == "shakeout_reclaim"
            and plan.get("armed_at")
            and _as_ist(str(plan["armed_at"])) >= cutoff
            for plan in code_plans
        ):
            return "reclaim_24h_cap"
    return None


def update_momentum_ignition_plans(
    report: Mapping[str, Any],
    registration_candidates: Sequence[Mapping[str, Any]],
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    allow_new_plans: bool = True,
) -> dict[str, Any]:
    """Resolve immutable M21 plans, then register bounded new research plans."""

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

    evidence = record_pre_takeoff_evidence(
        "crypto",
        latest,
        gate_path=_qualification_ledger(output_root),
        observed_at=observed_at,
    )
    coverage_open = float(report.get("universe_coverage") or 0.0) >= MIN_COVERAGE
    registration_open = allow_new_plans and coverage_open and not bool(evidence["terminal"])
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


def persist_momentum_ignition_watch(
    report: Mapping[str, Any], *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    """Atomically persist one M21 snapshot and append its independent hash chain."""

    value = dict(report)
    value.pop("snapshot_sha256", None)
    value["snapshot_sha256"] = canonical_sha256(value)
    root = output_root / "crypto"
    ledger = _snapshot_ledger(output_root)
    events = _load_hash_chain(ledger, label="momentum-ignition snapshot")
    _verify_snapshot_state(output_root)
    _atomic_json(root / "latest.json", value)
    if not events or events[-1].get("snapshot_sha256") != value["snapshot_sha256"]:
        _append_hash_event(ledger, value, label="momentum-ignition snapshot")
    return value


def collect_momentum_ignition_watch(
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one M21 collection from already-fetched all-universe crypto M15 data."""

    _verify_snapshot_state(output_root)
    history = load_momentum_ignition_plan_events(output_root=output_root)
    report, registration = build_momentum_ignition_watch(
        shortlist, frames, observed_at=observed_at, plan_history=history
    )
    if source is not None:
        report["source"] = dict(source)
    latest_bar = _latest_completed_bar(frames, observed_at)
    report["latest_completed_bar"] = latest_bar.isoformat() if latest_bar else None
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    report["watch_plans"] = update_momentum_ignition_plans(
        report,
        registration,
        frames,
        observed_at=observed_at,
        output_root=output_root,
    )
    report["new_plan_registration_open"] = report["watch_plans"][
        "new_plan_registration_open"
    ]
    return persist_momentum_ignition_watch(report, output_root=output_root)


def _not_applicable(market: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "market": market,
        "status": "not_applicable",
        "timeframe": "M15_completed_bars_only",
        "watch_candidates": 0,
        "registration_candidates": 0,
        "tradeable_calls": 0,
        "candidates": [],
        "watch_plans": {
            "total_plans": 0,
            "active_plans": 0,
            "resolved_trades": 0,
            "strict_accuracy": None,
            "mean_net_r": None,
            "evidence": {
                "market": market,
                "status": "not_applicable",
                "eligible_for_live": False,
                "baseline_accuracy_improved": False,
            },
        },
        "collector_health": {
            "status": "not_applicable",
            "healthy": False,
            "detail": "M21 is a crypto-only research challenger.",
        },
        "authority": "research_watch_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "detail": "M21 is intentionally not applicable outside crypto.",
    }


def load_momentum_ignition_watch(
    market: str,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Load verified M21 state; every integrity failure raises instead of passing."""

    if market != "crypto":
        return _not_applicable(market)
    # Verify the full plan and snapshot histories even if latest.json itself is intact.
    plan_events = _load_plan_events(_plan_ledger(output_root))
    value = _verify_snapshot_state(output_root)
    if value is None:
        report: dict[str, Any] = {
            "version": VERSION,
            "market": "crypto",
            "status": "not_collected",
            "timeframe": "M15_completed_bars_only",
            "watch_candidates": 0,
            "registration_candidates": 0,
            "tradeable_calls": 0,
            "candidates": [],
            "authority": "research_watch_only",
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
            "detail": "No forward M21 snapshot has been collected; unavailable is not a pass.",
        }
        report["watch_plans"] = {
            "total_plans": len(_latest_plans(plan_events)),
            "evidence": load_pre_takeoff_evidence(
                "crypto", output_root=output_root, observed_at=observed_at
            ),
        }
        report["collector_health"] = pre_takeoff_collector_health(
            "crypto",
            last_observed_at=None,
            latest_completed_bar=None,
            observed_at=observed_at,
        )
        return report
    report = dict(value)
    plans = dict(report.get("watch_plans") or {})
    plans["evidence"] = load_pre_takeoff_evidence(
        "crypto", output_root=output_root, observed_at=observed_at
    )
    report["watch_plans"] = plans
    report["collector_health"] = pre_takeoff_collector_health(
        "crypto",
        last_observed_at=str(report.get("observed_at") or "") or None,
        latest_completed_bar=(
            str(report["latest_completed_bar"])
            if report.get("latest_completed_bar")
            else None
        ),
        observed_at=observed_at,
    )
    return report


__all__ = [
    "build_momentum_ignition_watch",
    "collect_momentum_ignition_watch",
    "load_momentum_ignition_plan_events",
    "load_momentum_ignition_watch",
    "persist_momentum_ignition_watch",
    "update_momentum_ignition_plans",
]
