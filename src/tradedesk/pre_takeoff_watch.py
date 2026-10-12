"""Forward-only pre-takeoff watch discovery.

The daily opportunity ranker supplies the causal universe and completed M15 bars describe
whether each instrument is compressing below resistance before a move. The output is an
anticipatory research watch, never a trade or promotion shortcut.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.broker.indstocks.rest import IndstocksClient
from tradedesk.intraday_contract_race import (
    DB_PATHS,
    RESEARCH_CAPITAL,
    RISK_BUDGET,
    _atomic_json,
    _hash_file,
    _market_bundle,
    load_manifest,
)
from tradedesk.leader_discovery import FEATURE_COLUMNS, extract_decision_universe
from tradedesk.models import TradeType, price_decimal
from tradedesk.pre_takeoff_evidence import (
    load_pre_takeoff_evidence,
    pre_takeoff_collector_health,
    record_pre_takeoff_evidence,
)
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "pre-takeoff-watch-v1"
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/pre_takeoff_watch")
SHORTLIST_SIZE = {"nse": 75, "bse": 75, "crypto": 60}
DISPLAY_SIZE = 12
PLAN_VERSION = "pre-takeoff-watch-plan-v1"
WATCH_EXPIRY_BARS = 24
TRADE_TIMEOUT_BARS = 16
NON_DIRECTIONAL_CRYPTO_CODES = {
    "CDX_DAIINR",
    "CDX_FDUSDINR",
    "CDX_PYUSDINR",
    "CDX_TUSDINR",
    "CDX_USDCINR",
    "CDX_USDTINR",
}
MarketName = Literal["nse", "bse", "crypto"]


def _frame(candles: Sequence[Candle] | pd.DataFrame, observed_at: datetime) -> pd.DataFrame:
    if isinstance(candles, pd.DataFrame):
        source = candles.copy()
        if not isinstance(source.index, pd.DatetimeIndex):
            raise ValueError("pre-takeoff frame requires a datetime index")
    else:
        completed = [
            candle
            for candle in candles
            if candle.interval is Interval.M15 and candle.close_time <= observed_at
        ]
        source = pd.DataFrame(
            [
                {
                    "ts": candle.ts,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
                for candle in completed
            ]
        )
        if source.empty:
            return source
        source.set_index("ts", inplace=True)
    source = source[["open", "high", "low", "close", "volume"]].copy()
    source = source[~source.index.duplicated(keep="last")].sort_index()
    source = source[
        source.index.map(
            lambda value: pd.Timestamp(value).to_pydatetime() + timedelta(minutes=15)
            <= observed_at
        )
    ]
    return source.tail(96)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator and math.isfinite(denominator) else math.nan


def _intraday_features(frame: pd.DataFrame) -> dict[str, Any] | None:
    if len(frame) < 32:
        return None
    values = frame.tail(32)
    close = values["close"].to_numpy(dtype=float)
    high = values["high"].to_numpy(dtype=float)
    low = values["low"].to_numpy(dtype=float)
    volume = values["volume"].to_numpy(dtype=float)
    if not all(np.isfinite(item).all() for item in (close, high, low, volume)):
        return None
    if np.any(close <= 0) or np.any(high < low) or np.any(volume < 0):
        return None

    prior_high_20 = float(np.max(high[-21:-1]))
    latest = float(close[-1])
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    atr_14 = float(np.mean(true_range[-14:]))
    fast_range = float(np.mean(true_range[-4:]))
    slow_range = float(np.mean(true_range[-24:-4]))
    prior_volume = float(np.mean(volume[-21:-1]))
    recent_volume = float(np.mean(volume[-4:]))
    typical = (high + low + close) / 3.0
    rolling_volume = float(np.sum(volume[-24:]))
    vwap = (
        float(np.sum(typical[-24:] * volume[-24:]) / rolling_volume)
        if rolling_volume > 0
        else latest
    )
    low_window = low[-5:]
    low_slope = float(np.polyfit(np.arange(len(low_window)), low_window, 1)[0] / latest)
    last_range = float(high[-1] - low[-1])
    close_location = (latest - float(low[-1])) / last_range if last_range > 0 else 0.5
    features: dict[str, Any] = {
        "last_closed_bar": pd.Timestamp(values.index[-1]).tz_convert(IST).isoformat(),
        "last_close": latest,
        "return_1_bar": _safe_ratio(latest, float(close[-2])) - 1.0,
        "return_3_bar": _safe_ratio(latest, float(close[-4])) - 1.0,
        "return_6_bar": _safe_ratio(latest, float(close[-7])) - 1.0,
        "prior_high_20": prior_high_20,
        "distance_prior_high_20": _safe_ratio(latest, prior_high_20) - 1.0,
        "range_contraction_4v20": _safe_ratio(fast_range, slow_range),
        "volume_ratio_1v20": _safe_ratio(float(volume[-1]), prior_volume),
        "volume_ratio_4v20": _safe_ratio(recent_volume, prior_volume),
        "higher_low_slope": low_slope,
        "close_location": close_location,
        "vwap_24": vwap,
        "distance_vwap_24": _safe_ratio(latest, vwap) - 1.0,
        "atr_14": atr_14,
        "atr_14_pct": _safe_ratio(atr_14, latest),
        "average_notional_20": float(np.mean(close[-20:] * volume[-20:])),
        "bars": len(values),
    }
    required_finite = (
        "last_close",
        "return_1_bar",
        "return_3_bar",
        "return_6_bar",
        "prior_high_20",
        "distance_prior_high_20",
        "range_contraction_4v20",
        "volume_ratio_1v20",
        "volume_ratio_4v20",
        "higher_low_slope",
        "close_location",
        "vwap_24",
        "distance_vwap_24",
        "atr_14",
        "atr_14_pct",
        "average_notional_20",
    )
    if not all(math.isfinite(float(features[name])) for name in required_finite):
        return None
    return features


def _load_opportunity_shortlist(
    market: str,
    *,
    db_path: Path,
    observed_at: datetime,
    limit: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = load_manifest(market)
    ranker = manifest.get("ranker") or {}
    model_path = Path(str(ranker.get("path", "")))
    if not model_path.exists() or _hash_file(model_path) != ranker.get("sha256"):
        raise ValueError("pre-takeoff upstream ranker binding mismatch")
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict):
        raise ValueError("pre-takeoff upstream ranker bundle is invalid")
    if bundle.get("market") != market or tuple(bundle.get("features") or ()) != tuple(
        FEATURE_COLUMNS
    ):
        raise ValueError("pre-takeoff upstream ranker contract mismatch")
    universe, source = extract_decision_universe(db_path, market, now=observed_at)
    model = bundle.get("model")
    if model is None or not hasattr(model, "predict_proba"):
        raise ValueError("pre-takeoff upstream ranker model is missing")
    universe["opportunity_probability"] = model.predict_proba(
        universe[list(FEATURE_COLUMNS)]
    )[:, 1]
    universe.sort_values(
        ["opportunity_probability", "scrip_code"], ascending=[False, True], inplace=True
    )
    shortlist = universe.head(limit).copy()
    shortlist["opportunity_rank"] = np.arange(1, len(shortlist) + 1)
    return shortlist, {
        **source,
        "full_universe_rows": len(universe),
        "shortlist_rows": len(shortlist),
        "ranker_sha256": ranker["sha256"],
    }


def _candidate_state(features: Mapping[str, Any], market: str) -> tuple[str, list[str]]:
    extended_limit = 0.05 if market == "crypto" else 0.03
    blockers: list[str] = []
    distance = float(features["distance_prior_high_20"])
    if distance >= 0.0:
        blockers.append("already_above_prior_high")
    if distance < -0.05:
        blockers.append("more_than_5pct_below_prior_high")
    if float(features["return_3_bar"]) > extended_limit:
        blockers.append("already_extended_last_3_bars")
    if float(features["range_contraction_4v20"]) > 1.10:
        blockers.append("no_range_contraction")
    if float(features["distance_vwap_24"]) < -0.005:
        blockers.append("below_rolling_vwap")
    if float(features["higher_low_slope"]) <= 0.0:
        blockers.append("no_higher_low_pressure")
    # CoinDCX base volume is rounded by the shared Candle contract. Requiring a small but
    # non-zero ratio deliberately drops pairs whose current participation cannot be
    # measured reliably; unavailable liquidity is not a tradeable pass.
    if float(features["volume_ratio_1v20"]) < 0.01:
        blockers.append("current_volume_unavailable_or_zero")
    if float(features["volume_ratio_4v20"]) < 0.10:
        blockers.append("recent_volume_too_low")

    structural = {
        "already_above_prior_high",
        "more_than_5pct_below_prior_high",
        "already_extended_last_3_bars",
        "no_range_contraction",
        "below_rolling_vwap",
        "no_higher_low_pressure",
        "current_volume_unavailable_or_zero",
        "recent_volume_too_low",
    }
    if not (set(blockers) & structural):
        if (
            distance >= -0.02
            and float(features["volume_ratio_1v20"]) >= 1.10
            and float(features["close_location"]) >= 0.60
        ):
            return "arming", blockers
        return "watch", blockers
    if "already_above_prior_high" in blockers or "already_extended_last_3_bars" in blockers:
        return "already_moving", blockers
    return "observe", blockers


def build_pre_takeoff_watch(
    market: str,
    shortlist: pd.DataFrame,
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    """Rank completed-bar pre-break snapshots without inventing a trade."""

    rows: list[dict[str, Any]] = []
    invalid = 0
    for row in shortlist.itertuples(index=False):
        code = str(row.scrip_code)
        source = frames.get(code, ())
        frame = _frame(source, observed_at)
        features = _intraday_features(frame)
        if features is None:
            invalid += 1
            continue
        state, blockers = _candidate_state(features, market)
        if market == "crypto" and code in NON_DIRECTIONAL_CRYPTO_CODES:
            state = "observe"
            blockers = [*blockers, "non_directional_stablecoin"]
        raw_probability = getattr(row, "opportunity_probability", None)
        probability = (
            float(raw_probability)
            if raw_probability is not None and math.isfinite(float(raw_probability))
            else None
        )
        rows.append(
            {
                "market": market,
                "scrip_code": code,
                "symbol": str(row.symbol),
                "opportunity_rank": int(row.opportunity_rank),  # type: ignore[arg-type]
                "opportunity_probability": probability,
                "daily_rank_available": probability is not None,
                "state": state,
                "blockers": blockers,
                "features": features,
            }
        )
    if not rows:
        return {
            "version": VERSION,
            "market": market,
            "observed_at": observed_at.isoformat(),
            "status": "unavailable",
            "candidates": [],
            "blockers": ["no_shortlisted_instrument_with_32_completed_m15_bars"],
            "authority": "research_watch_only",
            "tradeable_calls": 0,
            "baseline_accuracy_improved": False,
        }

    table = pd.DataFrame(rows)
    expanded = pd.json_normalize(list(table["features"]))
    opportunity_probability = pd.to_numeric(
        table["opportunity_probability"], errors="coerce"
    )
    opportunity_rank = opportunity_probability.rank(pct=True).fillna(0.0)
    proximity = (
        1.0 - expanded["distance_prior_high_20"].abs().clip(upper=0.05) / 0.05
    ).clip(0.0, 1.0)
    contraction = ((1.2 - expanded["range_contraction_4v20"]) / 0.7).clip(0.0, 1.0)
    volume = (expanded["volume_ratio_1v20"] / 2.0).clip(0.0, 1.0)
    pressure = expanded["higher_low_slope"].rank(pct=True)
    vwap_support = (
        (expanded["distance_vwap_24"] >= 0.0)
        & (expanded["distance_vwap_24"] <= 0.03)
    ).astype(float)
    table["pre_takeoff_score"] = (
        0.30 * opportunity_rank
        + 0.20 * proximity
        + 0.15 * contraction
        + 0.15 * volume
        + 0.10 * pressure
        + 0.10 * vwap_support
    )
    state_order = {"arming": 0, "watch": 1, "observe": 2, "already_moving": 3}
    table["state_order"] = table["state"].map(state_order)
    table.sort_values(
        ["state_order", "pre_takeoff_score", "scrip_code"],
        ascending=[True, False, True],
        inplace=True,
    )
    candidates: list[dict[str, Any]] = []
    for candidate_value in table.head(DISPLAY_SIZE).to_dict(orient="records"):
        value: dict[str, Any] = {
            str(key): item for key, item in candidate_value.items()
        }
        value.pop("state_order", None)
        raw_probability = value.get("opportunity_probability")
        value["opportunity_probability"] = (
            float(raw_probability)
            if raw_probability is not None and math.isfinite(float(raw_probability))
            else None
        )
        value["daily_rank_available"] = value["opportunity_probability"] is not None
        features = dict(value["features"])
        trigger = float(features["prior_high_20"]) * 1.001
        stop = min(
            float(features["last_close"]) - float(features["atr_14"]),
            float(features["vwap_24"]) * 0.995,
        )
        risk = trigger - stop
        value.update(
            {
                "pre_takeoff_score": float(value["pre_takeoff_score"]),
                "trigger": trigger,
                "stop": stop,
                "quick_target_075r": trigger + 0.75 * risk if risk > 0 else None,
                "tradeable_now": False,
                "decision": (
                    "WATCH BEFORE BREAKOUT; trade only after a later completed-bar trigger"
                    if value["state"] in {"arming", "watch"}
                    else "NO TRADE"
                ),
            }
        )
        candidates.append(value)
    watch_count = sum(row["state"] in {"arming", "watch"} for row in candidates)
    report: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "observed_at": observed_at.isoformat(),
        "status": "watch_candidates_found" if watch_count else "no_pre_takeoff_candidate",
        "shortlist_rows": len(shortlist),
        "intraday_valid_rows": len(rows),
        "intraday_invalid_rows": invalid,
        "watch_candidates": watch_count,
        "tradeable_calls": 0,
        "candidates": candidates,
        "authority": "research_watch_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "detail": (
            "These are causal pre-break watches, not calls. A later completed M15 bar must "
            "cross the recorded trigger before execution can be evaluated."
        ),
    }
    report["snapshot_sha256"] = canonical_sha256(report)
    return report


def _persist_watch(report: Mapping[str, Any], output_root: Path) -> None:
    market = str(report["market"])
    root = output_root / market
    _atomic_json(root / "latest.json", report)
    ledger = root / "snapshots.jsonl"
    previous = "GENESIS"
    if ledger.exists():
        lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line]
        if lines:
            previous = str(json.loads(lines[-1])["event_sha256"])
    event = {**dict(report), "previous_sha256": previous}
    event["event_sha256"] = canonical_sha256(event)
    if ledger.exists():
        last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        if last.get("snapshot_sha256") == report.get("snapshot_sha256"):
            return
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")


def _plan_ledger(output_root: Path, market: str) -> Path:
    return output_root / market / "plans.jsonl"


def _qualification_ledger(output_root: Path, market: str) -> Path:
    return output_root / market / "qualification.jsonl"


def _load_plan_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous = "GENESIS"
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        value = json.loads(line)
        supplied = value.get("event_sha256")
        body = dict(value)
        body.pop("event_sha256", None)
        if value.get("previous_sha256") != previous or supplied != canonical_sha256(body):
            raise ValueError(f"pre-takeoff plan chain failure at line {line_number}")
        events.append(value)
        previous = str(supplied)
    return events


def _append_plan_event(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    events = _load_plan_events(path)
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


def _net_r(plan: Mapping[str, Any], exit_raw: float, market: str) -> float | None:
    bundle = _market_bundle(market)
    slippage = float(bundle.costs.slippage_pct)
    entry = float(plan["entry_execution"])
    stop = float(plan["stop"])
    risk_distance = entry - stop
    if risk_distance <= 0:
        return None
    raw_qty = min(RISK_BUDGET / risk_distance, RESEARCH_CAPITAL / entry)
    if market == "crypto":
        step = float(bundle.qty_step)
        quantity = math.floor(raw_qty / step) * step
    else:
        quantity = float(math.floor(raw_qty))
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


def _evaluate_plan(
    plan: Mapping[str, Any],
    frame: pd.DataFrame,
    *,
    observed_at: datetime,
) -> dict[str, Any] | None:
    state = str(plan["state"])
    if state in {"target", "stop", "timeout", "expired", "invalidated"}:
        return None
    after = datetime.fromisoformat(str(plan["last_evaluated_bar"]))
    bars = _frame(frame, observed_at)
    bars = bars[bars.index > pd.Timestamp(after)]
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
        target = float(updated["quick_target_075r"])
        updated["last_evaluated_bar"] = bar_timestamp.isoformat()
        updated["bars_seen"] = bars_seen

        if state == "pending_trigger":
            if high >= trigger:
                if low <= stop:
                    state = "invalidated"
                    updated["resolution_reason"] = "ambiguous_trigger_and_stop_same_bar"
                    updated["resolved_at"] = bar_timestamp.isoformat()
                    break
                raw_entry = max(trigger, open_price)
                if raw_entry >= target:
                    state = "invalidated"
                    updated["resolution_reason"] = "gap_above_quick_target"
                    updated["resolved_at"] = bar_timestamp.isoformat()
                    break
                slippage = float(_market_bundle(str(updated["market"])).costs.slippage_pct)
                updated["entry_raw"] = raw_entry
                updated["entry_execution"] = raw_entry * (1.0 + slippage)
                updated["triggered_at"] = bar_timestamp.isoformat()
                state = "triggered"
                if high >= target:
                    state = "target"
                    updated["exit_raw"] = target
                    updated["resolved_at"] = bar_timestamp.isoformat()
                    break
            elif low <= stop:
                state = "invalidated"
                updated["resolution_reason"] = "structure_failed_before_trigger"
                updated["resolved_at"] = bar_timestamp.isoformat()
                break
            elif bars_seen >= WATCH_EXPIRY_BARS:
                state = "expired"
                updated["resolution_reason"] = "trigger_not_reached_before_expiry"
                updated["resolved_at"] = bar_timestamp.isoformat()
                break

        if state == "triggered":
            trigger_bars_seen += 1
            updated["trigger_bars_seen"] = trigger_bars_seen
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
    if state in {"target", "stop", "timeout"}:
        net_r = _net_r(updated, float(updated["exit_raw"]), str(updated["market"]))
        updated["net_r"] = net_r
        updated["strict_success"] = state == "target" and net_r is not None and net_r > 0
    updated["event"] = "plan_evaluated"
    updated["observed_at"] = observed_at.isoformat()
    updated.pop("event_sha256", None)
    updated.pop("previous_sha256", None)
    return updated


def _update_watch_plans(
    report: Mapping[str, Any],
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame],
    *,
    observed_at: datetime,
    output_root: Path,
    allow_new_plans: bool = True,
) -> dict[str, Any]:
    market = str(report["market"])
    path = _plan_ledger(output_root, market)
    events = _load_plan_events(path)
    latest = _latest_plans(events)
    frame_views = {code: _frame(value, observed_at) for code, value in frames.items()}

    for plan_id, plan in list(latest.items()):
        code = str(plan["scrip_code"])
        if (
            market == "crypto"
            and code in NON_DIRECTIONAL_CRYPTO_CODES
            and plan["state"] == "pending_trigger"
        ):
            exclusion = dict(plan)
            exclusion.update(
                {
                    "event": "plan_invalidated",
                    "state": "invalidated",
                    "resolution_reason": "non_directional_asset_excluded_before_trigger",
                    "resolved_at": observed_at.isoformat(),
                    "observed_at": observed_at.isoformat(),
                }
            )
            exclusion.pop("event_sha256", None)
            exclusion.pop("previous_sha256", None)
            latest[plan_id] = _append_plan_event(path, exclusion)
            continue
        frame = frame_views.get(code)
        if frame is None or frame.empty:
            continue
        update = _evaluate_plan(plan, frame, observed_at=observed_at)
        if update is not None:
            latest[plan_id] = _append_plan_event(path, update)

    active_codes = {
        str(plan["scrip_code"])
        for plan in latest.values()
        if plan["state"] in {"pending_trigger", "triggered"}
    }
    evidence = record_pre_takeoff_evidence(
        market,
        latest,
        gate_path=_qualification_ledger(output_root, market),
        observed_at=observed_at,
    )
    registration_open = allow_new_plans and not bool(evidence["terminal"])
    new_plans_registered = 0
    registration_candidates = report.get("candidates", ()) if registration_open else ()
    for candidate in registration_candidates:
        if candidate.get("state") not in {"arming", "watch"}:
            continue
        code = str(candidate["scrip_code"])
        if code in active_codes:
            continue
        decision_bar = str(candidate["features"]["last_closed_bar"])
        plan_id = canonical_sha256(
            {
                "version": PLAN_VERSION,
                "market": market,
                "scrip_code": code,
                "decision_bar": decision_bar,
                "trigger": candidate["trigger"],
            }
        )
        if plan_id in latest:
            continue
        plan = {
            "version": PLAN_VERSION,
            "event": "plan_armed",
            "plan_id": plan_id,
            "market": market,
            "scrip_code": code,
            "symbol": candidate["symbol"],
            "armed_at": observed_at.isoformat(),
            "decision_bar": decision_bar,
            "last_evaluated_bar": decision_bar,
            "state": "pending_trigger",
            "bars_seen": 0,
            "trigger_bars_seen": 0,
            "trigger": float(candidate["trigger"]),
            "stop": float(candidate["stop"]),
            "quick_target_075r": float(candidate["quick_target_075r"]),
            "pre_takeoff_score": float(candidate["pre_takeoff_score"]),
            "opportunity_probability": (
                float(candidate["opportunity_probability"])
                if candidate.get("opportunity_probability") is not None
                and math.isfinite(float(candidate["opportunity_probability"]))
                else None
            ),
            "source_snapshot_sha256": report.get("snapshot_sha256"),
            "observed_at": observed_at.isoformat(),
            "authority": "research_watch_only",
        }
        latest[plan_id] = _append_plan_event(path, plan)
        active_codes.add(code)
        new_plans_registered += 1

    states = Counter(str(plan["state"]) for plan in latest.values())
    resolved = [
        plan for plan in latest.values() if plan["state"] in {"target", "stop", "timeout"}
    ]
    successes = sum(bool(plan.get("strict_success")) for plan in resolved)
    net_values = [float(plan["net_r"]) for plan in resolved if plan.get("net_r") is not None]
    return {
        "total_plans": len(latest),
        "states": dict(sorted(states.items())),
        "active_plans": sum(
            state in {"pending_trigger", "triggered"} for state in states.elements()
        ),
        "resolved_trades": len(resolved),
        "strict_successes": successes,
        "strict_accuracy": successes / len(resolved) if resolved else None,
        "mean_net_r": float(np.mean(net_values)) if net_values else None,
        "new_plan_registration_open": registration_open,
        "new_plans_registered": new_plans_registered,
        "evidence": evidence,
        "plans": [
            {
                "plan_id": plan["plan_id"],
                "symbol": plan["symbol"],
                "state": plan["state"],
                "armed_at": plan["armed_at"],
                "trigger": plan["trigger"],
                "stop": plan["stop"],
                "target": plan["quick_target_075r"],
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


def _latest_completed_bar(
    frames: Mapping[str, Sequence[Candle] | pd.DataFrame], observed_at: datetime
) -> datetime | None:
    latest: datetime | None = None
    for source in frames.values():
        frame = _frame(source, observed_at)
        if frame.empty:
            continue
        closed_at = pd.Timestamp(frame.index[-1]).to_pydatetime() + timedelta(minutes=15)
        if latest is None or closed_at > latest:
            latest = closed_at
    return latest


def _equity_registration_open(
    observed_at: datetime, latest_completed_bar: datetime | None
) -> bool:
    local = observed_at.astimezone(IST)
    if local.weekday() >= 5 or not (time(9, 30) <= local.time() < time(15, 0)):
        return False
    if latest_completed_bar is None:
        return False
    return latest_completed_bar.astimezone(IST).date() == local.date()


async def collect_equity_pre_takeoff_watch(
    market: str,
    *,
    client: IndstocksClient,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Collect one full daily-liquid NSE/BSE universe using completed M15 bars."""

    if market not in {"nse", "bse"}:
        raise ValueError("equity pre-takeoff collection requires market nse or bse")
    now = observed_at or datetime.now(IST)
    ranked, source = _load_opportunity_shortlist(
        market,
        db_path=db_path or DB_PATHS[market],
        observed_at=now,
        limit=100_000,
    )
    codes = [str(value) for value in ranked["scrip_code"].tolist()]
    candles = await client.candles_history(
        Interval.M15,
        codes,
        start=now - timedelta(days=Interval.M15.max_window_days),
        end=now,
    )
    latest_bar = _latest_completed_bar(candles, now)
    registration_open = _equity_registration_open(now, latest_bar)
    report = build_pre_takeoff_watch(market, ranked, candles, observed_at=now)
    report.update(
        {
            "source": source,
            "universe_scope": "full_daily_liquid_eligible",
            "active_pairs": len(ranked),
            "daily_ranked_pairs": len(ranked),
            "requested_pairs": len(codes),
            "latest_completed_bar": latest_bar.isoformat() if latest_bar else None,
            "new_plan_registration_open": registration_open,
        }
    )
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    report["watch_plans"] = _update_watch_plans(
        report,
        candles,
        observed_at=now,
        output_root=output_root,
        allow_new_plans=registration_open,
    )
    report["new_plan_registration_open"] = report["watch_plans"][
        "new_plan_registration_open"
    ]
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    _persist_watch(report, output_root)
    return report


