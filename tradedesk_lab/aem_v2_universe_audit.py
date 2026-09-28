"""Milestone 2 (second half): the AEM v2 universe integrity audit and the frozen
development dataset it produces.

This reuses the exact same frozen 50-stock staged source AEM v1 already reads
(``aem_staged_data.read_staged_aem_source``) rather than re-collecting or
re-deriving anything - the physical M1/daily data, the collection plan, and the
completeness coverage it already computed are all trusted as-is. This module only adds
the AEM-v2-specific eligibility decision on top: for each (symbol, session) pair in the
frozen 120-session evaluation window, is there a documented reason it cannot enter the
AEM v2 development dataset.

Interpretation, disclosed: the plan's Milestone 2 checklist item is "audit missingness,
corporate actions, liquidity, tradability and source versions... freeze the development
dataset and preserve every excluded event with a reason." All five of those concerns are
session/symbol-level data-hygiene questions, not opportunity-level questions, so
"excluded event" here means an excluded (symbol, session) pair, not an excluded
candidate opportunity. Deciding which individual opportunities exist and whether each
one resolves is Milestone 4's job (the bounded development experiment), run only over
the (symbol, session) pairs this audit includes.

Checks:

- **Missingness / tradability**: reuses ``source["coverage"][code]`` - a session already
  flagged as incomplete by the staging layer's own regular-session check is excluded.
- **Corporate actions**: reuses ``data/corporate_actions.py::detect_unadjusted`` (the
  same tested heuristic ``candle_store.py`` already uses) directly on each stock's own
  daily frame; any evaluation-window session flagged as a suspected unadjusted
  split/bonus is excluded.
- **Liquidity**: the trailing 20-session median daily turnover strictly before the
  session must clear a floor - the same constant
  ``aem_v2_precision_ladder.MIN_MEDIAN_TURNOVER_INR`` already uses for the hard veto
  layer, so the audit and the selector never disagree about what counts as liquid.
- **Source versions**: the source and contract fingerprints this audit ran against are
  recorded in the frozen report so a later re-run can prove it used the same data.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from tradedesk.config import load_config
from tradedesk.data.corporate_actions import detect_unadjusted
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_staged_data import read_staged_aem_source
from tradedesk_lab.aem_v2_precision_ladder import MIN_MEDIAN_TURNOVER_INR
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

IST = "Asia/Kolkata"
MINIMUM_DAILY_HISTORY_SESSIONS = 60
MEDIAN_TURNOVER_LOOKBACK_SESSIONS = 20


@dataclass(frozen=True)
class SessionAuditResult:
    scrip_code: str
    session: str
    included: bool
    reasons: tuple[str, ...]


def _load_v1_manifest(output: Path, dataset_id: str | None) -> tuple[str, Path, dict, AemContract]:
    if dataset_id is None:
        dataset_id = json.loads((output / "aem_staged/latest.json").read_text())["id"]
    folder = output / "aem_staged/datasets" / dataset_id
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("id") != dataset_id:
        raise ValueError("staged latest pointer and manifest differ")
    contract = AemContract(**manifest["contract"])
    if contract.sha256 != manifest["contract_sha256"]:
        raise ValueError("dataset_contract_mismatch")
    return dataset_id, folder, manifest, contract


def _audit_one_code(
    code: str, frame: pd.DataFrame, *, evaluation: list, missing_sessions: set[str]
) -> tuple[list[SessionAuditResult], list[dict[str, Any]]]:
    idx = pd.DatetimeIndex(frame.index)
    dates = idx.tz_convert(IST).date if idx.tz is not None else idx.date
    turnover = (frame["close"] * frame["volume"]).to_numpy()
    hits = detect_unadjusted(frame)
    evaluation_dates = {session for session in evaluation}
    corporate_action_hits = [
        {"date": str(hit_date), "observed_factor": factor, "label": label}
        for hit_date, factor, label in hits
        if hit_date in evaluation_dates
    ]
    suspect_dates = {str(hit["date"]) for hit in corporate_action_hits}

    results: list[SessionAuditResult] = []
    for session in evaluation:
        session_str = str(session)
        reasons: list[str] = []
        if session_str in missing_sessions:
            reasons.append("incomplete_m1_session")
        if session_str in suspect_dates:
            reasons.append("suspected_unadjusted_corporate_action")
        prior_mask = dates < session
        prior_count = int(prior_mask.sum())
        if prior_count < MINIMUM_DAILY_HISTORY_SESSIONS:
            reasons.append("insufficient_daily_warmup")
        elif prior_count < MEDIAN_TURNOVER_LOOKBACK_SESSIONS:
            reasons.append("insufficient_daily_turnover_history")
        else:
            recent_turnover = turnover[prior_mask][-MEDIAN_TURNOVER_LOOKBACK_SESSIONS:]
            if float(pd.Series(recent_turnover).median()) < MIN_MEDIAN_TURNOVER_INR:
                reasons.append("inadequate_liquidity")
        results.append(
            SessionAuditResult(
                scrip_code=code, session=session_str, included=not reasons, reasons=tuple(reasons)
            )
        )
    return results, corporate_action_hits


def run_universe_audit(
    root: Path = ROOT, output: Path = OUTPUT, *, dataset_id: str | None = None
) -> dict[str, Any]:
    root, output = Path(root).resolve(), Path(output).resolve()
    dataset_id, _folder, manifest, contract = _load_v1_manifest(output, dataset_id)
    settings = load_config(root)
    daily, _minute, _symbols, _calendar, evaluation, source = read_staged_aem_source(
        root,
        output,
        plan_id=manifest["plan_id"],
        contract=contract,
        benchmark_symbol=settings.universe.benchmark,
    )
    if source["sha256"] != manifest["source"]["sha256"]:
        raise ValueError("dataset_source_changed_rebuild_before_comparison")

    all_results: list[SessionAuditResult] = []
    per_code_summary: dict[str, Any] = {}
    for code in sorted(daily):
        coverage = source["coverage"].get(code, {})
        missing = set(coverage.get("missing_or_incomplete_sessions", []))
        results, corporate_action_hits = _audit_one_code(
            code, daily[code], evaluation=evaluation, missing_sessions=missing
        )
        all_results.extend(results)
        included = sum(1 for r in results if r.included)
        per_code_summary[code] = {
            "total_sessions": len(results),
            "included_sessions": included,
            "excluded_sessions": len(results) - included,
            "corporate_action_hits": corporate_action_hits,
        }

    included_results = [r for r in all_results if r.included]
    excluded_results = [r for r in all_results if not r.included]
    reason_counts = Counter(reason for r in excluded_results for reason in r.reasons)

    run_id = uuid4().hex
    target = output / "aem_v2/universe_audit/runs" / run_id
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "aem-v2-milestone-2-universe-audit-v1",
        "status": "universe_audit_complete",
        "milestone": 2,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": False,
        "source_dataset_id": dataset_id,
        "source_sha256": source["sha256"],
        "contract_sha256": contract.sha256,
        "plan_id": manifest["plan_id"],
        "audit_parameters": {
            "minimum_daily_history_sessions": MINIMUM_DAILY_HISTORY_SESSIONS,
            "median_turnover_lookback_sessions": MEDIAN_TURNOVER_LOOKBACK_SESSIONS,
            "minimum_median_turnover_inr": MIN_MEDIAN_TURNOVER_INR,
        },
        "universe_symbols": len(daily),
        "evaluation_sessions": len(evaluation),
        "total_code_sessions": len(all_results),
        "included_code_sessions": len(included_results),
        "excluded_code_sessions": len(excluded_results),
        "active_session_coverage": (
            len(included_results) / len(all_results) if all_results else 0.0
        ),
        "exclusion_reason_counts": dict(reason_counts),
        "per_code_summary": per_code_summary,
        "excluded_events": [
            {"scrip_code": r.scrip_code, "session": r.session, "reasons": list(r.reasons)}
            for r in excluded_results
        ],
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "register_candidate": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "reason": (
                "This audit only decides which (symbol, session) pairs are eligible for "
                "Milestone 4's development experiment. It computes no opportunity, "
                "feature, prediction or outcome, and makes no accuracy claim."
            ),
        },
    }
    write_json(target / "report.json", report)
    included_frame = pd.DataFrame(
        [{"scrip_code": r.scrip_code, "session": r.session} for r in included_results]
    )
    included_frame.to_csv(target / "included_pairs.csv", index=False)
    write_json(
        output / "aem_v2/universe_audit/latest.json",
        {"id": run_id, "path": str(target / "report.json"), "source_dataset_id": dataset_id},
    )
    return report
