"""Localhost dashboard (PLAN.md 7): one HTML page, JSON state, server-sent events.
Bind to 127.0.0.1 only (PLAN.md 16)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from tradedesk.dashboard.state import DashboardState

STATIC = Path(__file__).with_name("static")
MODELS_DIR = Path("data/models")
CRYPTO_UNIVERSE_REPORT = Path("data/reports/crypto_universe_latest.json")
ACCURACY_PROSPECTIVE_STATE = Path(
    "data/m14_m18/accuracy_prospective_shadow/state.json"
)
ACCURACY_PROSPECTIVE_MONITOR = Path(
    "data/m14_m18/accuracy_prospective_monitor/latest.json"
)
ACCURACY_PROSPECTIVE_CONTROL = Path(
    "data/m14_m18/accuracy_prospective_control/state.json"
)
ACCURACY_PROSPECTIVE_TIMING = Path(
    "data/m14_m18/accuracy_prospective_timing/state.json"
)
ACCURACY_PROSPECTIVE_QUALIFICATION = Path(
    "data/m14_m18/accuracy_prospective_qualification/latest.json"
)
CRYPTO_ACCURACY_STATE = Path("data/m14_m18/crypto_accuracy_program/state.json")
CRYPTO_ACCURACY_TIMING = Path("data/m14_m18/crypto_accuracy_timing/state.json")
CRYPTO_ACCURACY_DATASET = Path("data/m14_m18/crypto_accuracy_dataset/state.json")
CRYPTO_ACCURACY_MECHANISMS = Path("data/m14_m18/crypto_accuracy_mechanisms/state.json")
CRYPTO_UNIVERSE_STATE = Path(
    "data/m14_m18/crypto_accuracy_dataset/universe_state.json"
)
BSE_ACCURACY_QUICK_PROFIT = Path("docs/evidence/bse-accuracy-quick-profit-b1.json")


def _read_call_log(log_path: Path, limit: int) -> list[dict[str, Any]]:
    """Shared reader for the crypto/BSE JSONL call logs (signal_tracker.py's output) -
    newest-first, capped at `limit`. Reads raw JSON rather than TrackedSignal(**r), so a
    row logged before `source` existed (2026-09-13) has no "source" key at all here - default
    it to "backfill" the same way TrackedSignal's own dataclass default does, so the
    dashboard's Live/Past split sees the identical answer whichever path loaded the row."""
    if not log_path.exists():
        return []
    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for r in rows:
        r.setdefault("source", "backfill")
    rows.sort(key=lambda r: r["logged_at"], reverse=True)
    return rows[:limit]


def create_app(
    state: DashboardState, journal_path: Path | None = None, *, bse_state: DashboardState | None = None  # noqa: E501
) -> FastAPI:
    """`journal_path` is optional and keyword-only-by-convention so every existing caller
    (both CLI commands, and tests/alerts's `create_app(state)`) is unaffected; pass it to
    light up /api/eod and /api/performance, which read the paper book directly rather than
    through DashboardState (that stays purely the in-memory live-session state pushed over
    SSE - EOD/weekly/monthly are persisted history, a different kind of read).

    `bse_state` (added 2026-09-13) gives BSE the same live "today's plan vs what actually
    happened" view NSE has always had via `/api/state`/`/events` - BSE now runs the same
    full-universe live-session shape as NSE (tradedesk-bse-live-session mirrors
    tradedesk-live-session), so the dashboard should not leave it with only the historical
    resolved-calls log while NSE gets a live view too. Optional and additive: omitting it
    makes `/api/state/bse`/`/events/bse` 404, every existing caller unaffected."""
    app = FastAPI(title="tradedesk", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(state.snapshot())

    @app.get("/api/accuracy-policy")
    async def api_accuracy_policy() -> JSONResponse:
        """The live-call evidence gate and detectors still restricted to shadow research.

        This is configuration truth, not a performance estimate. Keeping it in the API
        prevents the dashboard from claiming that an installed research detector is live.
        """
        from tradedesk.config import load_config

        settings = load_config(".")
        gate = settings.setups.eligibility
        research_only = sorted(
            {f"{market}:{name}" for name, setup in settings.setups.setups.items()
             for market in setup.research_only_markets}
        )
        return JSONResponse(
            {
                "min_win_rate": gate.min_win_rate,
                "min_win_rate_wilson_lb": gate.min_win_rate_wilson_lb,
                "min_trades": gate.min_trades,
                "min_oos_trades": gate.min_oos_trades,
                "min_expectancy_r": gate.min_expectancy_r,
                "must_beat_random_by_r": gate.must_beat_random_by_r,
                "research_only": research_only,
            }
        )

    @app.get("/api/crypto/accuracy-program")
    async def api_crypto_accuracy_program() -> JSONResponse:
        """Crypto-only C0+ evidence; never pooled with the NSE accuracy program."""
        if not CRYPTO_ACCURACY_STATE.exists():
            return JSONResponse(
                {
                    "status": "not_run",
                    "checkpoint": "C0",
                    "detail": "crypto accuracy baseline has not been generated",
                }
            )
        try:
            payload = json.loads(CRYPTO_ACCURACY_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "checkpoint": "C0",
                    "detail": "crypto accuracy state is unreadable",
                }
            )
        return JSONResponse(payload)

    @app.get("/api/crypto/accuracy-timing")
    async def api_crypto_accuracy_timing() -> JSONResponse:
        """Forward-only same-coin timing evidence, separated by setup."""
        if not CRYPTO_ACCURACY_TIMING.exists():
            return JSONResponse(
                {
                    "status": "not_activated",
                    "summary": None,
                    "detail": "crypto prospective timing control has not been activated",
                }
            )
        try:
            payload = json.loads(CRYPTO_ACCURACY_TIMING.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "summary": None,
                    "detail": "crypto prospective timing state is unreadable",
                }
            )
        summary = payload.get("summary") or {}
        records = [
            {
                key: record.get(key)
                for key in (
                    "signal_id",
                    "scrip_code",
                    "symbol",
                    "setup",
                    "armed_on",
                    "assigned_at",
                    "prospective_eligible",
                )
            }
            for record in payload.get("records", [])[-50:]
        ]
        return JSONResponse(
            {
                "status": summary.get("status", "invalid"),
                "activation": payload.get("activation"),
                "summary": summary,
                "source_integrity": payload.get("source_integrity"),
                "records": records,
                "errors": payload.get("errors", []),
                "detail": (
                    "forward-only same-coin timing evidence; each setup remains "
                    "independent and live authority is false"
                ),
            }
        )

    @app.get("/api/crypto/accuracy-dataset")
    async def api_crypto_accuracy_dataset() -> JSONResponse:
        """Compact C1 dataset readiness; row-level artifacts stay off the dashboard."""
        dataset: dict[str, Any] | None = None
        universe: dict[str, Any] | None = None
        try:
            if CRYPTO_ACCURACY_DATASET.exists():
                dataset = json.loads(CRYPTO_ACCURACY_DATASET.read_text(encoding="utf-8"))
            if CRYPTO_UNIVERSE_STATE.exists():
                raw_universe = json.loads(CRYPTO_UNIVERSE_STATE.read_text(encoding="utf-8"))
                universe = {
                    key: raw_universe.get(key)
                    for key in (
                        "status",
                        "activated_at",
                        "observations",
                        "current_active_pairs",
                        "current_known_inactive_pairs",
                        "listing_transitions",
                        "removal_transitions",
                        "latest_event_sha256",
                        "membership_before_activation",
                    )
                }
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "detail": "crypto C1 dataset state is unreadable",
                }
            )
        if dataset is None:
            return JSONResponse(
                {
                    "status": "collecting_universe" if universe else "not_activated",
                    "universe": universe,
                    "detail": "exact universe history is collecting; dataset not frozen yet",
                }
            )
        return JSONResponse(
            {
                "status": dataset.get("status", "invalid"),
                "id": dataset.get("id"),
                "latest_closed_session": dataset.get("latest_closed_session"),
                "membership": dataset.get("membership"),
                "coverage_summary": dataset.get("coverage_summary"),
                "source_integrity": dataset.get("source_integrity"),
                "geometry_count": len(
                    (dataset.get("contract") or {}).get("geometries") or []
                ),
                "universe": universe,
                "baseline_improved": dataset.get("baseline_improved", False),
                "eligible_for_live": dataset.get("eligible_for_live", False),
                "next_checkpoint": dataset.get("next_checkpoint"),
                "detail": (
                    "crypto-only closed-candle dataset; pre-activation membership is "
                    "unknown, never inferred, and never shown as a pass"
                ),
            }
        )

    @app.get("/api/crypto/accuracy-mechanisms")
    async def api_crypto_accuracy_mechanisms() -> JSONResponse:
        """Compact C2 mechanism-race evidence; research can never grant live authority."""

        def compact(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
            if not isinstance(value, dict):
                return None
            return {key: value.get(key) for key in keys}

        def unavailable(status: str, detail: str) -> JSONResponse:
            return JSONResponse(
                {
                    "status": status,
                    "version": None,
                    "id": None,
                    "created_at": None,
                    "c1_dataset": None,
                    "c1_readiness": None,
                    "trial_counts": {
                        "registered": 12,
                        "evaluated": None,
                        "passed": None,
                        "rejected": None,
                        "incomplete": None,
                    },
                    "best_trial": None,
                    "mechanisms": [],
                    "source_integrity": {"passed": False, "errors": [detail]},
                    "baseline_improved": False,
                    "eligible_for_live": False,
                    "detail": detail,
                }
            )

        if not CRYPTO_ACCURACY_MECHANISMS.exists():
            return unavailable("not_run", "crypto C2 mechanism race has not run")
        try:
            payload = json.loads(CRYPTO_ACCURACY_MECHANISMS.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("crypto C2 state must be an object")
        except (OSError, ValueError, json.JSONDecodeError):
            return unavailable("invalid", "crypto C2 mechanism state is unreadable")

        mechanism_rows = payload.get("mechanisms")
        mechanisms = []
        if isinstance(mechanism_rows, list):
            mechanisms = [
                row
                for item in mechanism_rows
                if (
                    row := compact(
                        item,
                        (
                            "id",
                            "status",
                            "evaluated_trials",
                            "passing_trials",
                            "stopped",
                        ),
                    )
                )
                is not None
            ]

        source_integrity = compact(payload.get("source_integrity"), ("passed", "errors")) or {
            "passed": False,
            "errors": ["crypto C2 source integrity is unavailable"],
        }
        reported_status = payload.get("status", "invalid")
        if source_integrity["passed"] is not True:
            reported_status = "blocked_invalid_source_integrity"
        return JSONResponse(
            {
                "status": reported_status,
                "version": payload.get("version"),
                "id": payload.get("id"),
                "created_at": payload.get("created_at"),
                "c1_dataset": compact(
                    payload.get("c1_dataset"),
                    ("id", "status", "contract_sha256", "source_sha256"),
                ),
                "c1_readiness": compact(
                    payload.get("c1_readiness"),
                    (
                        "minimum_pair_sessions",
                        "required_pair_sessions",
                        "ready_pairs",
                        "required_pairs",
                        "resolved_labels",
                        "minimum_resolved_labels",
                        "resolved_sessions",
                        "minimum_active_sessions",
                    ),
                ),
                "trial_counts": compact(
                    payload.get("trial_counts"),
                    ("registered", "evaluated", "passed", "rejected", "incomplete"),
                ),
                "best_trial": compact(
                    payload.get("best_trial"),
                    (
                        "mechanism",
                        "geometry",
                        "status",
                        "resolved_calls",
                        "active_sessions",
                        "observed_accuracy",
                        "wilson95_lower",
                        "mean_net_r",
                        "minimum_control_advantage_r",
                    ),
                ),
                "mechanisms": mechanisms,
                "source_integrity": source_integrity,
                # C2 is consumed development evidence. Optimistic artifact fields cannot
                # turn this read-only surface into a baseline or live-eligibility claim.
                "baseline_improved": False,
                "eligible_for_live": False,
                "detail": payload.get(
                    "detail",
                    "crypto-only C2 mechanism evidence; unavailable is never a pass",
                ),
            }
        )

    @app.get("/api/accuracy-prospective-qualification")
    async def api_accuracy_prospective_qualification() -> JSONResponse:
        """Compact atomic NSE qualification; review authority is not live authority."""

        def unavailable(status: str, detail: str) -> JSONResponse:
            return JSONResponse(
                {
                    "status": status,
                    "created_at": None,
                    "components": [],
                    "parity": {
                        "identity_passed": False,
                        "evaluation_ready": False,
                        "evaluation_passed": False,
                        "failures": [detail],
                    },
                    "gate_checks": {},
                    "canonical_baseline": None,
                    "locked_historical_challenger": None,
                    "review_authorized": False,
                    "baseline_improved": False,
                    "eligible_for_live": False,
                    "detail": detail,
                }
            )

        if not ACCURACY_PROSPECTIVE_QUALIFICATION.exists():
            return unavailable(
                "not_run", "NSE prospective qualification has not run"
            )
        try:
            payload = json.loads(
                ACCURACY_PROSPECTIVE_QUALIFICATION.read_text(encoding="utf-8")
            )
            if not isinstance(payload, dict):
                raise ValueError("NSE qualification state must be an object")
        except (OSError, ValueError, json.JSONDecodeError):
            return unavailable(
                "invalid", "NSE prospective qualification state is unreadable"
            )

        metric_names = (
            "resolved_calls",
            "wins",
            "accuracy",
            "wilson_lower_bound",
            "active_sessions",
            "session_target_rate",
            "expectancy_r",
            "stressed_resolved_calls",
            "stressed_accuracy",
            "stressed_expectancy_r",
            "session_cluster_lower_95",
            "week_cluster_lower_95",
            "model_accuracy",
            "model_expectancy_r",
            "control_advantage_r",
            "p_value",
        )
        components = []
        raw_components = payload.get("components")
        if isinstance(raw_components, dict):
            for identifier, raw in raw_components.items():
                if not isinstance(raw, dict):
                    continue
                raw_metrics = raw.get("metrics")
                metrics = (
                    {
                        key: raw_metrics.get(key)
                        for key in metric_names
                        if key in raw_metrics
                    }
                    if isinstance(raw_metrics, dict)
                    else {}
                )
                components.append(
                    {
                        "id": identifier,
                        "available": raw.get("available") is True,
                        "ready": raw.get("ready") is True,
                        "passed": raw.get("passed") is True,
                        "status": raw.get("status", "not_available"),
                        "metrics": metrics,
                    }
                )

        raw_parity = payload.get("parity")
        parity = raw_parity if isinstance(raw_parity, dict) else {}
        compact_parity = {
            "identity_passed": parity.get("identity_passed") is True,
            "evaluation_ready": parity.get("evaluation_ready") is True,
            "evaluation_passed": parity.get("evaluation_passed") is True,
            "failures": parity.get("failures")
            if isinstance(parity.get("failures"), list)
            else [],
        }
        gate_names = (
            "all_components_available",
            "all_components_ready",
            "m8_accuracy_passed",
            "m9_integrity_stress_passed",
            "m10_selection_control_passed",
            "m11_timing_control_passed",
            "candidate_and_evaluation_parity",
        )
        raw_gates = payload.get("gate_checks")
        gate_checks = (
            {name: raw_gates.get(name) is True for name in gate_names}
            if isinstance(raw_gates, dict)
            else {}
        )

        baseline_names = (
            "contract",
            "strict_wins",
            "resolved_fills",
            "strict_success_rate",
            "wilson_lower_bound",
            "mean_net_r",
            "status",
        )
        challenger_names = (*baseline_names[:-1], "evidence_class")
        raw_baseline = payload.get("canonical_baseline")
        raw_challenger = payload.get("locked_historical_challenger")
        canonical_baseline = (
            {name: raw_baseline.get(name) for name in baseline_names}
            if isinstance(raw_baseline, dict)
            else None
        )
        locked_challenger = (
            {name: raw_challenger.get(name) for name in challenger_names}
            if isinstance(raw_challenger, dict)
            else None
        )
        expected_component_ids = {
            "m8_accuracy",
            "m9_integrity_stress",
            "m10_selection_control",
            "m11_timing_control",
        }
        review_authorized = bool(
            payload.get("status") == "human_review_authorized"
            and payload.get("review_authorized") is True
            and len(components) == 4
            and {row["id"] for row in components} == expected_component_ids
            and all(row["available"] and row["ready"] and row["passed"] for row in components)
            and compact_parity["identity_passed"]
            and compact_parity["evaluation_ready"]
            and compact_parity["evaluation_passed"]
            and gate_checks
            and all(gate_checks.values())
        )
        reported_status = payload.get("status", "invalid")
        allowed_statuses = {
            "not_available",
            "collecting_insufficient_evidence",
            "degraded",
            "prospective_rejected",
            "human_review_authorized",
        }
        if reported_status not in allowed_statuses:
            reported_status = "invalid"
        elif reported_status == "human_review_authorized" and not review_authorized:
            reported_status = "degraded"
        return JSONResponse(
            {
                "status": reported_status,
                "created_at": payload.get("created_at"),
                "components": components,
                "parity": compact_parity,
                "gate_checks": gate_checks,
                "canonical_baseline": canonical_baseline,
                "locked_historical_challenger": locked_challenger,
                "review_authorized": review_authorized,
                "baseline_improved": False,
                "eligible_for_live": False,
                "detail": payload.get(
                    "detail",
                    "NSE prospective qualification is read-only; unavailable is never a pass",
                ),
            }
        )

    @app.get("/api/bse/accuracy-quick-profit")
    async def api_bse_accuracy_quick_profit() -> JSONResponse:
        """Compact BSE B1 evidence; development transport stays visibly separate."""

        def unavailable(status: str, detail: str) -> JSONResponse:
            return JSONResponse(
                {
                    "status": status,
                    "created_at": None,
                    "readiness": None,
                    "geometry": None,
                    "prospective": {
                        "source_sessions": None,
                        "rules_sample_ready": None,
                        "best_trial": None,
                    },
                    "development_transport": None,
                    "baseline_improved": False,
                    "live": False,
                    "promotion_allowed": False,
                    "detail": detail,
                }
            )

        if not BSE_ACCURACY_QUICK_PROFIT.exists():
            return unavailable("not_run", "BSE B1 quick-profit evidence has not run")
        try:
            payload = json.loads(BSE_ACCURACY_QUICK_PROFIT.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("BSE B1 state must be an object")
        except (OSError, ValueError, json.JSONDecodeError):
            return unavailable("invalid", "BSE B1 quick-profit evidence is unreadable")

        def compact_trial(raw: Any) -> dict[str, Any] | None:
            if not isinstance(raw, dict):
                return None
            return {
                key: raw.get(key)
                for key in (
                    "rule",
                    "verdict",
                    "development_only",
                    "resolved",
                    "wins",
                    "observed_strict_success",
                    "wilson_lower_bound",
                    "after_cost_expectancy_r",
                    "active_sessions",
                    "source_sessions",
                    "sample_ready",
                    "qualified",
                )
            }

        def best_trial(raw: Any, *, prospective: bool) -> dict[str, Any] | None:
            if not isinstance(raw, dict) or not isinstance(raw.get("trials"), list):
                return None
            eligible = [
                item
                for item in raw["trials"]
                if isinstance(item, dict)
                and item.get("observed_strict_success") is not None
                and item.get("after_cost_expectancy_r") is not None
            ]
            if not eligible:
                return None

            def sortable(value: Any) -> float:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return float("-inf")
                return number if number == number else float("-inf")

            selected = max(
                eligible,
                key=lambda item: (
                    sortable(item["observed_strict_success"]),
                    sortable(item.get("wilson_lower_bound")),
                    sortable(item["after_cost_expectancy_r"]),
                    str(item.get("rule", "")),
                ),
            )
            result = compact_trial(selected)
            if result is not None and not prospective:
                result["development_only"] = True
                result["verdict"] = "development_only"
            return result

        readiness_names = (
            "activation_date",
            "prospective_source_sessions",
            "required_sessions",
            "rules_sample_ready",
            "registered_candidate_rules",
        )
        geometry_names = (
            "entry_mode",
            "stop_atr",
            "target_r",
            "max_hold_sessions",
            "trial_count",
        )
        raw_readiness = payload.get("readiness")
        raw_geometry = payload.get("geometry")
        raw_prospective = payload.get("prospective")
        raw_development = payload.get("development_transport")
        prospective = raw_prospective if isinstance(raw_prospective, dict) else {}
        development = raw_development if isinstance(raw_development, dict) else {}
        reported_status = payload.get("status", "invalid")
        if reported_status not in {"collecting", "rejected", "research_qualified"}:
            reported_status = "invalid"
        elif reported_status == "research_qualified" and not prospective.get(
            "qualified_rules"
        ):
            reported_status = "invalid"
        return JSONResponse(
            {
                "status": reported_status,
                "created_at": payload.get("created_at"),
                "readiness": (
                    {name: raw_readiness.get(name) for name in readiness_names}
                    if isinstance(raw_readiness, dict)
                    else None
                ),
                "geometry": (
                    {name: raw_geometry.get(name) for name in geometry_names}
                    if isinstance(raw_geometry, dict)
                    else None
                ),
                "prospective": {
                    "source_sessions": prospective.get("source_sessions"),
                    "rules_sample_ready": prospective.get("rules_sample_ready"),
                    "best_trial": best_trial(prospective, prospective=True),
                },
                "development_transport": {
                    "development_only": True,
                    "source_sessions": development.get("source_sessions"),
                    "rules_sample_ready": development.get("rules_sample_ready"),
                    "best_rule": best_trial(development, prospective=False),
                },
                "baseline_improved": False,
                "live": False,
                "promotion_allowed": False,
                "detail": payload.get(
                    "detail",
                    "BSE B1 is collecting prospective evidence; development transport cannot pass",
                ),
            }
        )

    @app.get("/api/accuracy-selector")
    async def api_accuracy_selector() -> JSONResponse:
        """Latest saved accuracy-selector evidence; absence is never presented as a pass."""
        from tradedesk.prediction.selective import AccuracySelectorPolicy

        artifacts: list[dict[str, Any]] = []
        if MODELS_DIR.exists():
            for path in sorted(MODELS_DIR.glob("20*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if "accuracy_selector_curve" not in payload:
                    continue
                artifacts.append(
                    {
                        "artifact": path.name,
                        "version": payload.get("version"),
                        "model_kind": payload.get("kind"),
                        "trained_on": payload.get("trained_on"),
                        "operating_point": payload.get("accuracy_operating_point"),
                        "curve": payload.get("accuracy_selector_curve") or [],
                    }
                )
        if not artifacts:
            return JSONResponse(
                {
                    "status": "not_trained",
                    "latest": None,
                    "detail": (
                        "selector code is ready; no compatible model artifact has been trained"
                    ),
                }
            )

        latest = artifacts[-1]
        operating = latest["operating_point"]
        policy = AccuracySelectorPolicy()
        adequately_sampled = [
            row for row in latest["curve"]
            if row.get("n_selected", 0) >= policy.min_calls
            and row.get("active_sessions", 0) >= policy.min_active_sessions
        ]
        best_observed = max(
            adequately_sampled,
            key=lambda row: (
                row.get("wilson_lower_bound", 0.0), row.get("observed_success", 0.0)
            ),
            default=None,
        )
        return JSONResponse(
            {
                "status": "qualified_shadow" if operating else "abstain",
                "latest": {
                    key: value for key, value in latest.items() if key != "curve"
                },
                "best_adequately_sampled": best_observed,
                "detail": (
                    "validation nomination only; still shadow and subject to "
                    "locked/prospective gates"
                    if operating
                    else "no validation operating point cleared every accuracy and "
                    "availability gate"
                ),
            }
        )

    @app.get("/api/accuracy-race")
    async def api_accuracy_race() -> JSONResponse:
        """Frozen Milestone-3 challenger-race status; missing evidence never passes."""
        path = MODELS_DIR / "accuracy-race" / "latest.json"
        if not path.exists():
            return JSONResponse(
                {
                    "status": "not_run",
                    "detail": "no frozen accuracy-race readiness artifact exists",
                }
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {"status": "invalid", "detail": "accuracy-race artifact is unreadable"}
            )
        cohort = payload.get("cohort") or {}
        return JSONResponse(
            {
                "status": payload.get("status", "invalid"),
                "detail": payload.get("detail"),
                "created_at": payload.get("created_at"),
                "cohort": {
                    "rows": cohort.get("rows"),
                    "sessions": cohort.get("sessions"),
                    "feature_version": cohort.get("feature_version"),
                    "economics_coverage": cohort.get("economics_coverage"),
                    "rule_score_coverage": cohort.get("rule_score_coverage"),
                    "blockers": cohort.get("blockers") or [],
                },
                "nominee": payload.get("nominee"),
                "candidates": [
                    {
                        "kind": row.get("kind"),
                        "oos_rows": row.get("oos_rows"),
                        "qualified": row.get("operating_point") is not None,
                        "best_adequately_sampled": row.get("best_adequately_sampled"),
                        "best_policy_coverage": row.get("best_policy_coverage"),
                    }
                    for row in (payload.get("candidates") or [])
                ],
                "locked_test": payload.get("locked_test"),
            }
        )

    @app.get("/api/accuracy-geometry")
    async def api_accuracy_geometry() -> JSONResponse:
        """Latest quick-profit development evidence; diagnostics never imply promotion."""
        path = MODELS_DIR / "accuracy-geometry" / "latest.json"
        if not path.exists():
            return JSONResponse(
                {"status": "not_run", "detail": "quick-profit geometry has not run"}
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {"status": "invalid", "detail": "accuracy-geometry artifact is unreadable"}
            )
        candidates = payload.get("candidates") or []
        best = max(
            candidates,
            key=lambda row: (
                row.get("wilson_lower_bound", 0.0),
                row.get("observed_success", 0.0),
            ),
            default=None,
        )
        positive_subgroups = [
            {
                "entry_mode": row.get("entry_mode"),
                "stop_atr": row.get("stop_atr"),
                "target_r": row.get("target_r"),
                "max_hold": row.get("max_hold"),
                **subgroup,
            }
            for row in candidates
            for subgroup in ((row.get("diagnostics") or {}).get("setup") or [])
            if subgroup.get("n", 0) >= 500 and subgroup.get("expectancy_r", -1.0) >= 0
        ]
        best_positive_setup = max(
            positive_subgroups,
            key=lambda row: (
                row.get("wilson_lower_bound", 0.0),
                row.get("accuracy", 0.0),
            ),
            default=None,
        )
        return JSONResponse(
            {
                "status": payload.get("status", "invalid"),
                "detail": payload.get("detail"),
                "created_at": payload.get("created_at"),
                "protocol": payload.get("protocol"),
                "development": payload.get("development"),
                "geometries_evaluated": len(candidates),
                "best": best,
                "best_positive_setup_diagnostic": best_positive_setup,
                "nominee": payload.get("nominee"),
                "locked_test": payload.get("locked_test"),
            }
        )

    @app.get("/api/accuracy-setup-stability")
    async def api_accuracy_setup_stability() -> JSONResponse:
        """Locked historical setup result; prospective status stays explicitly pending."""
        path = MODELS_DIR / "accuracy-setup-stability" / "latest.json"
        if not path.exists():
            return JSONResponse(
                {"status": "not_run", "detail": "setup stability has not run"}
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {"status": "invalid", "detail": "setup-stability artifact is unreadable"}
            )
        nominee = payload.get("nominee") or {}
        return JSONResponse(
            {
                "status": payload.get("status", "invalid"),
                "detail": payload.get("detail"),
                "created_at": payload.get("created_at"),
                "protocol": payload.get("protocol"),
                "development": payload.get("development"),
                "hypotheses": [
                    {
                        "name": (row.get("hypothesis") or {}).get("name"),
                        "stability_pass": row.get("stability_pass"),
                        "aggregate": row.get("aggregate"),
                        "selector_status": (row.get("selector") or {}).get("status"),
                    }
                    for row in (payload.get("hypotheses") or [])
                ],
                "nominee": nominee,
                "development_operating_point": nominee.get("development_operating_point"),
                "locked_test": payload.get("locked_test"),
                "evidence_level": (
                    "historical_locked_pass_prospective_pending"
                    if payload.get("status") == "locked_pass"
                    else "historical_development_only"
                ),
            }
        )

    @app.get("/api/accuracy-prospective-shadow")
    async def api_accuracy_prospective_shadow() -> JSONResponse:
        """Fresh M8 evidence only; never converts a historical pass into live authority."""
        if not ACCURACY_PROSPECTIVE_STATE.exists():
            return JSONResponse(
                {
                    "status": "not_activated",
                    "summary": None,
                    "activation": None,
                    "records": [],
                    "detail": "prospective shadow has not been activated",
                }
            )
        try:
            payload = json.loads(ACCURACY_PROSPECTIVE_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "summary": None,
                    "activation": None,
                    "records": [],
                    "detail": "prospective shadow state is unreadable",
                }
            )
        summary = payload.get("summary") or {}
        records = [
            {k: v for k, v in row.items() if k != "features"}
            for row in (payload.get("records") or [])[-50:]
        ]
        return JSONResponse(
            {
                "status": summary.get("status", "invalid"),
                "summary": summary,
                "activation": payload.get("activation"),
                "records": records,
                "current_errors": payload.get("current_errors", []),
                "detail": (
                    "shadow-only forward evidence; live eligibility remains unchanged"
                ),
            }
        )

    @app.get("/api/accuracy-prospective-monitor")
    async def api_accuracy_prospective_monitor() -> JSONResponse:
        """Independent M9 integrity/stress report; never a live-promotion endpoint."""
        if not ACCURACY_PROSPECTIVE_MONITOR.exists():
            return JSONResponse(
                {
                    "status": "not_run",
                    "integrity": None,
                    "detail": "prospective integrity monitor has not run",
                }
            )
        try:
            payload = json.loads(ACCURACY_PROSPECTIVE_MONITOR.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "integrity": None,
                    "detail": "prospective integrity report is unreadable",
                }
            )
        return JSONResponse(
            {
                "status": (payload.get("integrity") or {}).get("status", "invalid"),
                **payload,
            }
        )

    @app.get("/api/accuracy-prospective-control")
    async def api_accuracy_prospective_control() -> JSONResponse:
        """M10 random-selection control; not the broader random-timing gate."""
        if not ACCURACY_PROSPECTIVE_CONTROL.exists():
            return JSONResponse(
                {
                    "status": "not_run",
                    "summary": None,
                    "detail": "prospective matched-random control has not run",
                }
            )
        try:
            payload = json.loads(ACCURACY_PROSPECTIVE_CONTROL.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "summary": None,
                    "detail": "prospective matched-random control is unreadable",
                }
            )
        summary = payload.get("summary") or {}
        sessions = [
            {
                key: session.get(key)
                for key in (
                    "armed_on",
                    "assigned_at",
                    "score_deadline",
                    "prospective_eligible",
                    "candidate_count",
                    "model_call_count",
                )
            }
            for session in payload.get("sessions", [])[-50:]
        ]
        return JSONResponse(
            {
                "status": summary.get("status", "invalid"),
                "registration": {
                    key: payload.get(key)
                    for key in (
                        "version",
                        "registered_at",
                        "seed",
                        "n_cohorts",
                        "scope",
                    )
                },
                "summary": summary,
                "outcome_parity": payload.get("outcome_parity"),
                "sessions": sessions,
                "errors": payload.get("errors", []),
                "detail": (
                    "same-session random candidate selection only; the broader "
                    "random-timing gate remains pending"
                ),
            }
        )

    @app.get("/api/accuracy-prospective-timing")
    async def api_accuracy_prospective_timing() -> JSONResponse:
        """M11 prospective same-stock timing control; never grants live authority."""
        if not ACCURACY_PROSPECTIVE_TIMING.exists():
            return JSONResponse(
                {
                    "status": "not_run",
                    "summary": None,
                    "detail": "prospective same-stock timing control has not run",
                }
            )
        try:
            payload = json.loads(ACCURACY_PROSPECTIVE_TIMING.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {
                    "status": "invalid",
                    "summary": None,
                    "detail": "prospective same-stock timing control is unreadable",
                }
            )
        summary = payload.get("summary") or {}
        records = [
            {
                key: record.get(key)
                for key in (
                    "signal_id",
                    "scrip_code",
                    "symbol",
                    "armed_on",
                    "assigned_at",
                    "score_deadline",
                    "prospective_eligible",
                )
            }
            for record in payload.get("records", [])[-50:]
        ]
        return JSONResponse(
            {
                "status": summary.get("status", "invalid"),
                "registration": {
                    key: payload.get(key)
                    for key in (
                        "version",
                        "registered_at",
                        "seed",
                        "n_cohorts",
                        "offset_sessions",
                        "scope",
                    )
                },
                "summary": summary,
                "source_integrity": payload.get("source_integrity"),
                "records": records,
                "errors": payload.get("errors", []),
                "detail": (
                    "same-stock daily placebo entries assigned 1-20 NSE sessions "
                    "after each model signal; research-only"
                ),
            }
        )

    @app.get("/api/crypto/universe")
    async def api_crypto_universe() -> JSONResponse:
        """Latest full-active-universe run; absence is shown as not run, never as zero."""
        if not CRYPTO_UNIVERSE_REPORT.exists():
            return JSONResponse(
                {
                    "status": "not_run",
                    "detail": "the full-active-universe crypto tracker has not run yet",
                }
            )
        try:
            payload = json.loads(CRYPTO_UNIVERSE_REPORT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse(
                {"status": "invalid", "detail": "crypto universe report is unreadable"}
            )
        return JSONResponse({"status": "complete", **payload})

    @app.get("/api/state/bse")
    async def api_state_bse() -> JSONResponse:
        if bse_state is None:
            return JSONResponse({"error": "no BSE live state configured for this dashboard"}, 404)
        return JSONResponse(bse_state.snapshot())

    @app.get("/api/eod")
    async def api_eod(on: str | None = Query(None, alias="date")) -> JSONResponse:
        if journal_path is None:
            return JSONResponse({"error": "no journal configured for this dashboard"}, 404)
        from tradedesk.broker.indstocks.models import IST
        from tradedesk.journal import Journal
        from tradedesk.journal.stats import eod_report

        day = date.fromisoformat(on) if on else datetime.now(IST).date()
        with Journal(journal_path) as jn:
            return JSONResponse(eod_report(jn, day))

    @app.get("/api/performance")
    async def api_performance(
        period: Literal["week", "month"] = "week", n: int = 12
    ) -> JSONResponse:
        if journal_path is None:
            return JSONResponse({"error": "no journal configured for this dashboard"}, 404)
        from tradedesk.journal import Journal
        from tradedesk.journal.stats import performance_rollup

        with Journal(journal_path) as jn:
            return JSONResponse(performance_rollup(jn, period=period, n=n))

    @app.get("/api/symbols")
    async def api_symbols(
        market: Literal["nse", "crypto", "bse"] = "nse", q: str = "", limit: int = 20
    ) -> JSONResponse:
        """Lookup/navigation: codes+symbols matching `q`, for the dashboard's search box -
        doesn't touch the engine, so it's cheap enough to run on the event loop directly."""
        from tradedesk.analysis import db_for
        from tradedesk.broker.indstocks.models import Interval
        from tradedesk.data.candle_store import CandleStore

        prefix = "CDX_" if market == "crypto" else ""
        db = db_for(market)
        if not db.exists():
            return JSONResponse([])
        with CandleStore(db) as store:
            hits = store.search_codes(Interval.D1, query=q, prefix=prefix, limit=limit)
        return JSONResponse([{"code": c, "symbol": s} for c, s in hits])

    @app.get("/api/analyze")
    async def api_analyze(
        code: str, market: Literal["nse", "crypto", "bse"] = "nse", on: str | None = None
    ) -> JSONResponse:
        """On-demand buy/hold call for one symbol, either market - same engine and same
        code path as the `analyze` MCP tool (tradedesk/analysis.py), just reachable from
        the dashboard's search box instead of an MCP client. CPU/DB work off the event
        loop per the project's no-blocking-the-loop rule."""
        from tradedesk.analysis import analyze_symbol
        from tradedesk.config import load_config

        settings = load_config(".")
        result = await asyncio.to_thread(analyze_symbol, settings, market, code, on)
        return JSONResponse(json.loads(json.dumps(result, default=str)))

    @app.get("/api/potential-calls/summary")
    async def api_potential_calls_summary() -> JSONResponse:
        """Compact "how many potential calls today, how many resolved, hit rate" per market
        (2026-09-15 request: a small Report-tab section covering all three) - reuses the
        exact same per-market call logs the Calls/Crypto/BSE tabs already read, just
        summarised into one number set per market rather than a full row-by-row table.
        Live only for crypto/BSE (excludes the historical backfill) - matching every other
        "is the agent doing well" number on this dashboard (agent reliability, Coin/Stock
        confidence); NSE has no backfill concept for this log, every row is naturally live."""
        from tradedesk.analysis import BSE_LOG, CRYPTO_LOG, NSE_LOG

        out: dict[str, Any] = {}
        for market, log_path in (("nse", NSE_LOG), ("crypto", CRYPTO_LOG), ("bse", BSE_LOG)):
            all_rows = _read_call_log(log_path, limit=100_000)
            rows = all_rows if market == "nse" else [r for r in all_rows if r.get("source") == "live"]  # noqa: E501
            today = date.today().isoformat()
            today_rows = [r for r in rows if r.get("logged_at", "").startswith(today)]
            resolved = [r for r in rows if r.get("outcome")]
            wins = sum(1 for r in resolved if r.get("label") == 1)
            out[market] = {
                "logged_today": len(today_rows),
                "n_total": len(rows),
                "n_resolved": len(resolved),
                "win_rate": wins / len(resolved) if resolved else None,
            }
        return JSONResponse(out)

    @app.get("/api/session-report")
    async def api_session_report(
        market: Literal["nse", "crypto", "bse"] = "nse", on: str | None = None
    ) -> JSONResponse:
        """The full day's dissection for one market's potential calls (2026-09-15 request:
        "clicking on that section should show the whole picture for the day") - the exact
        markdown `scripts/<market>_signal_tracker.py` already writes per day
        (data/reports/<market>_sessions/<date>.md), read back raw rather than recomputed, so
        the dashboard can never show a different picture than the file the tracker itself
        produced. `on` defaults to the newest report that exists for that market."""
        sessions_dir = Path(f"data/reports/{market}_sessions")
        path: Path | None
        if on:
            path = sessions_dir / f"{on}.md"
        else:
            candidates = sorted(sessions_dir.glob("*.md")) if sessions_dir.exists() else []
            path = candidates[-1] if candidates else None
        if path is None or not path.exists():
            return JSONResponse({"error": f"no session report found for {market}"}, status_code=404)  # noqa: E501
        return JSONResponse({"date": path.stem, "text": path.read_text(encoding="utf-8")})

    @app.get("/api/nse/calls")
    async def api_nse_calls(limit: int = 500) -> JSONResponse:
        """NSE's "potential calls" log (2026-09-15, data/reports/nse_signal_tracking.jsonl) -
        every candidate the daily scan evaluates, tradeable or not, resolved forward via
        triple-barrier the same way crypto/BSE's setups are. Separate from /api/eod's paper
        book on purpose: the paper book only ever gets a row once a signal actually
        triggers live, which the eligibility gate currently blocks entirely, so this is the
        only forward record of what the agent's daily scoring actually predicts vs what
        really happens. Higher default limit than crypto/BSE's 50 since NSE evaluates the
        full universe (dozens to ~100+ candidates) every single day, not a fixed watchlist."""
        from tradedesk.analysis import NSE_LOG

        return JSONResponse(_read_call_log(NSE_LOG, limit))

    @app.get("/api/crypto/calls")
    async def api_crypto_calls(limit: int = 50) -> JSONResponse:
        """Crypto's own call log (data/reports/crypto_signal_tracking.jsonl) - kept as a
        separate dataset from NSE's paper-book /api/eod on purpose (different market,
        different costs, no shared population to pool)."""
        from tradedesk.analysis import CRYPTO_LOG

        return JSONResponse(_read_call_log(CRYPTO_LOG, limit))

    @app.get("/api/bse/calls")
    async def api_bse_calls(limit: int = 50) -> JSONResponse:
        """BSE's own call log (data/reports/bse_signal_tracking.jsonl) - same reasoning as
        crypto's: no BSE paper book exists, so this JSONL is the only track record."""
        from tradedesk.analysis import BSE_LOG

        return JSONResponse(_read_call_log(BSE_LOG, limit))

    @app.get("/api/research/calls")
    async def api_research_calls(
        market: Literal["nse", "crypto", "bse"] = "nse", limit: int = 500
    ) -> JSONResponse:
        """Research tracker's own call log for one market - forward, out-of-sample candidate
        calls, own dataset per market, cannot alert. See tradedesk.research_tracker's module
        docstring for the full design rationale (including the multi-market generalisation)."""
        from tradedesk.research_tracker import log_path_for

        return JSONResponse(_read_call_log(log_path_for(market), limit))

    @app.get("/api/research/summary")
    async def api_research_summary(market: Literal["nse", "crypto", "bse"] = "nse") -> JSONResponse:  # noqa: E501
        """One row per candidate rule (including the random_eligible control) for one market:
        n, hit rate, gross/net R, t-stats, and whether it has cleared the SAME evidence bar
        the live setups on THAT market are held to - the exact computation
        scripts/research_tracker.py's CLI report and flag_research_findings() both use, so
        this can never show different numbers."""
        from tradedesk.research_tracker import cost_r_for, load_log, log_path_for, rule_stats

        rows = load_log(log_path_for(market))
        return JSONResponse(rule_stats(rows, cost_r_for(market)))

    @app.get("/api/eod-learning")
    async def api_eod_learning(
        market: Literal["nse", "crypto", "bse"] = "nse"
    ) -> JSONResponse:
        """History of the EOD self-learning step (tradedesk.eod_learning, 2026-09-14; crypto
        added the same day) for one market - one row per run: how much forward evidence
        exists, the walk-forward OOS Brier/ROC-AUC, locked-tail net R, and which feature (if
        any) was explored that run and whether it was adopted. NSE/BSE run it once daily
        (bundled into their nightly research_tracker run); crypto runs it twice a day (once
        bundled, once via its own schedule), since crypto data is 24/7 rather than one
        session. Newest first."""
        from tradedesk.eod_learning import load_history

        return JSONResponse(list(reversed(load_history(market))))

    @app.get("/api/ml-calibration")
    async def api_ml_calibration() -> JSONResponse:
        """"How close and how successful are our predictions" (2026-09-15 request) - exposes
        prediction/calibration.py::drift_check() (built 2026-09-13, already the thing
        `tradedesk ml check-drift` runs daily as the 5th step of tradedesk-after-close and
        auto-flags into review_queue.py on drift) so it's visible, not just a background
        pass/fail. NSE only, matching `ml check-drift`'s own scope - the shadow log and the
        paper-book outcome proxy (`r_multiple > 0`) it reads are both NSE-only today.
        Buckets stay empty until real resolved paper trades exist - honestly reports that
        rather than a fabricated calibration."""
        from tradedesk.journal import Journal
        from tradedesk.prediction import calibration as calib
        from tradedesk.prediction.predict import read_shadow

        predictions = read_shadow(calib.SHADOW_LOG_PATH)
        journal_path = calib.NSE_JOURNAL_PATH
        outcomes: dict[str, int] = {}
        if journal_path.exists():
            with Journal(journal_path) as jn:
                outcomes = {
                    row["signal_id"]: (1 if row["r_multiple"] > 0 else 0)
                    for row in jn.trades(source="paper")
                }
        report = calib.drift_check(predictions, outcomes)
        return JSONResponse(
            {
                "n_logged": int(len(predictions)),
                "n_resolved_checked": sum(n for _, _, _, n in report.buckets),
                **calib.report_as_dict(report),
            }
        )

    @app.get("/api/ml-calibration/history")
    async def api_ml_calibration_history() -> JSONResponse:
        """Day-by-day calibration trend (2026-09-15 request) - one row per day this was
        checked (`tradedesk ml check-drift`, scheduled daily), so "is the model's calibration
        actually improving" is a real trend rather than one re-computed-fresh snapshot."""
        from tradedesk.prediction import calibration as calib

        return JSONResponse(list(reversed(calib.load_calibration_history(calib.CALIBRATION_HISTORY_PATH))))  # noqa: E501

    @app.get("/api/lab/summary")
    async def api_lab_summary() -> JSONResponse:
        """Read-only glance at the M14-M18 research lab (2026-09-16) - its own SQLite
        registry (tradedesk_lab.registry.Registry, opened read-only, same as the lab's own
        standalone dashboard reads it) and its own forward-evidence state file. This
        dashboard never writes into data/m14_m18/ - the lab's own isolation guarantee
        (verify-base) is unaffected by surfacing a summary here. The lab's own richer
        diagnostics (CPCV/PBO/DSR charts, per-run detail) stay at its standalone page
        (127.0.0.1:8766), linked from the UI rather than duplicated."""
        import sys

        # tradedesk_lab lives at the repo root, not under src/ - `tradedesk dashboard`'s
        # console-script entry point doesn't put the repo root on sys.path the way
        # `python -m`/pytest do (both of which is how this imported cleanly everywhere it
        # was tested before), so it needs adding explicitly here, once.
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            from tradedesk_lab.artifacts import OUTPUT as LAB_OUTPUT
            from tradedesk_lab.registry import Registry
        except ImportError:
            return JSONResponse({"error": "tradedesk_lab not present in this checkout"}, 404)

        def load() -> dict[str, Any]:
            reg_path = LAB_OUTPUT / "registry.sqlite"
            runs: list[dict[str, Any]] = []
            if reg_path.exists():
                with Registry(reg_path, readonly=True) as registry:
                    runs = registry.runs()
            report = next((r["report"] for r in runs if r["status"] == "completed"), None)
            portfolio = None
            eligibility = None
            if report:
                portfolio = (
                    report.get("historical_holdout", {})
                    .get("existing_candidates", {})
                    .get("portfolio")
                )
                eligibility = report.get("eligibility")
            fwd_path = LAB_OUTPUT / "forward/state.json"
            forward = json.loads(fwd_path.read_text(encoding="utf-8")) if fwd_path.exists() else None  # noqa: E501
            return {
                "n_runs": len(runs),
                "latest_run_id": report["id"] if report else None,
                "latest_run_market": report["metadata"]["market"] if report else None,
                "latest_run_range": (
                    f"{report['metadata']['from']} to {report['metadata']['to']}" if report else None  # noqa: E501
                ),
                "historical_portfolio": (
                    {
                        "trades": portfolio["trades"],
                        "net_pnl": portfolio["net_pnl"],
                        "net_win_rate": portfolio["net_win_rate"],
                        "sharpe": portfolio["sharpe"],
                    }
                    if portfolio
                    else None
                ),
                "eligibility_decision": eligibility["decision"] if eligibility else None,
                "forward_summary": forward.get("summary") if forward else None,
                "forward_activation": forward.get("activation") if forward else None,
            }

        return JSONResponse(await asyncio.to_thread(load))

    @app.get("/api/reliability/overall")
    async def api_reliability_overall() -> JSONResponse:
        """The one top-right "agent reliability" number - Wilson lower bound pooled across
        every REAL, LIVE resolved call across all three markets - plus the per-market
        breakdown behind it. See reliability.py/reliability_sources.py for why this excludes
        backfill and research candidates."""
        from tradedesk.reliability_sources import overall_reliability_now

        return JSONResponse(overall_reliability_now())

    @app.get("/api/reliability/evaluated")
    async def api_reliability_evaluated() -> JSONResponse:
        """Per-market confidence from all resolved forward agent evaluations.

        Includes executable, rejected, and shadow tracker rows; excludes backfill and
        research replay. This endpoint feeds the dashboard header only and does not alter
        the accuracy-qualification policy.
        """
        from tradedesk.reliability_sources import evaluated_reliability_now

        return JSONResponse(evaluated_reliability_now())

    @app.get("/api/reliability/history")
    async def api_reliability_history() -> JSONResponse:
        """Daily-logged trend for the top-right number, so it can be watched for whether it
        is actually rising as more real evidence accumulates - not just asserted."""
        from tradedesk import reliability

        return JSONResponse(reliability.load_reliability_history(reliability.HISTORY_PATH))

    @app.get("/api/reliability/symbols")
    async def api_reliability_symbols(
        market: Literal["nse", "crypto", "bse"] = "nse",
        source: Literal["live", "backfill", "all"] = "all",
    ) -> JSONResponse:
        """Per-symbol/coin confidence (Wilson lower bound), sorted most-trustworthy first."""
        from dataclasses import asdict

        from tradedesk.analysis import BSE_LOG, CRYPTO_LOG
        from tradedesk.reliability_sources import (
            crypto_bse_symbol_confidence,
            nse_symbol_confidence,
        )

        src = None if source == "all" else source
        if market == "nse":
            rows = nse_symbol_confidence()
        else:
            rows = crypto_bse_symbol_confidence(CRYPTO_LOG if market == "crypto" else BSE_LOG, source=src)  # noqa: E501
        return JSONResponse([asdict(r) for r in rows])

    @app.get("/api/reliability/backfill-pnl")
    async def api_reliability_backfill_pnl(
        market: Literal["crypto", "bse"] = "crypto",
        days: int | None = None,
    ) -> JSONResponse:
        """Compact P&L summary over a timeframe for the removed "Past (backfill)" browsing
        table - days=None is all-time."""
        from tradedesk.analysis import BSE_LOG, CRYPTO_LOG
        from tradedesk.reliability_sources import crypto_bse_backfill_pnl

        log = CRYPTO_LOG if market == "crypto" else BSE_LOG
        return JSONResponse(crypto_bse_backfill_pnl(log, days=days))

    @app.get("/api/review")
    async def api_review_list() -> JSONResponse:
        """Pending/decided review-queue items (review_queue.py). Most producers (`tradedesk
        review week`, drift checks, signal-tracker failure flags) are never auto-applied -
        see review_queue.py's own docstring for why that stays a hard rule. A self-review
        item (`proposal_ref` set) is different by explicit user request: approving it
        applies it immediately (see `/api/review/decide`) - `applied` here reports whether
        that has actually happened yet for a decided item."""
        from tradedesk.proposals import safe_filename
        from tradedesk.review_queue import load_queue
        from tradedesk.self_review.apply import APPLIED_DIR

        items = sorted(load_queue().values(), key=lambda i: i.created_at, reverse=True)
        rows = []
        for i in items:
            row = vars(i).copy()
            if i.proposal_ref:
                row["applied"] = (APPLIED_DIR / f"{safe_filename(i.id)}.json").is_file()
            rows.append(row)
        return JSONResponse(rows)

    @app.post("/api/review/decide")
    async def api_review_decide(item_id: str, status: str) -> JSONResponse:
        """For every producer except self-review, this is still pure bookkeeping - it only
        flips `status`/`decided_at`, exactly as review_queue.py's own docstring promises.

        For a self-review item (`proposal_ref` set), the user explicitly asked that
        "approve" mean something, not just bookkeeping: approving immediately calls
        `self_review/apply.py::apply()`, which re-checks `status == "approved"` and the
        proposal's own hash before writing anything - so this is still gated, just no
        longer a separate manual step. Rejecting still only stamps the cooldown
        (`decide_with_cooldown`) and applies nothing. If apply() itself refuses (e.g. a
        missing pre-generated source, or a git failure), the decision already happened -
        it is not silently rolled back - and the response reports `apply_error` so the
        dashboard can show a decided-but-not-applied state rather than hiding it."""
        from tradedesk.review_queue import decide, load_queue
        from tradedesk.self_review import apply as self_review_apply
        from tradedesk.self_review.decision_packet import decide_with_cooldown

        if status not in ("approved", "rejected"):
            return JSONResponse({"error": "status must be approved or rejected"}, status_code=400)
        existing = load_queue().get(item_id)
        if existing is None:
            return JSONResponse({"error": f"no review item {item_id!r}"}, status_code=404)

        if not existing.proposal_ref:
            item = decide(item_id, status)
            return JSONResponse(vars(item)) if item else JSONResponse(
                {"error": f"no review item {item_id!r}"}, status_code=404
            )

        item = decide_with_cooldown(item_id, status)
        if item is None:
            return JSONResponse({"error": f"no review item {item_id!r}"}, status_code=404)
        if status == "rejected":
            return JSONResponse(vars(item))
        try:
            result = self_review_apply.apply(
                item_id, root=self_review_apply.ROOT, lab_output=self_review_apply.OUTPUT
            )
        except self_review_apply.ApplyRefused as exc:
            return JSONResponse({"item": vars(item), "apply_error": str(exc)}, status_code=409)
        return JSONResponse(
            {
                "item": vars(item),
                "applied": {
                    "git_commit_sha": result.git_commit_sha,
                    "files_changed": result.files_changed,
                    "applied_at": result.applied_at,
                },
            }
        )

    @app.get("/api/review/replacement-search")
    async def api_replacement_search() -> JSONResponse:
        """The latest replacement-search report per market (self_review/
        replacement_search.py): what it tested, the best candidates so far and what it
        tries next - the research that keeps running whether or not an item is pending."""
        import json

        from tradedesk.self_review.replacement_search import SEARCH_DIR

        out = []
        for market in ("nse", "bse", "crypto"):
            reports = sorted((SEARCH_DIR / market).glob("*.json"))
            if not reports:
                continue
            data = json.loads(reports[-1].read_text(encoding="utf-8"))
            out.append(
                {
                    "market": market,
                    "generated": reports[-1].stem,
                    "status": data.get("status"),
                    "summary": data.get("summary"),
                    "space_size": data.get("space_size"),
                    "tested_total": data.get("tested_total"),
                    "untested_remaining": data.get("untested_remaining"),
                    "baseline_net_r": data.get("baseline_net_r"),
                    "baseline_hit_rate": data.get("baseline_hit_rate"),
                    "leaderboard": (data.get("leaderboard") or [])[:10],
                }
            )
        return JSONResponse(out)

    @app.get("/api/review/{item_id}/gauntlet")
    async def api_review_gauntlet(item_id: str) -> JSONResponse:
        """The full validation behind a self-review proposal - the gauntlet report, not
        just the one-line summary `ReviewItem.proposal` shows."""
        from dataclasses import asdict

        from tradedesk.proposals import load_proposal
        from tradedesk.review_queue import load_queue

        item = load_queue().get(item_id)
        if item is None:
            return JSONResponse({"error": f"no review item {item_id!r}"}, status_code=404)
        if not item.proposal_ref:
            return JSONResponse({"error": "this item has no linked proposal"}, status_code=404)
        proposal = load_proposal(Path(item.proposal_ref))
        return JSONResponse(
            {
                "kind": proposal.kind.value,
                "payload": asdict(proposal.payload),
                "gauntlet_report": proposal.gauntlet_report,
                "evidence_summary": proposal.evidence_summary,
                "cooldown_until": proposal.cooldown_until,
            }
        )

    @app.get("/chart")
    async def chart(path: str) -> Any:
        p = Path(path)
        if not p.exists() or p.suffix.lower() != ".png":
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="image/png")

    def _sse_stream(st: DashboardState) -> StreamingResponse:
        q = st.subscribe()

        async def gen() -> AsyncIterator[bytes]:
            try:
                yield _sse(st.snapshot())
                while True:
                    try:
                        snap = await asyncio.wait_for(q.get(), timeout=15)
                        yield _sse(snap)
                    except TimeoutError:
                        yield b": keepalive\n\n"
            finally:
                st.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/events")
    async def events() -> StreamingResponse:
        return _sse_stream(state)

    @app.get("/events/bse", response_model=None)
    async def events_bse() -> StreamingResponse | JSONResponse:
        if bse_state is None:
            return JSONResponse({"error": "no BSE live state configured for this dashboard"}, 404)
        return _sse_stream(bse_state)

    # 2026-09-16: mount the M14-M18 research lab's own FastAPI app under /lab so it's
    # reachable through this same process/port (and therefore the same Cloudflare tunnel)
    # instead of needing a second exposed port. This is a straight ASGI mount of the lab's
    # OWN create_app(root, output) - none of its routes, registry access, or read-only
    # discipline are touched; its static page was updated separately (API_BASE) to prefix
    # its own fetch calls with the mount path so it works identically standalone (still
    # servable on its own port 8766 if ever wanted) or mounted here.
    import sys

    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from tradedesk_lab.artifacts import OUTPUT as LAB_OUTPUT
        from tradedesk_lab.artifacts import ROOT as LAB_ROOT
        from tradedesk_lab.server import create_app as create_lab_app

        app.mount("/lab", create_lab_app(LAB_ROOT, LAB_OUTPUT))
    except ImportError:
        pass  # tradedesk_lab not present in this checkout

    return app


def _sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


async def serve(app: FastAPI, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
