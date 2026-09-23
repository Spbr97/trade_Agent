"""Point-in-time event reconstruction for anticipatory early momentum."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd

from tradedesk.backtest.null_baseline import sessions_in
from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features, intraday_features
from tradedesk.markets.market import nse_market
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT, AemContract
from tradedesk_lab.aem_data import read_aem_source, regular_minutes
from tradedesk_lab.aem_detector import detect_daily_candidate, evaluate_trigger
from tradedesk_lab.aem_labels import label_trade
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.mcb_features import time_of_day_rvol

AEM_OUTPUT = OUTPUT / "aem"


@dataclass
class AemDataset:
    events: pd.DataFrame
    manifest: dict
    contract: AemContract


def _complete_signal_session(frame: pd.DataFrame) -> bool:
    if len(frame) != 375:
        return False
    idx = pd.DatetimeIndex(frame.index)
    return bool(
        idx[0].strftime("%H:%M") == "09:15"
        and idx[-1].strftime("%H:%M") == "15:29"
        and (idx.to_series().diff().iloc[1:] == pd.Timedelta(minutes=1)).all()
    )


def build_events(
    daily: dict[str, pd.DataFrame],
    intraday: dict[str, pd.DataFrame],
    symbols: dict[str, str],
    *,
    risk,
    costs,
    execution: dict[str, pd.DataFrame] | None = None,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
    evaluation_sessions: int | None = None,
    trading_dates: list[date] | None = None,
    evaluation_dates: list[date] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Keep every eligible daily session, including rejected intraday opportunities."""
    rows: list[dict] = []
    audit: Counter = Counter()
    calendar = sorted(
        set(
            trading_dates
            or [pd.Timestamp(stamp).date() for frame in daily.values() for stamp in frame.index]
        )
    )
    previous_session = dict(zip(calendar[1:], calendar[:-1], strict=True))
    allowed_dates = set(evaluation_dates) if evaluation_dates is not None else None
    for code in sorted(set(daily) & set(intraday)):
        print(f"AEM events: {code}", flush=True)
        day_frame = daily_features(daily[code].sort_index())
        raw = regular_minutes(intraday[code].sort_index())
        if raw.empty:
            audit["no_regular_minute_bars"] += 1
            continue
        if raw.index.has_duplicates:
            raise ValueError(f"duplicate_minute_candles:{code}")
        prices = raw[["open", "high", "low", "close"]]
        valid = (
            np.isfinite(raw[["open", "high", "low", "close", "volume"]]).all(axis=1)
            & (prices > 0).all(axis=1)
            & (raw.volume >= 0)
            & (raw.high >= raw[["open", "low", "close"]].max(axis=1))
            & (raw.low <= raw[["open", "high", "close"]].min(axis=1))
        )
        if not valid.all():
            invalid_dates = set(pd.DatetimeIndex(raw.index[~valid]).date)
            audit["invalid_ohlcv_sessions"] += len(invalid_dates)
            audit["invalid_ohlcv_bars"] += int((~valid).sum())
            # A later bad session must not erase an earlier valid event. Exclude
            # affected sessions before they can contaminate indicator baselines.
            raw = raw.loc[~pd.Index(pd.DatetimeIndex(raw.index).date).isin(invalid_dates)]
            if raw.empty:
                continue
        enriched = intraday_features(raw)
        enriched["tod_rvol"] = time_of_day_rvol(
            enriched,
            lookback_sessions=contract.rvol_lookback_sessions,
            min_sessions=contract.rvol_min_sessions,
        )
        execution_by_date: dict = {}
        if execution is not None and code in execution:
            execution_frame = regular_minutes(execution[code].sort_index())
            for execution_start, execution_end in sessions_in(execution_frame.index):
                execution_session = execution_frame.iloc[execution_start : execution_end + 1]
                execution_by_date[pd.Timestamp(execution_session.index[0]).date()] = (
                    execution_session
                )
        day_index = pd.DatetimeIndex(day_frame.index)
        day_dates = day_index.tz_convert("Asia/Kolkata").date if day_index.tz else day_index.date
        spans = sessions_in(enriched.index)
        if evaluation_sessions is not None and allowed_dates is None:
            spans = spans[-evaluation_sessions:]
        for start, end in spans:
            session = enriched.iloc[start : end + 1]
            session_on = pd.Timestamp(session.index[0]).date()
            if allowed_dates is not None and session_on not in allowed_dates:
                continue
            audit["symbol_sessions_observed"] += 1
            if not _complete_signal_session(session):
                audit["incomplete_session"] += 1
                continue
            prior = [value for value in day_dates if value < session_on]
            if not prior:
                audit["no_prior_daily_close"] += 1
                continue
            armed_on = max(prior)
            if session_on not in previous_session or armed_on != previous_session[session_on]:
                audit["stale_or_missing_prior_daily_close"] += 1
                continue
            try:
                candidate = detect_daily_candidate(
                    code, symbols.get(code, code), day_frame, armed_on, contract
                )
            except ValueError as exc:
                audit[str(exc)] += 1
                continue
            if candidate is None:
                audit["daily_conditions_failed"] += 1
                continue
            audit["daily_candidates"] += 1
            decision, best, best_score = None, None, -1
            signal_error = None
            signal_bar = pd.Timedelta(minutes=contract.signal_interval_minutes)
            for position in range(contract.opening_range_bars - 1, len(session) - 1):
                at = pd.Timestamp(session.index[position]) + signal_bar
                if at.strftime("%H:%M") > contract.latest_decision_time:
                    break
                try:
                    current = evaluate_trigger(candidate, session, at, contract)
                except ValueError as exc:
                    if str(exc) not in {
                        "invalid_signal_bar",
                        "invalid_signal_timestamps",
                        "stale_signal_bar",
                        "incomplete_signal_session",
                    }:
                        raise
                    signal_error = str(exc)
                    audit[signal_error] += 1
                    break
                score = sum(bool(value) for value in current.features["checks"].values())
                if score > best_score:
                    best, best_score = current, score
                if current.decision == "TRADE":
                    decision = current
                    break
            base = {
                "event_id": f"{candidate.identifier}:{session_on}",
                "candidate_id": candidate.identifier,
                "scrip_code": code,
                "symbol": candidate.symbol,
                "session_date": str(session_on),
                "armed_on": str(candidate.armed_on),
                "strategy_version": contract.strategy_version,
                "feature_version": contract.feature_version,
                "label_version": contract.label_version,
                "contract_sha256": contract.sha256,
                **{
                    f"daily_{key}": value
                    for key, value in candidate.features.items()
                    if key != "checks"
                },
            }
            if decision is None:
                reasons = (
                    [signal_error]
                    if signal_error
                    else list(best.reasons)
                    if best
                    else ["no_eligible_intraday_bar"]
                )
                rows.append(
                    {
                        **base,
                        "decision": "NO_TRADE",
                        "available_at": best.available_at if best else None,
                        "reasons": reasons,
                        "label": None,
                        "strict_success": None,
                        "net_r": None,
                    }
                )
                audit["no_trade_sessions"] += 1
                for reason in reasons:
                    audit[f"reject_{reason}"] += 1
                continue
            fill_session = execution_by_date.get(session_on, session)
            audit["m1_execution"] += 1
            result = label_trade(candidate, decision, fill_session, risk, costs, contract)
            audit[f"outcome_{result['status']}"] += 1
            rows.append(
                {
                    **base,
                    "decision": "TRADE",
                    "available_at": decision.available_at,
                    "reasons": list(decision.reasons),
                    **{
                        f"intraday_{key}": value
                        for key, value in decision.features.items()
                        if key != "checks"
                    },
                    **result,
                }
            )
    events = pd.DataFrame(rows)
    if not events.empty:
        events = events.sort_values(["session_date", "event_id"]).reset_index(drop=True)
        if events.event_id.duplicated().any():
            raise ValueError("duplicate AEM event identifiers")
    return events, dict(audit)


