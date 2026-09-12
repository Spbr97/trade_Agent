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


def create_app(state: DashboardState, journal_path: Path | None = None) -> FastAPI:
    """`journal_path` is optional and keyword-only-by-convention so every existing caller
    (both CLI commands, and tests/alerts's `create_app(state)`) is unaffected; pass it to
    light up /api/eod and /api/performance, which read the paper book directly rather than
    through DashboardState (that stays purely the in-memory live-session state pushed over
    SSE - EOD/weekly/monthly are persisted history, a different kind of read)."""
    app = FastAPI(title="tradedesk", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(state.snapshot())

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

    @app.get("/chart")
    async def chart(path: str) -> Any:
        p = Path(path)
        if not p.exists() or p.suffix.lower() != ".png":
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="image/png")

    @app.get("/events")
    async def events() -> StreamingResponse:
        q = state.subscribe()

        async def gen() -> AsyncIterator[bytes]:
            try:
                yield _sse(state.snapshot())
                while True:
                    try:
                        snap = await asyncio.wait_for(q.get(), timeout=15)
                        yield _sse(snap)
                    except TimeoutError:
                        yield b": keepalive\n\n"
            finally:
                state.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def _sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


async def serve(app: FastAPI, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
