"""Localhost dashboard (PLAN.md 7): one HTML page, JSON state, server-sent events.
Bind to 127.0.0.1 only (PLAN.md 16)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from tradedesk.dashboard.state import DashboardState

STATIC = Path(__file__).with_name("static")


def create_app(state: DashboardState) -> FastAPI:
    app = FastAPI(title="tradedesk", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(state.snapshot())

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
