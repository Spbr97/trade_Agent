"""M18: independent localhost preview. GET-only; no production dashboard imports."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from string import hexdigits
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from tradedesk_lab.aem_scorecard import build_accuracy_scorecard
from tradedesk_lab.decision import from_entry
from tradedesk_lab.registry import Registry


def create_app(root: Path, output: Path) -> FastAPI:
    app = FastAPI(title="tradedesk Research Lab", docs_url=None, redoc_url=None)

    def read_runs() -> list[dict[str, Any]]:
        path = output / "registry.sqlite"
        if not path.exists():
            return []
        with Registry(path, readonly=True) as registry:
            return registry.runs()

    def latest() -> dict[str, Any] | None:
        runs = read_runs()
        return next((run["report"] for run in runs if run["status"] == "completed"), None)

    def read_forward() -> dict[str, Any]:
        path = output / "forward/state.json"
        if not path.exists():
            return {
                "activation": None,
                "summary": None,
                "records": [],
                "recent_errors": [],
            }
        state = json.loads(path.read_text(encoding="utf-8"))
        records = []
        for row in state.get("records", [])[-200:]:
            records.append({k: v for k, v in row.items() if k not in {"features", "signal"}})
        return {
            "activation": state.get("activation"),
            "summary": state.get("summary"),
            "records": records,
            "recent_errors": state.get("recent_errors", []),
        }

    def read_accuracy_milestone() -> dict[str, Any]:
        latest_path = output / "aem_staged/latest.json"
        if not latest_path.is_file():
            return {
                "available": False,
                "eligible_for_live": False,
                "status": "awaiting_frozen_baseline",
            }
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        identifier = latest.get("id")
        if (
            not isinstance(identifier, str)
            or len(identifier) != 32
            or any(character not in hexdigits for character in identifier)
        ):
            raise ValueError("invalid staged dataset identifier")
        manifest_path = output / "aem_staged/datasets" / identifier / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("id") != identifier:
            raise ValueError("staged latest pointer and manifest differ")
        source = manifest["source"]
        coverage = source["coverage"]
        exceptions = {
            code: row["missing_or_incomplete_sessions"]
            for code, row in coverage.items()
            if row["missing_or_incomplete_sessions"]
        }
        collected_rows = sum(row["minute"]["rows"] for row in source["symbols"].values())
        complete_sessions = sum(row["complete_sessions"] for row in coverage.values())
        total_sessions = complete_sessions + sum(
            len(row["missing_or_incomplete_sessions"]) for row in coverage.values()
        )
        diagnostics = manifest["diagnostics"]
        validation = None
        validation_pointer = output / "aem_staged_validation/latest.json"
        if validation_pointer.is_file():
            pointer = json.loads(validation_pointer.read_text(encoding="utf-8"))
            if pointer.get("dataset_id") == identifier:
                candidate = Path(pointer["path"])
                if candidate.is_file():
                    loaded = json.loads(candidate.read_text(encoding="utf-8"))
                    if (
                        loaded.get("dataset_id") == identifier
                        and loaded.get("status") == "historical_diagnostic_only"
                    ):
                        validation = loaded
        scorecard = (
            validation["scorecard"]
            if validation is not None
            else manifest.get("accuracy_scorecard") or build_accuracy_scorecard(manifest)
        )
        mean_reversion_search = None
        mean_reversion_path = root / "docs/evidence/daily-mean-reversion-exit-search.json"
        if mean_reversion_path.is_file():
            mean_reversion_search = json.loads(mean_reversion_path.read_text(encoding="utf-8"))
        information_quality_experiment = None
        information_quality_path = root / "docs/evidence/aem-information-quality-experiment.json"
        if information_quality_path.is_file():
            information_quality_experiment = json.loads(
                information_quality_path.read_text(encoding="utf-8")
            )
        market_sector_context_readiness = None
        context_path = root / "docs/evidence/aem-market-sector-context-readiness.json"
        if context_path.is_file():
            market_sector_context_readiness = json.loads(context_path.read_text(encoding="utf-8"))
        aem_v2_protocol = None
        aem_v2_path = root / "docs/evidence/aem-v2-protocol.json"
        if aem_v2_path.is_file():
            aem_v2_protocol = json.loads(aem_v2_path.read_text(encoding="utf-8"))
        aem_v2_engine = None
        aem_v2_engine_path = root / "docs/evidence/aem-v2-engine.json"
        if aem_v2_engine_path.is_file():
            aem_v2_engine = json.loads(aem_v2_engine_path.read_text(encoding="utf-8"))
        return {
            "available": True,
            "status": manifest["status"],
            "eligible_for_live": bool(manifest["eligible_for_live"]),
            "dataset_id": identifier,
            "created_at": manifest["created_at"],
            "collection": {
                "plan_id": manifest["plan_id"],
                "requests_recorded": source["requests_recorded"],
                "collected_rows": collected_rows,
                "expected_rows": total_sessions * 375,
                "complete_sessions": complete_sessions,
                "total_sessions": total_sessions,
                "exceptions": exceptions,
            },
            "baseline": {
                "symbols": manifest["symbols_with_daily_and_m1"],
                "evaluation_sessions": manifest["evaluation_sessions"],
                "candidate_events": manifest["events"],
                "resolved_trades": manifest["resolved_trades"],
                "strict_success_rate": manifest["strict_success_rate"],
                "strict_success_wilson95": diagnostics["overall"]["strict_success_wilson95"],
                "mean_net_r": manifest["mean_net_r"],
                "session_coverage": diagnostics["session_coverage"],
                "incomplete_sessions_rejected": manifest["audit"].get("incomplete_session", 0),
            },
            "scorecard": scorecard,
            "mean_reversion_exit_search": mean_reversion_search,
            "information_quality_experiment": information_quality_experiment,
            "market_sector_context_readiness": market_sector_context_readiness,
            "aem_v2_protocol": aem_v2_protocol,
            "aem_v2_engine": aem_v2_engine,
            "validation": (
                {
                    "id": validation["id"],
                    "cohorts": validation["matched_random"]["comparison"]["comparison"][
                        "cohort_count"
                    ],
                    "random_advantage_r": validation["matched_random"]["comparison"]["comparison"][
                        "actual_minus_null_mean_net_r"
                    ],
                    "minimum_stress_mean_net_r": validation["stress"]["minimum_mean_net_r"],
                    "portfolio_selected_fills": validation["portfolio"]["selected_fills"],
                    "portfolio_mean_net_r": validation["portfolio"]["mean_net_r_after_constraints"],
                }
                if validation is not None
                else None
            ),
            "protocol": {
                "version": manifest["accuracy_protocol"]["version"],
                "sha256": manifest["accuracy_protocol_sha256"],
                "target_rate": manifest["accuracy_protocol"]["minimum_eligibility_observed_rate"],
                "prospective_minimum": manifest["accuracy_protocol"][
                    "minimum_prospective_resolved"
                ],
                "top_k": manifest["accuracy_protocol"]["reported_top_k_policies"],
            },
            "next_gate": (
                "accuracy_improvement_experiments"
                if validation is not None
                and not validation["scorecard"]["all_promotion_gates_pass"]
                else (
                    "prospective_evidence"
                    if validation is not None
                    else "matched_random_cost_stress_and_portfolio_replay"
                )
            ),
            "message": (
                "The broader frozen baseline is below the target and loses after costs; "
                "it is diagnostic only and must not generate live calls. Every later "
                "milestone must report accuracy, uncertainty, availability and net-R "
                "deltas against this frozen baseline."
            ),
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (Path(__file__).parent / "static/index.html").read_text(encoding="utf-8")

    @app.get("/api/summary")
    async def summary() -> dict[str, Any]:
        def load() -> dict[str, Any]:
            rows = read_runs()
            return {
                "report": latest(),
                "runs": [{k: v for k, v in row.items() if k != "report"} for row in rows],
                "preview": True,
                "market": "NSE",
                "production_url": "http://127.0.0.1:8765",
            }

        return await asyncio.to_thread(load)

    @app.get("/api/research/runs")
    async def runs() -> list[dict[str, Any]]:
        return await asyncio.to_thread(read_runs)

    @app.get("/api/forward")
    async def forward() -> dict[str, Any]:
        return await asyncio.to_thread(read_forward)

    @app.get("/api/accuracy-milestone")
    async def accuracy_milestone() -> dict[str, Any]:
        return await asyncio.to_thread(read_accuracy_milestone)

    @app.get("/api/research/run/{identifier}")
    async def run(identifier: str) -> dict[str, Any]:
        def load() -> dict[str, Any] | None:
            if not (output / "registry.sqlite").exists():
                return None
            with Registry(output / "registry.sqlite", readonly=True) as registry:
                return registry.run(identifier)

        result = await asyncio.to_thread(load)
        if result is None:
            raise HTTPException(404, "Run not found")
        return result

    @app.get("/api/research/agreement")
    async def agreement(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]] | None:
            report = latest()
            if report is None:
                return None
            path = output / "runs" / report["id"] / "predictions.json"
            return json.loads(path.read_text(encoding="utf-8"))[-limit:] if path.exists() else None

        result = await asyncio.to_thread(load)
        if result is None:
            raise HTTPException(404, "No recorded ensemble predictions yet")
        return result

    @app.get("/api/harness")
    async def harness() -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            directory = output / "harness"
            if not directory.exists():
                return []
            reports = []
            for report_path in sorted(directory.glob("*/latest.json")):
                try:
                    reports.append(json.loads(report_path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
            return reports

        return await asyncio.to_thread(load)

    @app.get("/api/decisions")
    async def decisions(market: str = Query("nse", pattern="^(nse|bse|crypto)$")) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            directory = root / "data/watchlists"
            if market != "nse":
                directory /= market
            files = sorted(directory.glob("*.json"))
            if not files:
                return {"as_of": None, "market": market, "decisions": []}
            try:
                wl = json.loads(files[-1].read_text(encoding="utf-8"))
                return {
                    "as_of": wl.get("on"),
                    "market": market,
                    "decisions": [
                        from_entry(entry, wl["generated_at"]).to_json()
                        for entry in wl.get("entries", [])
                    ],
                }
            except (KeyError, ValueError):
                return {
                    "as_of": None,
                    "market": market,
                    "decisions": [],
                    "error": "Saved watchlist is incomplete; retry after the producer finishes",
                }

        return await asyncio.to_thread(load)

    return app
