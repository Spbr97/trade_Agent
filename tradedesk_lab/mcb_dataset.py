"""Point-in-time MCB event reconstruction from daily candidates and M5 sessions."""

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
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.mcb_contract import DEFAULT_MCB_CONTRACT, McbContract
from tradedesk_lab.mcb_detector import detect_daily_candidate, evaluate_trigger
from tradedesk_lab.mcb_features import BAR, time_of_day_rvol
from tradedesk_lab.mcb_labels import label_trade


@dataclass
class McbDataset:
    events: pd.DataFrame
    manifest: dict
    contract: McbContract


def _complete_session(frame: pd.DataFrame) -> bool:
    if len(frame) != 75:
        return False
    idx = pd.DatetimeIndex(frame.index)
    return bool(
        idx[0].strftime("%H:%M") == "09:15"
        and idx[-1].strftime("%H:%M") == "15:25"
        and (idx.to_series().diff().iloc[1:] == BAR).all()
    )


def build_events(
    daily: dict[str, pd.DataFrame],
    intraday: dict[str, pd.DataFrame],
    symbols: dict[str, str],
    *,
    risk,
    costs,
    contract: McbContract = DEFAULT_MCB_CONTRACT,
    evaluation_sessions: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Reconstruct every daily-qualified MCB session, including sessions with no trade."""
    rows: list[dict] = []
    audit: Counter = Counter()
    for code in sorted(set(daily) & set(intraday)):
        print(f"MCB events: {code}", flush=True)
        day_frame = daily_features(daily[code].sort_index())
        raw = intraday[code].sort_index()
        if raw.empty:
            continue
        enriched = intraday_features(raw)
        enriched["tod_rvol"] = time_of_day_rvol(
            enriched,
            lookback_sessions=contract.rvol_lookback_sessions,
            min_sessions=contract.rvol_min_sessions,
        )
        day_index = pd.DatetimeIndex(day_frame.index)
        day_dates = day_index.tz_convert("Asia/Kolkata").date if day_index.tz else day_index.date
        spans = sessions_in(enriched.index)
        if evaluation_sessions is not None:
            spans = spans[-evaluation_sessions:]
        for start, end in spans:
            session = enriched.iloc[start : end + 1]
            session_on = pd.Timestamp(session.index[0]).date()
            if not _complete_session(session):
                audit["incomplete_session"] += 1
                continue
            prior = [value for value in day_dates if value < session_on]
            if not prior:
                audit["no_prior_daily_close"] += 1
                continue
            armed_on = max(prior)
            try:
                candidate = detect_daily_candidate(
                    code,
                    symbols.get(code, code),
                    day_frame,
                    armed_on,
                    contract,
                )
            except ValueError as exc:
                audit[str(exc)] += 1
                continue
            if candidate is None:
                audit["daily_conditions_failed"] += 1
                continue
            audit["daily_candidates"] += 1
            decision = None
            best = None
            best_score = -1
            # Every assessment happens after the current bar closes. Entry, if any, is
            # the following bar's open and therefore cannot be retroactive.
            for position in range(contract.opening_range_bars - 1, len(session) - 1):
                at = pd.Timestamp(session.index[position]) + BAR
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
                audit["no_trade_sessions"] += 1
                rows.append(
                    {
                        **base,
                        "decision": "NO_TRADE",
                        "available_at": best.available_at if best else None,
                        "reasons": list(best.reasons) if best else ["no_eligible_intraday_bar"],
                        "label": None,
                        "strict_success": None,
                        "net_r": None,
                    }
                )
                for reason in rows[-1]["reasons"]:
                    audit[f"reject_{reason}"] += 1
                continue
            result = label_trade(candidate, decision, session, risk, costs, contract)
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
            raise ValueError("duplicate MCB event identifiers")
    return events, dict(audit)


def _read_frames(
    root: Path,
    *,
    sessions: int | None,
    contract: McbContract,
) -> tuple[dict, dict, dict]:
    daily, intraday, symbols = {}, {}, {}
    with duckdb.connect(str(root / "data/tradedesk.duckdb"), read_only=True) as con:
        codes = [
            row[0]
            for row in con.execute(
                "SELECT scrip_code FROM candles "
                "WHERE interval IN (?,?) AND scrip_code LIKE 'NSE_%' "
                "GROUP BY scrip_code HAVING count(DISTINCT interval)=2 ORDER BY 1",
                [Interval.D1.value, Interval.M5.value],
            ).fetchall()
        ]
        print(f"MCB data: {len(codes)} symbols have daily and M5 candles", flush=True)
        for code in codes:
            print(f"MCB data: loading {code}", flush=True)
            frames = {}
            for interval in (Interval.D1, Interval.M5):
                raw = con.execute(
                    "SELECT ts,open,high,low,close,volume FROM candles "
                    "WHERE scrip_code=? AND interval=? ORDER BY ts",
                    [code, interval.value],
                ).df()
                raw.index = pd.DatetimeIndex(
                    pd.to_datetime(raw.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                )
                if raw.index.has_duplicates:
                    raise ValueError(f"duplicate {interval.value} candles for {code}")
                frames[interval] = raw
            m5 = frames[Interval.M5]
            spans = sessions_in(m5.index)
            if sessions is not None and len(spans) > sessions + contract.rvol_min_sessions:
                m5 = m5.iloc[spans[-sessions - contract.rvol_min_sessions][0] :]
            daily[code], intraday[code] = frames[Interval.D1], m5
            row = con.execute(
                "SELECT trading_symbol FROM instruments WHERE scrip_code=?", [code]
            ).fetchone()
            symbols[code] = row[0] if row else code
    return daily, intraday, symbols


def prepare_mcb(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    sessions: int | None = 120,
    contract: McbContract = DEFAULT_MCB_CONTRACT,
) -> McbDataset:
    if sessions is not None and sessions < 1:
        raise ValueError("sessions must be positive or None")
    print("MCB: loading read-only source candles", flush=True)
    daily, intraday, symbols = _read_frames(root, sessions=sessions, contract=contract)
    print("MCB: reconstructing point-in-time events", flush=True)
    settings = load_config(root)
    events, audit = build_events(
        daily,
        intraday,
        symbols,
        risk=settings.risk,
        costs=nse_market(settings).costs,
        contract=contract,
        evaluation_sessions=sessions,
    )
    run_id = uuid4().hex
    serial = events.copy()
    for column in ("reasons",):
        if column in serial:
            serial[column] = serial[column].map(json.dumps)
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
        "events": len(events),
        "resolved_trades": len(resolved),
        "strict_success_rate": float(resolved.label.mean()) if len(resolved) else None,
        "mean_net_r": float(resolved.net_r.mean()) if len(resolved) else None,
        "dataset_sha256": dataset_hash,
        "audit": audit,
        "limitations": [
            "Historical diagnostic only; all source periods may already have been inspected.",
            "Available M5 symbols are a selected, survivorship-biased pilot universe.",
            "No bid/ask history; configured slippage is an estimate.",
            "Intraday costs remain provisional until a real intraday contract note exists.",
            "Daily qualification uses prior closes; app reports are hypotheses only.",
            "No production setup, model, dashboard, alert or execution path is changed.",
        ],
    }
    dataset = McbDataset(events=events, manifest=manifest, contract=contract)
    folder = output / "mcb/datasets" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    joblib.dump(dataset, folder / "dataset.joblib")
    serial.to_csv(folder / "events.csv", index=False)
    write_json(folder / "manifest.json", manifest)
    write_json(output / "mcb/dataset-latest.json", {"id": run_id})
    return dataset
