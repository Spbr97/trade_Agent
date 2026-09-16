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
