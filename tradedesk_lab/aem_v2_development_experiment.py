"""Milestone 4: the bounded AEM v2 development experiment.

Stage 1 of this module (``build_development_dataset``) is the first place in this
project that actually runs the Milestone 1 opportunity/outcome engine and the
Milestone 2 feature registry together, over the real frozen 50-stock cohort, restricted
to exactly the (symbol, session) pairs the Milestone 2 universe audit included. It
produces one row per (opportunity, quick-profit geometry): the 34 causal features plus
the realized label (strict success) and net R for that geometry - genuinely new,
real, resolved evidence, not a synthetic example.

This is still not an accuracy claim. A labeled dataset is not a fitted, validated
selector. Stage 2 (not yet built) registers the bounded pipeline race and runs the
nested chronological walk-forward evaluation this dataset exists to support.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from tradedesk.config import load_config
from tradedesk.markets.market import nse_market
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_staged_data import read_staged_aem_source
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import IST, reconstruct_session_opportunities, resolve_opportunity
from tradedesk_lab.aem_v2_features import compute_features
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

RISK_PCT = 0.005  # policy-compliant reference risk/trade, matching CLAUDE.md's breakeven note


def fixed_risk_quantity(entry: float, stop: float, *, equity: float, risk_pct: float) -> int:
    """A simple, disclosed sizing approximation for accuracy determination only - not
    the portfolio-constrained replay a later, separate stage could add if this result
    is promising enough to warrant it.
    """

    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    return max(0, int((equity * risk_pct) / risk_per_share))


def _load_v1_manifest(output: Path, dataset_id: str) -> tuple[dict, AemContract]:
    folder = output / "aem_staged/datasets" / dataset_id
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("id") != dataset_id:
        raise ValueError("staged dataset id and manifest differ")
    contract = AemContract(**manifest["contract"])
    if contract.sha256 != manifest["contract_sha256"]:
        raise ValueError("dataset_contract_mismatch")
    return manifest, contract


def _load_universe_audit(output: Path, audit_id: str | None) -> tuple[str, dict, pd.DataFrame]:
    if audit_id is None:
        audit_id = json.loads((output / "aem_v2/universe_audit/latest.json").read_text())["id"]
    folder = output / "aem_v2/universe_audit/runs" / audit_id
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if report["id"] != audit_id:
        raise ValueError("universe audit pointer and report differ")
    included = pd.read_csv(folder / "included_pairs.csv", dtype={"scrip_code": str, "session": str})
    return audit_id, report, included


def _session_slices(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    idx = pd.DatetimeIndex(frame.index)
    dates = idx.tz_convert(IST).date if idx.tz is not None else idx.date
    result: dict[str, pd.DataFrame] = {}
    for session_date in sorted(set(dates)):
        result[str(session_date)] = frame.loc[dates == session_date]
    return result


def load_real_session_sources(
    root: Path = ROOT, output: Path = OUTPUT, *, audit_id: str | None = None
) -> dict[str, Any]:
    """Load the real, frozen inputs shared by every consumer of the universe-audited
    development population: the Milestone 2 universe audit, the Milestone 1 staged
    dataset it audited, and the real minute/daily bars themselves, sliced per session.

    Shared by ``build_development_dataset`` (Stage 1) and
    ``aem_v2_stress_gates.run_stress_gates`` (the mandatory execution-stress replay),
    so both read exactly the same real data through one verified path.
    """

    root, output = Path(root).resolve(), Path(output).resolve()
    audit_id, audit_report, included = _load_universe_audit(output, audit_id)
    dataset_id = audit_report["source_dataset_id"]
    manifest, contract = _load_v1_manifest(output, dataset_id)
    if contract.sha256 != audit_report["contract_sha256"]:
        raise ValueError("contract_changed_since_universe_audit")

    settings = load_config(root)
    market = nse_market(settings)
    daily, minute, symbols, _calendar, _evaluation, source = read_staged_aem_source(
        root,
        output,
        plan_id=manifest["plan_id"],
        contract=contract,
        benchmark_symbol=settings.universe.benchmark,
    )
    if source["sha256"] != audit_report["source_sha256"]:
        raise ValueError("dataset_source_changed_since_universe_audit")

    included_sessions: dict[str, set[str]] = {}
    for _, row in included.iterrows():
        included_sessions.setdefault(row["scrip_code"], set()).add(row["session"])

    per_code_sessions = {code: _session_slices(frame) for code, frame in minute.items()}
    per_code_daily_dates = {}
    for code, frame in daily.items():
        idx = pd.DatetimeIndex(frame.index)
        per_code_daily_dates[code] = idx.tz_convert(IST).date if idx.tz is not None else idx.date

    return {
        "audit_id": audit_id,
        "dataset_id": dataset_id,
        "contract": contract,
        "settings": settings,
        "market": market,
        "daily": daily,
        "symbols": symbols,
        "included_sessions": included_sessions,
        "per_code_sessions": per_code_sessions,
        "per_code_daily_dates": per_code_daily_dates,
        "source_sha256": source["sha256"],
    }


def build_development_dataset(
    root: Path = ROOT, output: Path = OUTPUT, *, audit_id: str | None = None
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    """Returns (labeled_rows, excluded_events, run_metadata). Writes nothing itself -
    ``freeze_development_dataset`` below does the freezing, once the caller has seen
    the result.
    """

    root, output = Path(root).resolve(), Path(output).resolve()
    sources = load_real_session_sources(root, output, audit_id=audit_id)
    audit_id = sources["audit_id"]
    dataset_id = sources["dataset_id"]
    contract = sources["contract"]
    settings = sources["settings"]
    market = sources["market"]
    daily = sources["daily"]
    symbols = sources["symbols"]
    included_sessions = sources["included_sessions"]
    per_code_sessions = sources["per_code_sessions"]
    per_code_daily_dates = sources["per_code_daily_dates"]

    equity = float(settings.risk.trading_capital)
    costs = market.costs
    geometries = DEFAULT_AEM_V2_CONTRACT.geometries
    smallest_geometry = min(geometries, key=lambda g: g.target_pct)

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    opportunities_found = 0

    for code in sorted(included_sessions):
        sessions_for_code = sorted(included_sessions[code])
        daily_dates = per_code_daily_dates[code]
        for session_str in sessions_for_code:
            session_date = date.fromisoformat(session_str)
            session_frame = per_code_sessions.get(code, {}).get(session_str)
            if session_frame is None or session_frame.empty:
                excluded.append(
                    {"scrip_code": code, "session": session_str, "reason": "no_minute_bars"}
                )
                continue
            try:
                opportunities = reconstruct_session_opportunities(
                    session_frame, scrip_code=code, symbol=symbols[code]
                )
            except ValueError as exc:
                excluded.append(
                    {
                        "scrip_code": code,
                        "session": session_str,
                        "reason": f"opportunity_reconstruction_failed:{exc}",
                    }
                )
                continue
            if not opportunities:
                continue

            daily_history = daily[code].loc[daily_dates < session_date]
            history_sessions = [s for s in sessions_for_code if s < session_str]
            history_frames = [
                per_code_sessions[code][s] for s in history_sessions if s in per_code_sessions[code]
            ]
            intraday_history = pd.concat(history_frames) if history_frames else pd.DataFrame()
            peer_codes = [
                other
                for other in included_sessions
                if other != code and session_str in included_sessions[other]
            ]
            universe_frames = {
                other: per_code_sessions[other][session_str]
                for other in peer_codes
                if session_str in per_code_sessions.get(other, {})
            }

            for opportunity in opportunities:
                opportunities_found += 1
                stop_for_features = opportunity.intended_entry * (1 - smallest_geometry.stop_pct)
                qty_for_features = fixed_risk_quantity(
                    opportunity.intended_entry, stop_for_features, equity=equity, risk_pct=RISK_PCT
                )
                if qty_for_features <= 0:
                    excluded.append(
                        {
                            "scrip_code": code,
                            "session": session_str,
                            "opportunity_id": opportunity.identifier,
                            "reason": "zero_quantity_for_features",
                        }
                    )
                    continue
                try:
                    feature_result = compute_features(
                        opportunity,
                        session_frame,
                        daily_frame=daily_history,
                        universe_frames=universe_frames,
                        intraday_history=intraday_history,
                        costs=costs,
                        quantity=qty_for_features,
                    )
                except ValueError as exc:
                    excluded.append(
                        {
                            "scrip_code": code,
                            "session": session_str,
                            "opportunity_id": opportunity.identifier,
                            "reason": f"feature_computation_failed:{exc}",
                        }
                    )
                    continue

                for geometry in geometries:
                    stop = opportunity.intended_entry * (1 - geometry.stop_pct)
                    qty = fixed_risk_quantity(
                        opportunity.intended_entry, stop, equity=equity, risk_pct=RISK_PCT
                    )
                    if qty <= 0:
                        excluded.append(
                            {
                                "scrip_code": code,
                                "session": session_str,
                                "opportunity_id": opportunity.identifier,
                                "geometry_id": geometry.id,
                                "reason": "zero_quantity_for_execution",
                            }
                        )
                        continue
                    outcome = resolve_opportunity(
                        opportunity, session_frame, geometry, quantity=qty, costs=costs
                    )
                    if outcome["status"] != "resolved":
                        excluded.append(
                            {
                                "scrip_code": code,
                                "session": session_str,
                                "opportunity_id": opportunity.identifier,
                                "geometry_id": geometry.id,
                                "reason": f"{outcome['status']}:{outcome['outcome']}",
                            }
                        )
                        continue
                    rows.append(
                        {
                            **feature_result["values"],
                            "label": int(outcome["strict_success"]),
                            "net_r": outcome["net_r"],
                            "gross_r": outcome["gross_r"],
                            "session": session_str,
                            "scrip_code": code,
                            "mode": opportunity.mode,
                            "geometry_id": geometry.id,
                            "opportunity_id": opportunity.identifier,
                            "decision_at": opportunity.decision_at,
                        }
                    )

    frame = pd.DataFrame(rows)
    metadata = {
        "source_dataset_id": dataset_id,
        "universe_audit_id": audit_id,
        "contract_sha256": contract.sha256,
        "source_sha256": sources["source_sha256"],
        "included_code_sessions": sum(len(v) for v in included_sessions.values()),
        "opportunities_found": opportunities_found,
        "resolved_rows": int(len(frame)),
        "excluded_events": len(excluded),
    }
    return frame, excluded, metadata


def freeze_development_dataset(
    root: Path = ROOT, output: Path = OUTPUT, *, audit_id: str | None = None
) -> dict[str, Any]:
    root, output = Path(root).resolve(), Path(output).resolve()
    frame, excluded, metadata = build_development_dataset(root, output, audit_id=audit_id)
    run_id = uuid4().hex
    target = output / "aem_v2/development_dataset/runs" / run_id
    target.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target / "events.csv", index=False)
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "aem-v2-milestone-4-development-dataset-v1",
        "status": "development_dataset_frozen_not_evaluated",
        "milestone": 4,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": False,
        **metadata,
        "label_rate": float(frame["label"].mean()) if len(frame) else None,
        "mode_counts": frame["mode"].value_counts().to_dict() if len(frame) else {},
        "geometry_counts": frame["geometry_id"].value_counts().to_dict() if len(frame) else {},
        "exclusion_reason_counts": pd.Series(
            [e["reason"].split(":")[0] for e in excluded]
        ).value_counts().to_dict()
        if excluded
        else {},
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "register_candidate": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "This freezes the labeled development dataset only. No selector has "
                "been fit, no walk-forward evaluation has run, and no accuracy claim "
                "exists yet."
            ),
        },
    }
    write_json(target / "report.json", report)
    write_json(target / "excluded_events.json", excluded)
    write_json(
        output / "aem_v2/development_dataset/latest.json",
        {"id": run_id, "path": str(target / "report.json")},
    )
    return report