def prepare_aem(
    root: Path = ROOT,
    output: Path = AEM_OUTPUT,
    *,
    sessions: int | None = 120,
    as_of: date | None = None,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
) -> AemDataset:
    if sessions is not None and sessions < 1:
        raise ValueError("sessions must be positive or None")
    print("AEM: loading read-only source candles", flush=True)
    settings = load_config(root)
    daily, execution, symbols, calendar, evaluation_dates, source = read_aem_source(
        root,
        contract=contract,
        sessions=sessions,
        as_of=as_of,
        benchmark_symbol=settings.universe.benchmark,
    )
    print("AEM: reconstructing point-in-time events", flush=True)
    events, audit = build_events(
        daily,
        execution,
        symbols,
        risk=settings.risk,
        costs=nse_market(settings).costs,
        execution=execution,
        contract=contract,
        evaluation_sessions=sessions,
        trading_dates=calendar,
        evaluation_dates=evaluation_dates,
    )
    run_id = uuid4().hex
    serial = events.copy()
    if "reasons" in serial:
        serial["reasons"] = serial["reasons"].map(json.dumps)
    dataset_hash = hashlib.sha256(
        pd.util.hash_pandas_object(serial, index=True).values.tobytes()
    ).hexdigest()
    resolved = events.loc[events.status == "resolved"] if "status" in events else events.iloc[:0]
    from tradedesk_lab.aem_report import summarize_aem

    summary = summarize_aem(events, [str(day) for day in evaluation_dates])
    dependencies = [
        "tradedesk_lab/aem_contract.py",
        "tradedesk_lab/aem_data.py",
        "tradedesk_lab/aem_dataset.py",
        "tradedesk_lab/aem_features.py",
        "tradedesk_lab/aem_detector.py",
        "tradedesk_lab/aem_labels.py",
        "tradedesk_lab/aem_report.py",
        "tradedesk_lab/mcb_features.py",
        "src/tradedesk/engine/indicators.py",
        "src/tradedesk/backtest/null_baseline.py",
        "src/tradedesk/risk/costs.py",
        "src/tradedesk/risk/sizing.py",
        "src/tradedesk/markets/costs.py",
        "src/tradedesk/markets/market.py",
        "config/risk.yaml",
        "config/universe.yaml",
    ]
    manifest = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "contract": contract.to_dict(),
        "contract_sha256": contract.sha256,
        "builder_sha256": digest(Path(__file__)),
        "risk_sha256": digest(root / "config/risk.yaml"),
        "data_store": str(root / "data/tradedesk.duckdb"),
        "sessions_requested": sessions,
        "symbols_with_daily_and_m1": len(symbols),
        "symbols_with_m1_execution": len(execution),
        "events": len(events),
        "resolved_trades": len(resolved),
        "strict_success_rate": float(resolved.label.mean()) if len(resolved) else None,
        "mean_net_r": float(resolved.net_r.mean()) if len(resolved) else None,
        "mean_minutes_held": float(resolved.minutes_held.mean()) if len(resolved) else None,
        "dataset_sha256": dataset_hash,
        "source": source,
        "dependency_sha256": {name: digest(root / name) for name in dependencies},
        "evidence_class": "previously_inspected_development_sample",
        "diagnostics": summary,
        "audit": audit,
        "limitations": [
            "Historical data was repeatedly inspected in development; not out-of-sample.",
            "Available M1 symbols and current metadata retain selection/survivorship bias.",
            "Only complete regular sessions are evaluated; rejected data sessions are audited.",
            "Independent event returns do not model portfolio overlapping-position limits.",
            "Five supplied app examples are success-only and cannot establish precision.",
            "No bid/ask history; configured slippage and intraday costs are provisional.",
            "No production scanner, model, dashboard, alert, or execution path is changed.",
        ],
    }
    target = output / "datasets" / run_id
    target.mkdir(parents=True, exist_ok=False)
    joblib.dump(
        AemDataset(events=events, manifest=manifest, contract=contract), target / "dataset.joblib"
    )
    serial.to_csv(target / "events.csv", index=False)
    write_json(target / "manifest.json", manifest)
    write_json(output / "latest.json", {"id": run_id, "path": str(target)})
    return AemDataset(events=events, manifest=manifest, contract=contract)
