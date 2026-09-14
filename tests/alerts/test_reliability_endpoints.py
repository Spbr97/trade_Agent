"""Dashboard endpoints for the reliability/confidence system (2026-09-14 request): the
top-right overall agent reliability number + its history, per-symbol confidence, and the
compact backfill P&L stat that replaced the removed "Past (backfill)" browsing table."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from tradedesk.dashboard import DashboardState, create_app


async def test_reliability_overall_and_history_endpoints(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tradedesk.reliability_sources as rs

    crypto_log = tmp_path / "crypto.jsonl"
    crypto_log.write_text(
        json.dumps({"symbol": "BTC", "label": 1, "source": "live"}) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(rs, "NSE_JOURNAL", tmp_path / "no_journal.sqlite")
    monkeypatch.setattr(rs, "CRYPTO_LOG", crypto_log)
    monkeypatch.setattr(rs, "BSE_LOG", tmp_path / "no_bse.jsonl")
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr("tradedesk.reliability.HISTORY_PATH", history_path)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        overall = (await c.get("/api/reliability/overall")).json()
        assert overall["n"] == 1 and overall["wins"] == 1
        assert overall["by_market"]["crypto"]["wins"] == 1

        assert (await c.get("/api/reliability/history")).json() == []
        rs.log_daily_reliability_snapshot(history_path)
        history = (await c.get("/api/reliability/history")).json()
        assert len(history) == 1 and history[0]["n"] == 1


async def test_reliability_symbols_endpoint_filters_by_source(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tradedesk.analysis as an

    crypto_log = tmp_path / "crypto.jsonl"
    rows = [
        {"symbol": "BTC", "label": 1, "source": "backfill"},
        {"symbol": "BTC", "label": 1, "source": "live"},
        {"symbol": "ETH", "label": 0, "source": "live"},
    ]
    crypto_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(an, "CRYPTO_LOG", crypto_log)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        pooled = (await c.get("/api/reliability/symbols", params={"market": "crypto"})).json()
        btc = next(s for s in pooled if s["symbol"] == "BTC")
        assert btc["n"] == 2 and btc["wins"] == 2

        live_only = (
            await c.get(
                "/api/reliability/symbols", params={"market": "crypto", "source": "live"}
            )
        ).json()
        btc_live = next(s for s in live_only if s["symbol"] == "BTC")
        assert btc_live["n"] == 1


async def test_reliability_backfill_pnl_endpoint(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tradedesk.analysis as an

    bse_log = tmp_path / "bse.jsonl"
    rows = [
        {"symbol": "SENSEX", "label": 1, "source": "backfill", "r_multiple": 2.0, "resolved_at": "2026-01-01T00:00:00"},  # noqa: E501
        {"symbol": "SENSEX", "label": 0, "source": "backfill", "r_multiple": -1.0, "resolved_at": "2026-01-02T00:00:00"},  # noqa: E501
    ]
    bse_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(an, "BSE_LOG", bse_log)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        summary = (await c.get("/api/reliability/backfill-pnl", params={"market": "bse"})).json()
        assert summary["n"] == 2
        assert summary["sum_r"] == 1.0
