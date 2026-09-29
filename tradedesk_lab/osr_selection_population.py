"""Build the leakage-safe OSR call population used by Milestone 3 selectors.

Milestone 2's ``events.csv`` intentionally contains resolved fills only and keeps
unfilled/unresolved geometry outcomes in ``excluded_events.json``.  A selector may
not train or rank on that resolved-only table: whether a later order fills is future
information at decision time.  This module restores those later execution outcomes
to the call population while keeping them strictly outside the 30-feature matrix.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from tradedesk_lab.aem_v2_development_experiment import (
    RISK_PCT,
    fixed_risk_quantity,
    load_real_session_sources,
)
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT
from tradedesk_lab.osr_events import reconstruct_session_opportunities
from tradedesk_lab.osr_features import compute_features

FEATURE_NAMES = tuple(feature.name for feature in DEFAULT_OSR_CONTRACT.features)
IDENTITY_COLUMNS = ("opportunity_id", "geometry_id")
OUTCOME_STATUSES = ("resolved", "unfilled", "unresolved")


def _load_development_artifact(
    output: Path, dataset_run_id: str | None
) -> tuple[str, Path, dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    if dataset_run_id is None:
        pointer = json.loads(
            (output / "osr/development_dataset/latest.json").read_text(encoding="utf-8")
        )
        dataset_run_id = str(pointer["id"])
    folder = output / "osr/development_dataset/runs" / dataset_run_id
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if report.get("id") != dataset_run_id:
        raise ValueError("OSR development dataset pointer and report differ")
    if report.get("osr_contract_sha256") != DEFAULT_OSR_CONTRACT.sha256:
        raise ValueError("OSR development dataset contract differs from the frozen contract")
    if report.get("evidence_class") != "consumed_historical_development":
        raise ValueError("OSR development dataset has an unexpected evidence class")
    events = pd.read_csv(folder / "events.csv")
    excluded = json.loads((folder / "excluded_events.json").read_text(encoding="utf-8"))
    if len(events) != int(report["resolved_rows"]):
        raise ValueError("OSR development report and resolved event rows differ")
    return dataset_run_id, folder, report, events, excluded


def _outcome_parts(reason: str) -> tuple[str, str]:
    status, separator, detail = str(reason).partition(":")
    if not separator or status not in {"unfilled", "unresolved"} or not detail:
        raise ValueError(f"unsupported OSR exclusion reason for selection: {reason}")
    return status, detail


def _selection_row(
    feature_values: dict[str, Any],
    *,
    label: int,
    net_r: float | None,
    gross_r: float | None,
    session: str,
    scrip_code: str,
    mode: str,
    geometry_id: str,
    opportunity_id: str,
    decision_at: Any,
    outcome_status: str,
    outcome_reason: str,
) -> dict[str, Any]:
    if set(feature_values) != set(FEATURE_NAMES):
        raise ValueError("selection feature values must equal the frozen OSR feature registry")
    if outcome_status not in OUTCOME_STATUSES:
        raise ValueError("selection outcome status is not registered")
    return {
        **{name: float(feature_values[name]) for name in FEATURE_NAMES},
        "label": int(label),
        "net_r": net_r,
        "gross_r": gross_r,
        "outcome_status": outcome_status,
        "filled": outcome_status == "resolved",
        "outcome_reason": outcome_reason,
        "session": str(session),
        "scrip_code": str(scrip_code),
        "mode": str(mode),
        "geometry_id": str(geometry_id),
        "opportunity_id": str(opportunity_id),
        "decision_at": str(decision_at),
    }


def _resolved_rows(events: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    required = {
        *FEATURE_NAMES,
        "label",
        "net_r",
        "gross_r",
        "session",
        "scrip_code",
        "mode",
        "geometry_id",
        "opportunity_id",
        "decision_at",
    }
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"OSR resolved events are missing columns: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    templates: dict[str, dict[str, Any]] = {}
    for record in events.to_dict(orient="records"):
        feature_values = {name: record[name] for name in FEATURE_NAMES}
        opportunity_id = str(record["opportunity_id"])
        templates.setdefault(
            opportunity_id,
            {
                "features": feature_values,
                "session": record["session"],
                "scrip_code": record["scrip_code"],
                "mode": record["mode"],
                "decision_at": record["decision_at"],
            },
        )
        rows.append(
            _selection_row(
                feature_values,
                label=int(record["label"]),
                net_r=float(record["net_r"]),
                gross_r=float(record["gross_r"]),
                session=record["session"],
                scrip_code=record["scrip_code"],
                mode=record["mode"],
                geometry_id=record["geometry_id"],
                opportunity_id=opportunity_id,
                decision_at=record["decision_at"],
                outcome_status="resolved",
                outcome_reason="strict_success" if int(record["label"]) else "strict_failure",
            )
        )
    return rows, templates


def _recover_missing_features(
    root: Path,
    output: Path,
    report: dict[str, Any],
    excluded: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    needed_ids = {str(row["opportunity_id"]) for row in excluded}
    if not needed_ids:
        return {}
    sources = load_real_session_sources(
        root,
        output,
        audit_id=str(report["source_universe_audit_id"]),
    )
    if sources["dataset_id"] != report["source_dataset_id"]:
        raise ValueError("OSR recovery source dataset differs from the frozen development run")

    settings = sources["settings"]
    market = sources["market"]
    daily = sources["daily"]
    symbols = sources["symbols"]
    included_sessions = sources["included_sessions"]
    per_code_sessions = sources["per_code_sessions"]
    per_code_daily_dates = sources["per_code_daily_dates"]
    equity = float(settings.risk.trading_capital)
    smallest_geometry = min(DEFAULT_OSR_CONTRACT.geometries, key=lambda item: item.stop_pct)

    by_code_session: dict[tuple[str, str], set[str]] = {}
    for row in excluded:
        key = (str(row["scrip_code"]), str(row["session"]))
        by_code_session.setdefault(key, set()).add(str(row["opportunity_id"]))

    recovered: dict[str, dict[str, Any]] = {}
    for (code, session_str), group_ids in sorted(by_code_session.items()):
        session_frame = per_code_sessions.get(code, {}).get(session_str)
        if session_frame is None or session_frame.empty:
            raise ValueError(f"missing M1 bars while recovering selection features: {code} {session_str}")
        session_date = date.fromisoformat(session_str)
        daily_dates = per_code_daily_dates[code]
        daily_history = daily[code].loc[daily_dates < session_date]
        if daily_history.empty:
            raise ValueError(f"missing daily context while recovering selection features: {code}")
        prior = daily_history.iloc[-1]
        opportunities = reconstruct_session_opportunities(
            session_frame,
            prior_close=float(prior.close),
            prior_low=float(prior.low),
            scrip_code=code,
            symbol=symbols[code],
        )
        opportunity_map = {item.identifier: item for item in opportunities}
        absent = group_ids.difference(opportunity_map)
        if absent:
            raise ValueError(f"could not reconstruct excluded OSR opportunities: {sorted(absent)}")

        all_history_sessions = sorted(per_code_sessions.get(code, {}))
        earlier_sessions = [item for item in all_history_sessions if item < session_str]
        history_frames = [per_code_sessions[code][item] for item in earlier_sessions]
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

        for opportunity_id in sorted(group_ids):
            opportunity = opportunity_map[opportunity_id]
            stop = opportunity.intended_entry * (1 - smallest_geometry.stop_pct)
            quantity = fixed_risk_quantity(
                opportunity.intended_entry,
                stop,
                equity=equity,
                risk_pct=RISK_PCT,
            )
            if quantity <= 0:
                raise ValueError(f"zero quantity while recovering selection features: {opportunity_id}")
            result = compute_features(
                opportunity,
                session_frame,
                daily_frame=daily_history,
                universe_frames=universe_frames,
                intraday_history=intraday_history,
                costs=market.costs,
                quantity=quantity,
            )
            recovered[opportunity_id] = {
                "features": result["values"],
                "session": session_str,
                "scrip_code": code,
                "mode": opportunity.mode,
                "decision_at": opportunity.decision_at,
            }
    if recovered.keys() != needed_ids:
        raise ValueError("selection feature recovery did not cover every excluded opportunity")
    return recovered


def _validate_population(frame: pd.DataFrame, *, expected_rows: int) -> dict[str, Any]:
    if len(frame) != expected_rows:
        raise ValueError("selection population does not equal resolved rows plus exclusions")
    duplicate_rows = int(frame.duplicated(list(IDENTITY_COLUMNS)).sum())
    if duplicate_rows:
        raise ValueError("selection population contains duplicate opportunity/geometry identities")
    feature_matrix = frame[list(FEATURE_NAMES)].to_numpy(dtype=float)
    if not np.isfinite(feature_matrix).all():
        raise ValueError("selection population contains missing or non-finite feature values")
    if not set(frame["outcome_status"]).issubset(OUTCOME_STATUSES):
        raise ValueError("selection population contains an unregistered outcome status")
    if not set(frame["label"].unique()).issubset({0, 1}):
        raise ValueError("selection population labels must be binary")
    nonresolved = frame["outcome_status"] != "resolved"
    if frame.loc[nonresolved, "label"].ne(0).any():
        raise ValueError("an unfilled or unresolved call cannot be a strict success")
    return {
        "rows": int(len(frame)),
        "duplicate_identity_rows": duplicate_rows,
        "feature_null_or_nonfinite_cells": 0,
        "unique_opportunities": int(frame["opportunity_id"].nunique()),
        "unique_sessions": int(frame["session"].nunique()),
        "unique_scrip_codes": int(frame["scrip_code"].nunique()),
    }


def build_selection_population(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_run_id: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return every OSR decision-time call, including later execution failures."""

    root, output = Path(root).resolve(), Path(output).resolve()
    dataset_run_id, folder, report, events, excluded = _load_development_artifact(
        output, dataset_run_id
    )
    rows, templates = _resolved_rows(events)
    missing_exclusions = [
        item for item in excluded if str(item["opportunity_id"]) not in templates
    ]
    recovered = _recover_missing_features(root, output, report, missing_exclusions)
    templates.update(recovered)

    for exclusion in excluded:
        opportunity_id = str(exclusion["opportunity_id"])
        template = templates.get(opportunity_id)
        if template is None:
            raise ValueError(f"missing decision-time features for excluded call: {opportunity_id}")
        status, reason = _outcome_parts(str(exclusion["reason"]))
        rows.append(
            _selection_row(
                template["features"],
                label=0,
                net_r=None,
                gross_r=None,
                session=template["session"],
                scrip_code=template["scrip_code"],
                mode=template["mode"],
                geometry_id=str(exclusion["geometry_id"]),
                opportunity_id=opportunity_id,
                decision_at=template["decision_at"],
                outcome_status=status,
                outcome_reason=reason,
            )
        )

    frame = pd.DataFrame(rows).sort_values(
        ["session", "decision_at", "scrip_code", "mode", "geometry_id"],
        kind="stable",
    )
    frame = frame.reset_index(drop=True)
    quality = _validate_population(frame, expected_rows=len(events) + len(excluded))
    counts = frame["outcome_status"].value_counts().to_dict()
    metadata = {
        "source_development_dataset_run_id": dataset_run_id,
        "source_events_sha256": digest(folder / "events.csv"),
        "source_exclusions_sha256": digest(folder / "excluded_events.json"),
        "source_dataset_id": report["source_dataset_id"],
        "source_universe_audit_id": report["source_universe_audit_id"],
        "source_osr_contract_sha256": report["osr_contract_sha256"],
        "resolved_calls": int(counts.get("resolved", 0)),
        "unfilled_calls": int(counts.get("unfilled", 0)),
        "unresolved_calls": int(counts.get("unresolved", 0)),
        "strict_successes": int(frame["label"].sum()),
        "strict_success_rate_all_calls": float(frame["label"].mean()),
        "strict_success_rate_resolved": float(
            frame.loc[frame["outcome_status"] == "resolved", "label"].mean()
        ),
        "feature_count": len(FEATURE_NAMES),
        "evidence_class": "consumed_historical_development",
        **quality,
    }
    return frame, metadata


def freeze_selection_population(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_run_id: str | None = None,
) -> dict[str, Any]:
    """Freeze the full call population without fitting or evaluating a selector."""

    root, output = Path(root).resolve(), Path(output).resolve()
    frame, metadata = build_selection_population(root, output, dataset_run_id=dataset_run_id)
    run_id = uuid4().hex
    target = output / "osr/selection_population/runs" / run_id
    target.mkdir(parents=True, exist_ok=True)
    calls_path = target / "calls.csv"
    frame.to_csv(calls_path, index=False)
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "osr-milestone-3-selection-population-v1",
        "status": "selection_population_frozen_not_evaluated",
        "milestone": 3,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": False,
        **metadata,
        "calls_sha256": digest(calls_path),
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "register_candidate": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "This artifact removes resolved-only selection bias by restoring later "
                "unfilled and unresolved calls. It does not fit a selector or report an "
                "accuracy improvement."
            ),
        },
    }
    write_json(target / "report.json", report)
    write_json(
        output / "osr/selection_population/latest.json",
        {"id": run_id, "path": str(target / "report.json")},
    )
    return report
