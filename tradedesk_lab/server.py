"""M18: independent localhost preview. GET-only; no production dashboard imports."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

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
