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

    @app.get("/api/crypto/calls")
    async def api_crypto_calls(limit: int = 50) -> JSONResponse:
        """Crypto's own call log (data/reports/crypto_signal_tracking.jsonl) - kept as a
        separate dataset from NSE's paper-book /api/eod on purpose (different market,
        different costs, no shared population to pool)."""
        from tradedesk.analysis import CRYPTO_LOG

        if not CRYPTO_LOG.exists():
            return JSONResponse([])
        rows = [
            json.loads(line)
            for line in CRYPTO_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows.sort(key=lambda r: r["logged_at"], reverse=True)
        return JSONResponse(rows[:limit])

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
