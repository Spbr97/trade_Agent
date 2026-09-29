"""Assemble the real OSR development population without fitting a selector.

This joins the frozen OSR event engine, 30-feature layer, and conservative
outcome resolver over the already-audited 50-stock AEM source. The resulting
rows are consumed historical development evidence, never a new baseline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from tradedesk_lab.aem_v2_development_experiment import (
    RISK_PCT,
    fixed_risk_quantity,
    load_real_session_sources,
)
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT
from tradedesk_lab.osr_events import reconstruct_session_opportunities, resolve_opportunity
from tradedesk_lab.osr_features import compute_features


def _empty_dataset() -> pd.DataFrame:
    columns = [feature.name for feature in DEFAULT_OSR_CONTRACT.features]
    columns.extend(
        [
            "label",
            "net_r",
            "gross_r",
            "session",
            "scrip_code",
            "mode",
            "geometry_id",
            "opportunity_id",
            "decision_at",
        ]
    )
    return pd.DataFrame(columns=columns)


def build_development_dataset(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    audit_id: str | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    """Return labeled OSR rows, explicit exclusions, and source metadata."""

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
    geometries = DEFAULT_OSR_CONTRACT.geometries
    smallest_geometry = min(geometries, key=lambda geometry: geometry.stop_pct)

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    opportunities_found = 0

    for code in sorted(included_sessions):
        sessions_for_code = sorted(included_sessions[code])
        daily_dates = per_code_daily_dates[code]
        all_history_sessions = sorted(per_code_sessions.get(code, {}))
        for session_str in sessions_for_code:
            session_date = date.fromisoformat(session_str)
            session_frame = per_code_sessions.get(code, {}).get(session_str)
            if session_frame is None or session_frame.empty:
                excluded.append(
                    {"scrip_code": code, "session": session_str, "reason": "no_minute_bars"}
                )
                continue
            daily_history = daily[code].loc[daily_dates < session_date]
            if daily_history.empty:
                excluded.append(
                    {
                        "scrip_code": code,
                        "session": session_str,
                        "reason": "missing_prior_daily_context",
                    }
                )
                continue
            prior = daily_history.iloc[-1]
            try:
                opportunities = reconstruct_session_opportunities(
                    session_frame,
                    prior_close=float(prior.close),
                    prior_low=float(prior.low),
                    scrip_code=code,
                    symbol=symbols[code],
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

            earlier_sessions = [
                session for session in all_history_sessions if session < session_str
            ]
            history_frames = [per_code_sessions[code][session] for session in earlier_sessions]
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
                stop_for_features = opportunity.intended_entry * (
                    1 - smallest_geometry.stop_pct
                )
                qty_for_features = fixed_risk_quantity(
                    opportunity.intended_entry,
                    stop_for_features,
                    equity=equity,
                    risk_pct=RISK_PCT,
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
                        opportunity.intended_entry,
                        stop,
                        equity=equity,
                        risk_pct=RISK_PCT,
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
                        opportunity,
                        session_frame,
                        geometry,
                        quantity=qty,
                        costs=costs,
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

    frame = pd.DataFrame(rows) if rows else _empty_dataset()
    metadata = {
        "source_dataset_id": dataset_id,
        "source_universe_audit_id": audit_id,
        "source_contract_sha256": contract.sha256,
        "source_sha256": sources["source_sha256"],
        "osr_contract_sha256": DEFAULT_OSR_CONTRACT.sha256,
        "included_code_sessions": sum(len(sessions) for sessions in included_sessions.values()),
        "opportunities_found": opportunities_found,
        "resolved_rows": int(len(frame)),
        "excluded_events": len(excluded),
        "evidence_class": "consumed_historical_development",
    }
    return frame, excluded, metadata


def freeze_development_dataset(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    audit_id: str | None = None,
) -> dict[str, Any]:
    """Freeze OSR development rows without making a selector claim."""

    root, output = Path(root).resolve(), Path(output).resolve()
    frame, excluded, metadata = build_development_dataset(root, output, audit_id=audit_id)
    run_id = uuid4().hex
    target = output / "osr/development_dataset/runs" / run_id
    target.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target / "events.csv", index=False)
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "osr-milestone-2-development-dataset-v1",
        "status": "development_dataset_frozen_not_evaluated",
        "milestone": 2,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": False,
        **metadata,
        "label_rate": float(frame["label"].mean()) if len(frame) else None,
        "mode_counts": frame["mode"].value_counts().to_dict() if len(frame) else {},
        "geometry_counts": (
            frame["geometry_id"].value_counts().to_dict() if len(frame) else {}
        ),
        "exclusion_reason_counts": (
            pd.Series([row["reason"].split(":")[0] for row in excluded])
            .value_counts()
            .to_dict()
            if excluded
            else {}
        ),
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "register_candidate": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "This freezes consumed historical development rows only. No selector, "
                "walk-forward evaluation, stress replay, or independent validation has run."
            ),
        },
    }
    write_json(target / "report.json", report)
    write_json(target / "excluded_events.json", excluded)
    write_json(
        output / "osr/development_dataset/latest.json",
        {"id": run_id, "path": str(target / "report.json")},
    )
    return report