async def collect_crypto_pre_takeoff_watch(
    *,
    db_path: Path = DB_PATHS["crypto"],
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    momentum_output_root: Path | None = None,
    momentum_m22_output_root: Path | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Refresh M20 and isolated M21/M22 challengers from one completed-M15 fetch."""

    from tradedesk.broker.coindcx import CoinDcxClient
    from tradedesk.broker.coindcx.models import candle_from_api
    from tradedesk.markets.crypto_universe import active_crypto_codes

    now = observed_at or datetime.now(IST)
    async with CoinDcxClient() as client:
        instruments = await client.instruments()
        active = set(active_crypto_codes(instruments))
        pairs = {
            instrument.scrip_code: str(instrument.custom_symbol)
            for instrument in instruments
            if instrument.scrip_code in active and instrument.custom_symbol
        }
        client.register_pairs(pairs)
        ranked, source = _load_opportunity_shortlist(
            "crypto",
            db_path=db_path,
            observed_at=now,
            limit=max(SHORTLIST_SIZE["crypto"], len(active)),
        )
        ranked_by_code: dict[str, dict[str, Any]] = {
            str(value["scrip_code"]): {str(key): item for key, item in value.items()}
            for value in ranked.to_dict(orient="records")
        }
        symbol_by_code = {
            instrument.scrip_code: (
                instrument.trading_symbol or instrument.symbol_name or instrument.scrip_code
            )
            for instrument in instruments
            if instrument.scrip_code in active
        }
        universe_rows: list[dict[str, Any]] = []
        for fallback_rank, code in enumerate(sorted(active), start=len(ranked) + 1):
            daily = ranked_by_code.get(code)
            universe_rows.append(
                {
                    "scrip_code": code,
                    "symbol": (
                        str(daily["symbol"])
                        if daily is not None
                        else str(symbol_by_code.get(code, code.removeprefix("CDX_")))
                    ),
                    "opportunity_rank": (
                        int(daily["opportunity_rank"])
                        if daily is not None
                        else fallback_rank
                    ),
                    "opportunity_probability": (
                        float(daily["opportunity_probability"])
                        if daily is not None
                        else None
                    ),
                }
            )
        shortlist = pd.DataFrame(universe_rows)
        codes = sorted(active)
        precise_rows = await client.candle_rows_history(
            Interval.M15, codes, start=now - timedelta(hours=48), end=now
        )
        candles = {
            code: [candle_from_api(code, Interval.M15, row) for row in rows]
            for code, rows in precise_rows.items()
        }
    report = build_pre_takeoff_watch("crypto", shortlist, candles, observed_at=now)
    report["source"] = source
    report["active_pairs"] = len(active)
    report["daily_ranked_pairs"] = len(ranked)
    report["requested_pairs"] = len(codes)
    latest_bar = _latest_completed_bar(candles, now)
    report["latest_completed_bar"] = latest_bar.isoformat() if latest_bar else None
    # Source metadata is appended before the final integrity hash and durable write.
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    report["watch_plans"] = _update_watch_plans(
        report, candles, observed_at=now, output_root=output_root
    )
    report["new_plan_registration_open"] = report["watch_plans"][
        "new_plan_registration_open"
    ]
    report.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = canonical_sha256(report)
    _persist_watch(report, output_root)

    # M21 is a separate forward experiment with its own ledgers and qualification
    # denominator. Reusing this already-fetched full-universe frame keeps the scheduled
    # task bounded, while persisting M20 first ensures the benchmark remains independent
    # of any challenger failure. A non-default M20 root gets a contained sibling only for
    # tests/callers that deliberately isolate runtime artifacts.
    from tradedesk.momentum_ignition_watch import (
        DEFAULT_OUTPUT_ROOT as MOMENTUM_DEFAULT_OUTPUT_ROOT,
    )
    from tradedesk.momentum_ignition_watch import (
        collect_momentum_ignition_watch,
        precise_crypto_frames,
    )

    challenger_root = momentum_output_root
    if challenger_root is None:
        challenger_root = (
            MOMENTUM_DEFAULT_OUTPUT_ROOT
            if output_root == DEFAULT_OUTPUT_ROOT
            else output_root / "momentum_ignition_watch"
        )
    precise_frames = precise_crypto_frames(precise_rows)
    momentum_source = {
        **source,
        "active_pairs": len(active),
        "requested_pairs": len(codes),
        "shared_m15_fetch_with_m20": True,
        "m15_volume_contract": "coindcx_raw_fractional_base_asset_v1",
    }
    collect_momentum_ignition_watch(
        shortlist,
        precise_frames,
        observed_at=now,
        output_root=challenger_root,
        source=momentum_source,
    )

    from tradedesk.momentum_ignition_watch_m22 import (
        DEFAULT_OUTPUT_ROOT as MOMENTUM_M22_DEFAULT_OUTPUT_ROOT,
    )
    from tradedesk.momentum_ignition_watch_m22 import (
        collect_momentum_ignition_watch_m22,
    )

    m22_root = momentum_m22_output_root
    if m22_root is None:
        m22_root = (
            MOMENTUM_M22_DEFAULT_OUTPUT_ROOT
            if output_root == DEFAULT_OUTPUT_ROOT
            else output_root / "momentum_ignition_watch_m22"
        )
    collect_momentum_ignition_watch_m22(
        shortlist,
        precise_frames,
        observed_at=now,
        output_root=m22_root,
        source=momentum_source,
    )
    return report


def load_pre_takeoff_watch(
    market: str,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    path = output_root / market / "latest.json"
    if not path.exists():
        report: dict[str, Any] = {
            "version": VERSION,
            "market": market,
            "status": "not_collected",
            "watch_candidates": 0,
            "tradeable_calls": 0,
            "candidates": [],
            "authority": "research_watch_only",
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
            "detail": "No forward pre-takeoff snapshot has been collected.",
        }
        report["watch_plans"] = {
            "evidence": load_pre_takeoff_evidence(
                market, output_root=output_root, observed_at=observed_at
            )
        }
        report["collector_health"] = pre_takeoff_collector_health(
            market,
            last_observed_at=None,
            latest_completed_bar=None,
            observed_at=observed_at,
        )
        return report
    value = json.loads(path.read_text(encoding="utf-8"))
    supplied = value.get("snapshot_sha256")
    body = dict(value)
    body.pop("snapshot_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("pre-takeoff watch snapshot hash mismatch")
    report = dict(value)
    plans = dict(report.get("watch_plans") or {})
    plans["evidence"] = load_pre_takeoff_evidence(
        market, output_root=output_root, observed_at=observed_at
    )
    report["watch_plans"] = plans
    report["collector_health"] = pre_takeoff_collector_health(
        market,
        last_observed_at=str(report.get("observed_at") or "") or None,
        latest_completed_bar=(
            str(report["latest_completed_bar"])
            if report.get("latest_completed_bar")
            else None
        ),
        observed_at=observed_at,
    )
    return report
