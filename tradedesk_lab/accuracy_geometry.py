"""Coverage-first quick-profit geometry experiment for the clean accuracy cohort.

This is historical research on an already-inspected candidate population.  It changes
neither production setup geometry nor live eligibility.  The chronological tail is only
opened when one development geometry clears every frozen evidence gate.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.config import load_config
from tradedesk.markets.costs import CostModel
from tradedesk.markets.market import nse_market
from tradedesk.models import Side, TradeType, price_decimal, qty_decimal
from tradedesk.risk.sizing import SizeInputs, gap95_pct, position_size
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.dataset import Dataset


@dataclass(frozen=True)
class AccuracyGeometryProtocol:
    version: str = "accuracy-geometry-v1"
    entry_modes: tuple[str, ...] = ("saved_fill", "next_session_open")
    stop_atrs: tuple[float, ...] = (0.75, 1.0, 1.25)
    target_rs: tuple[float, ...] = (0.25, 0.5, 0.75)
    max_holds: tuple[int, ...] = (1, 2, 3)
    final_test_frac: float = 0.20
    min_calls: int = 500
    min_active_sessions: int = 100
    min_session_coverage: float = 0.40
    min_accuracy: float = 0.80
    min_wilson_lower: float = 0.70
    min_session_target_rate: float = 0.70
    min_expectancy_r: float = 0.0

    @property
    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_GEOMETRY_PROTOCOL = AccuracyGeometryProtocol()

_WORKER_CALLS: list[PreparedCall] | None = None
_WORKER_PROTOCOL: AccuracyGeometryProtocol | None = None
_WORKER_ROOT: Path | None = None


@dataclass(frozen=True)
class PriceBar:
    on: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class EntrySpec:
    on: date
    fill: float
    at_open: bool
    future: tuple[PriceBar, ...]


@dataclass
class PreparedCall:
    signal_id: str
    setup: str
    adx_regime: str
    market_5d: str
    armed_on: date
    atr: float
    bars: pd.DataFrame
    gap95: float | None
    entries: dict[str, EntrySpec]
    qty_cache: dict[tuple[str, float], float]


def _initialize_geometry_worker(
    calls: list[PreparedCall], protocol: AccuracyGeometryProtocol, root: Path
) -> None:
    global _WORKER_CALLS, _WORKER_PROTOCOL, _WORKER_ROOT
    _WORKER_CALLS = calls
    _WORKER_PROTOCOL = protocol
    _WORKER_ROOT = root


def _evaluate_combination(combination: tuple[str, float, float, int]) -> dict[str, Any]:
    if _WORKER_CALLS is None or _WORKER_PROTOCOL is None or _WORKER_ROOT is None:
        raise RuntimeError("geometry worker was not initialized")
    entry_mode, stop_atr, target_r, max_hold = combination
    return evaluate_geometry(
        _WORKER_CALLS,
        entry_mode=entry_mode,
        stop_atr=stop_atr,
        target_r=target_r,
        max_hold=max_hold,
        protocol=_WORKER_PROTOCOL,
        root=_WORKER_ROOT,
    )


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    rate = wins / total
    denominator = 1 + z * z / total
    centre = rate + z * z / (2 * total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return float((centre - spread) / denominator)


def chronological_partition(
    frame: pd.DataFrame, final_test_frac: float
) -> tuple[pd.DataFrame, pd.DataFrame, date]:
    work = frame.copy()
    work["_armed_date"] = [pd.Timestamp(value).date() for value in work["armed_on"]]
    work = work.sort_values(["_armed_date", "signal_id"]).reset_index(drop=True)
    sessions = sorted(work["_armed_date"].unique())
    if len(sessions) < 2:
        raise ValueError("geometry experiment requires at least two sessions")
    split_pos = max(1, min(len(sessions) - 1, int(len(sessions) * (1 - final_test_frac))))
    split_at = sessions[split_pos]
    development = work[work["_armed_date"] < split_at].reset_index(drop=True)
    locked = work[work["_armed_date"] >= split_at].reset_index(drop=True)
    return development, locked, split_at


def _next_exchange_session(calendar: pd.DatetimeIndex, armed_on: date) -> pd.Timestamp | None:
    normalized = pd.DatetimeIndex(calendar).tz_localize(None).normalize()
    position = int(normalized.searchsorted(pd.Timestamp(armed_on), side="right"))
    return None if position >= len(normalized) else pd.Timestamp(normalized[position])


def _entry_spec(
    bars: pd.DataFrame,
    on: pd.Timestamp,
    fill: float,
    at_open: bool,
    max_hold: int,
) -> EntrySpec:
    future = bars.loc[bars.index >= on].head(max_hold + 1)
    path = tuple(
        PriceBar(
            on=pd.Timestamp(cast(Any, stamp)).date(),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
        )
        for stamp, candle in future.iterrows()
    )
    return EntrySpec(on=on.date(), fill=fill, at_open=at_open, future=path)


def prepare_calls(
    dataset: Dataset,
    frame: pd.DataFrame,
    *,
    slippage_pct: float,
    max_hold: int = 3,
) -> list[PreparedCall]:
    prepared: list[PreparedCall] = []
    gap_cache: dict[tuple[str, date], float | None] = {}
    for _, row in frame.iterrows():
        code = str(row.scrip_code)
        bars = dataset.bars.get(code)
        if bars is None or bars.empty:
            continue
        armed_on = pd.Timestamp(row.armed_on).date()
        entry_date = pd.Timestamp(row.entry_date).normalize()
        entries: dict[str, EntrySpec] = {}

        if entry_date in bars.index:
            entries["saved_fill"] = _entry_spec(
                bars, entry_date, float(row.entry), False, max_hold
            )
        next_session = _next_exchange_session(dataset.calendar, armed_on)
        if next_session is not None and next_session in bars.index:
            next_open = float(bars.loc[next_session, "open"]) * (1 + slippage_pct)
            entries["next_session_open"] = _entry_spec(
                bars, next_session, next_open, True, max_hold
            )
        key = (code, armed_on)
        if key not in gap_cache:
            gap_cache[key] = gap95_pct(bars.loc[: pd.Timestamp(armed_on)])
        prepared.append(
            PreparedCall(
                signal_id=str(row.signal_id),
                setup=str(row.setup),
                adx_regime=(
                    "weak" if float(row.adx14) < 20 else
                    "medium" if float(row.adx14) < 30 else "strong"
                ),
                market_5d="up" if float(row.nifty_return_5d) > 0 else "flat_or_down",
                armed_on=armed_on,
                atr=float(row.atr),
                bars=bars,
                gap95=gap_cache[key],
                entries=entries,
                qty_cache={},
            )
        )
    return prepared


def _quick_outcome(
    entry: EntrySpec,
    *,
    stop: float,
    target: float,
    max_hold: int,
    qty: float,
    costs: CostModel,
) -> tuple[int, float] | None:
    """Exact full-exit subset of ``simulate_outcome`` without DataFrame/Pydantic churn."""
    if len(entry.future) < max_hold + 1:
        return None
    slip = 1 - float(costs.slippage_pct)
    exit_price: float | None = None
    exit_on: date | None = None
    target_hit = False
    for session, bar in enumerate(entry.future[: max_hold + 1]):
        if session > 0 and bar.open <= stop:
            exit_price = bar.open * slip
        elif bar.low <= stop:
            exit_price = stop * slip
        else:
            target_available = bar.high >= target
            if session == 0 and not entry.at_open and bar.close < target:
                target_available = False
            if target_available:
                exit_price = target * slip
                target_hit = True
            elif session == max_hold:
                exit_price = bar.close * slip
        if exit_price is not None:
            exit_on = bar.on
            break
    if exit_price is None or exit_on is None:
        return None

    trade_type = TradeType.INTRADAY if exit_on == entry.on else TradeType.DELIVERY
    charges = costs.leg_cost(
        side=Side.BUY,
        trade_type=trade_type,
        qty=qty,
        price=price_decimal(entry.fill),
    ).total
    charges += costs.leg_cost(
        side=Side.SELL,
        trade_type=trade_type,
        qty=qty,
        price=price_decimal(exit_price),
        dp_applies=trade_type == TradeType.DELIVERY,
    ).total
    gross = (price_decimal(exit_price) - price_decimal(entry.fill)) * qty_decimal(qty)
    net = gross - Decimal(charges)
    initial_risk = (price_decimal(entry.fill) - price_decimal(stop)) * qty_decimal(qty)
    return int(target_hit and net > 0), float(net / initial_risk)


def _failures(summary: dict[str, Any], protocol: AccuracyGeometryProtocol) -> list[str]:
    checks = (
        (summary["n_selected"] >= protocol.min_calls, "selected calls"),
        (summary["active_sessions"] >= protocol.min_active_sessions, "active sessions"),
        (summary["session_coverage"] >= protocol.min_session_coverage, "session coverage"),
        (summary["observed_success"] >= protocol.min_accuracy, "observed success"),
        (summary["wilson_lower_bound"] >= protocol.min_wilson_lower, "Wilson lower bound"),
        (
            summary["session_target_rate"] >= protocol.min_session_target_rate,
            "successful-session rate",
        ),
        (summary["expectancy_r"] >= protocol.min_expectancy_r, "after-cost expectancy"),
    )
    return [name for passed, name in checks if not passed]


def quick_geometry_records(
    calls: list[PreparedCall],
    *,
    entry_mode: str,
    stop_atr: float,
    target_r: float,
    max_hold: int,
    root: Path = ROOT,
) -> pd.DataFrame:
    """Resolve one frozen geometry into per-call labels for downstream validation."""
    settings = load_config(root)
    market = nse_market(settings)
    records: list[dict[str, Any]] = []
    for call in calls:
        entry_spec = call.entries.get(entry_mode)
        if entry_spec is None:
            continue
        entry = entry_spec.fill
        stop = entry - stop_atr * call.atr
        target = entry + target_r * (entry - stop)
        size_key = (entry_mode, stop_atr)
        if size_key not in call.qty_cache:
            call.qty_cache[size_key] = position_size(
                SizeInputs(
                    equity=float(settings.risk.trading_capital),
                    entry=entry,
                    stop=stop,
                    max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
                    max_position_value_pct=float(settings.risk.max_position_value_pct),
                    size_multiplier=float(settings.risk.regime_size_multiplier.neutral),
                    gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
                    gap95_pct=call.gap95,
                    available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
                )
            ).qty
        qty = call.qty_cache[size_key]
        if qty <= 0:
            continue
        outcome = _quick_outcome(
            entry_spec,
            stop=stop,
            target=target,
            max_hold=max_hold,
            qty=qty,
            costs=market.costs,
        )
        if outcome is None:
            continue
        won, net_r = outcome
        records.append(
            {
                "signal_id": call.signal_id,
                "armed_on": call.armed_on,
                "setup": call.setup,
                "label": won,
                "net_r": net_r,
            }
        )
    return pd.DataFrame.from_records(records)


def evaluate_geometry(
    calls: list[PreparedCall],
    *,
    entry_mode: str,
    stop_atr: float,
    target_r: float,
    max_hold: int,
    protocol: AccuracyGeometryProtocol = DEFAULT_GEOMETRY_PROTOCOL,
    root: Path = ROOT,
) -> dict[str, Any]:
    settings = load_config(root)
    market = nse_market(settings)
    session_counts: dict[date, int] = defaultdict(int)
    session_wins: dict[date, int] = defaultdict(int)
    subgroup_values: dict[str, dict[str, list[float]]] = {
        "setup": defaultdict(list),
        "adx_regime": defaultdict(list),
        "market_5d": defaultdict(list),
    }
    subgroup_wins: dict[str, dict[str, int]] = {
        "setup": defaultdict(int),
        "adx_regime": defaultdict(int),
        "market_5d": defaultdict(int),
    }
    realised: list[float] = []
    wins = 0
    entries = 0
    unresolved = 0
    untradeable = 0
    for call in calls:
        entry_spec = call.entries.get(entry_mode)
        if entry_spec is None:
            continue
        entry = entry_spec.fill
        entries += 1
        stop = entry - stop_atr * call.atr
        target = entry + target_r * (entry - stop)
        size_key = (entry_mode, stop_atr)
        if size_key not in call.qty_cache:
            call.qty_cache[size_key] = position_size(
                SizeInputs(
                    equity=float(settings.risk.trading_capital),
                    entry=entry,
                    stop=stop,
                    max_risk_pct=float(settings.risk.max_risk_per_trade_pct),
                    max_position_value_pct=float(settings.risk.max_position_value_pct),
                    size_multiplier=float(settings.risk.regime_size_multiplier.neutral),
                    gap_risk_cap_pct=float(settings.risk.gap_risk_cap_pct),
                    gap95_pct=call.gap95,
                    available_heat_pct=float(settings.risk.max_portfolio_heat_pct),
                )
            ).qty
        qty = call.qty_cache[size_key]
        if qty <= 0:
            untradeable += 1
            continue
        outcome = _quick_outcome(
            entry_spec,
            stop=stop,
            target=target,
            max_hold=max_hold,
            qty=qty,
            costs=market.costs,
        )
        if outcome is None:
            unresolved += 1
            continue
        won, net_r = outcome
        wins += won
        realised.append(net_r)
        session_counts[call.armed_on] += 1
        session_wins[call.armed_on] += won
        for dimension, value in (
            ("setup", call.setup),
            ("adx_regime", call.adx_regime),
            ("market_5d", call.market_5d),
        ):
            subgroup_values[dimension][value].append(net_r)
            subgroup_wins[dimension][value] += won

    selected = len(realised)
    all_sessions = len({call.armed_on for call in calls})
    active_sessions = len(session_counts)
    successful_sessions = sum(
        session_wins[session] / count >= protocol.min_accuracy
        for session, count in session_counts.items()
    )
    summary: dict[str, Any] = {
        "entry_mode": entry_mode,
        "stop_atr": stop_atr,
        "target_r": target_r,
        "max_hold": max_hold,
        "n_candidates": len(calls),
        "n_entries": entries,
        "n_selected": selected,
        "unresolved": unresolved,
        "untradeable": untradeable,
        "wins": wins,
        "observed_success": wins / selected if selected else 0.0,
        "wilson_lower_bound": wilson_lower_bound(wins, selected),
        "active_sessions": active_sessions,
        "total_sessions": all_sessions,
        "session_coverage": active_sessions / all_sessions if all_sessions else 0.0,
        "sessions_meeting_target": successful_sessions,
        "session_target_rate": (
            successful_sessions / active_sessions if active_sessions else 0.0
        ),
        "expectancy_r": float(np.mean(realised)) if realised else 0.0,
        "diagnostics": {
            dimension: [
                {
                    "value": value,
                    "n": len(values),
                    "wins": subgroup_wins[dimension][value],
                    "accuracy": subgroup_wins[dimension][value] / len(values),
                    "wilson_lower_bound": wilson_lower_bound(
                        subgroup_wins[dimension][value], len(values)
                    ),
                    "expectancy_r": float(np.mean(values)),
                }
                for value, values in sorted(groups.items())
            ]
            for dimension, groups in subgroup_values.items()
        },
    }
    summary["failures"] = _failures(summary, protocol)
    summary["qualified"] = not summary["failures"]
    return summary


def _group_summary(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    output = []
    for value, group in frame.groupby(column, dropna=False, observed=True):
        output.append(
            {
                "value": str(value),
                "n": len(group),
                "wins": int(group["label"].sum()),
                "accuracy": float(group["label"].mean()),
                "mean_net_r": float(group["net_r"].mean()),
            }
        )
    return output


def failure_audit(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.copy()
    work["adx_regime"] = pd.cut(
        work["adx14"], [-np.inf, 20, 30, np.inf], labels=("weak", "medium", "strong")
    )
    work["market_5d"] = np.where(work["nifty_return_5d"] > 0, "up", "flat_or_down")
    resolution = (
        pd.to_datetime(work["label_end_date"]) - pd.to_datetime(work["entry_date"])
    ).dt.days
    return {
        "overall": {
            "n": len(work),
            "wins": int(work["label"].sum()),
            "accuracy": float(work["label"].mean()),
            "mean_net_r": float(work["net_r"].mean()),
            "median_calendar_days_to_resolution": float(resolution.median()),
        },
        "by_setup": _group_summary(work, "setup"),
        "by_adx_regime": _group_summary(work, "adx_regime"),
        "by_market_5d": _group_summary(work, "market_5d"),
    }


def run_accuracy_geometry(
    dataset: Dataset,
    protocol: AccuracyGeometryProtocol = DEFAULT_GEOMETRY_PROTOCOL,
    *,
    root: Path = ROOT,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    development, locked, split_at = chronological_partition(
        dataset.frame, protocol.final_test_frac
    )
    settings = load_config(root)
    slippage = float(nse_market(settings).costs.slippage_pct)
    calls = prepare_calls(
        dataset, development, slippage_pct=slippage, max_hold=max(protocol.max_holds)
    )
    combinations = list(
        product(
            protocol.entry_modes,
            protocol.stop_atrs,
            protocol.target_rs,
            protocol.max_holds,
        )
    )
    candidates: list[dict[str, Any]] = []
    if workers > 1:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_initialize_geometry_worker,
            initargs=(calls, protocol, root),
        ) as executor:
            for index, candidate in enumerate(
                executor.map(_evaluate_combination, combinations), 1
            ):
                candidates.append(candidate)
                if progress is not None:
                    progress(index, len(combinations))
    else:
        for index, (entry_mode, stop_atr, target_r, max_hold) in enumerate(combinations, 1):
            candidates.append(
                evaluate_geometry(
                    calls,
                    entry_mode=entry_mode,
                    stop_atr=stop_atr,
                    target_r=target_r,
                    max_hold=max_hold,
                    protocol=protocol,
                    root=root,
                )
            )
            if progress is not None:
                progress(index, len(combinations))
    qualified = [row for row in candidates if row["qualified"]]
    nominee = max(
        qualified,
        key=lambda row: (
            row["wilson_lower_bound"],
            row["observed_success"],
            row["expectancy_r"],
        ),
        default=None,
    )
    result: dict[str, Any] = {
        "created_at": datetime.now(IST).isoformat(),
        "status": "abstain" if nominee is None else "development_pass",
        "protocol": asdict(protocol) | {"sha256": protocol.sha256},
        "data_source": dataset.manifest,
        "development": {
            "rows": len(development),
            "sessions": int(development["_armed_date"].nunique()),
            "split_before": split_at.isoformat(),
            "historical_research": True,
        },
        "failure_audit": failure_audit(development),
        "candidates": candidates,
        "nominee": nominee,
        "locked_test": {"status": "not_opened_no_nominee", "rows": len(locked)},
        "detail": "no quick-profit geometry cleared every development gate",
    }
    if nominee is None:
        return result

    locked_calls = prepare_calls(
        dataset, locked, slippage_pct=slippage, max_hold=max(protocol.max_holds)
    )
    locked_result = evaluate_geometry(
        locked_calls,
        entry_mode=str(nominee["entry_mode"]),
        stop_atr=float(nominee["stop_atr"]),
        target_r=float(nominee["target_r"]),
        max_hold=int(nominee["max_hold"]),
        protocol=protocol,
        root=root,
    )
    result["locked_test"] = locked_result
    result["status"] = "locked_pass" if locked_result["qualified"] else "locked_fail"
    result["detail"] = "nominee evaluated once on locked tail"
    return result


def save_accuracy_geometry(result: dict[str, Any], output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
    path = output / f"{timestamp}.json"
    payload = json.dumps(result, indent=2, allow_nan=False, default=str)
    path.write_text(payload, encoding="utf-8")
    (output / "latest.json").write_text(payload, encoding="utf-8")
    return path
