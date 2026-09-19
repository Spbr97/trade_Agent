"""Point-in-time event reconstruction for anticipatory early momentum."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import joblib
import pandas as pd

from tradedesk.backtest.null_baseline import sessions_in
from tradedesk.broker.indstocks.models import Interval
from tradedesk.config import load_config
from tradedesk.engine.indicators import daily_features, intraday_features
from tradedesk.markets.market import nse_market
from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT, AemContract
from tradedesk_lab.aem_detector import detect_daily_candidate, evaluate_trigger
from tradedesk_lab.aem_labels import label_trade
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.mcb_dataset import _read_frames
from tradedesk_lab.mcb_features import time_of_day_rvol

AEM_OUTPUT = OUTPUT / "aem"


@dataclass
class AemDataset:
    events: pd.DataFrame
    manifest: dict
    contract: AemContract


def _complete_signal_session(frame: pd.DataFrame) -> bool:
    if len(frame) < 2:
        return False
    idx = pd.DatetimeIndex(frame.index)
    bar = idx.to_series().diff().dropna().median()
    if not isinstance(bar, pd.Timedelta) or bar <= pd.Timedelta(0):
        return False
    expected = int(pd.Timedelta(minutes=375) / bar)
    return bool(
        len(frame) in {expected, expected + 1}
        and idx[0].strftime("%H:%M") == "09:15"
        and (idx.to_series().diff().iloc[1:] == bar).all()
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
) -> tuple[pd.DataFrame, dict]:
    """Keep every eligible daily session, including rejected intraday opportunities."""
    rows: list[dict] = []
    audit: Counter = Counter()
    for code in sorted(set(daily) & set(intraday)):
        print(f"AEM events: {code}", flush=True)
        day_frame = daily_features(daily[code].sort_index())
        enriched = intraday_features(intraday[code].sort_index())
        enriched["tod_rvol"] = time_of_day_rvol(
            enriched,
            lookback_sessions=contract.rvol_lookback_sessions,
            min_sessions=contract.rvol_min_sessions,
        )
        execution_by_date: dict = {}
        if execution is not None and code in execution:
            execution_frame = execution[code]
            for execution_start, execution_end in sessions_in(execution_frame.index):
                execution_session = execution_frame.iloc[execution_start : execution_end + 1]
                execution_by_date[pd.Timestamp(execution_session.index[0]).date()] = (
                    execution_session
                )
        day_index = pd.DatetimeIndex(day_frame.index)
        day_dates = day_index.tz_convert("Asia/Kolkata").date if day_index.tz else day_index.date
        spans = sessions_in(enriched.index)
        if evaluation_sessions is not None:
            spans = spans[-evaluation_sessions:]
        for start, end in spans:
            session = enriched.iloc[start : end + 1]
            session_on = pd.Timestamp(session.index[0]).date()
            if not _complete_signal_session(session):
                audit["incomplete_session"] += 1
                continue
            prior = [value for value in day_dates if value < session_on]
            if not prior:
                audit["no_prior_daily_close"] += 1
                continue
            armed_on = max(prior)
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
            signal_bar = pd.DatetimeIndex(session.index).to_series().diff().dropna().median()
            for position in range(contract.opening_range_bars - 1, len(session) - 1):
                at = pd.Timestamp(session.index[position]) + signal_bar
                if at.strftime("%H:%M") > contract.latest_decision_time:
                    break
                current = evaluate_trigger(candidate, session, at, contract)
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
                reasons = list(best.reasons) if best else ["no_eligible_intraday_bar"]
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
            if fill_session is not session:
                audit["m1_execution"] += 1
            else:
                audit["m5_execution_fallback"] += 1
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


def _read_m1_frames(
    root: Path,
    codes: set[str],
    sessions: int | None,
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        for code in sorted(codes):
            raw = con.execute(
                "SELECT ts,open,high,low,close,volume FROM candles "
                "WHERE scrip_code=? AND interval=? ORDER BY ts",
                [code, Interval.M1.value],
            ).df()
            if raw.empty:
                continue
            raw.index = pd.DatetimeIndex(
                pd.to_datetime(raw.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            )
            spans = sessions_in(raw.index)
            if sessions is not None and len(spans) > sessions:
                raw = raw.iloc[spans[-sessions][0] :]
            frames[code] = raw
    return frames


def prepare_aem(
    root: Path = ROOT,
    output: Path = AEM_OUTPUT,
    *,
    sessions: int | None = 120,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
) -> AemDataset:
    if sessions is not None and sessions < 1:
        raise ValueError("sessions must be positive or None")
    print("AEM: loading read-only source candles", flush=True)
    daily, intraday, symbols = _read_frames(root, sessions=sessions, contract=contract, label="AEM")
    execution = _read_m1_frames(root, set(intraday), sessions)
    print("AEM: reconstructing point-in-time events", flush=True)
    settings = load_config(root)
    events, audit = build_events(
        daily,
        execution,
        symbols,
        risk=settings.risk,
        costs=nse_market(settings).costs,
        execution=execution,
        contract=contract,
        evaluation_sessions=sessions,
    )
    run_id = uuid4().hex
    serial = events.copy()
    if "reasons" in serial:
        serial["reasons"] = serial["reasons"].map(json.dumps)
    dataset_hash = hashlib.sha256(
        pd.util.hash_pandas_object(serial, index=True).values.tobytes()
    ).hexdigest()
    resolved = events.loc[events.status == "resolved"] if "status" in events else events.iloc[:0]
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
        "symbols_with_daily_and_m5": len(symbols),
        "symbols_with_m1_execution": len(execution),
        "events": len(events),
        "resolved_trades": len(resolved),
        "strict_success_rate": float(resolved.label.mean()) if len(resolved) else None,
        "mean_net_r": float(resolved.net_r.mean()) if len(resolved) else None,
        "mean_minutes_held": float(resolved.minutes_held.mean()) if len(resolved) else None,
        "dataset_sha256": dataset_hash,
        "audit": audit,
        "limitations": [
            "Historical diagnostic only; thresholds are hypotheses, not optimized proof.",
            "The ten-symbol M5 universe is selected and survivorship-biased.",
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
