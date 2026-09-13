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

    @app.get("/api/review")
    async def api_review_list() -> JSONResponse:
        """Pending/decided review-queue items (review_queue.py) - proposals from `tradedesk
        review week`, waiting for a human decision. Never auto-applied; see the module
        docstring for why that's a hard rule, not a missing feature."""
        from tradedesk.review_queue import load_queue

        items = sorted(load_queue().values(), key=lambda i: i.created_at, reverse=True)
        return JSONResponse([vars(i) for i in items])

    @app.post("/api/review/decide")
    async def api_review_decide(item_id: str, status: str) -> JSONResponse:
        from tradedesk.review_queue import decide

        try:
            item = decide(item_id, status)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if item is None:
            return JSONResponse({"error": f"no review item {item_id!r}"}, status_code=404)
        return JSONResponse(vars(item))

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

    return app


def _sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


async def serve(app: FastAPI, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
