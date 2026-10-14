from __future__ import annotations

import json
from pathlib import Path

import httpx

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState


async def test_filings_and_mover_study_are_read_only_and_fail_soft(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        filings = (await client.get("/api/filings")).json()
        missing = (await client.get("/api/mover-study?market=nse")).json()
        report = tmp_path / "data/m14_m18/mover_study/bse/latest.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({"market": "bse", "latest_observation": {}}), "utf-8")
        present = (await client.get("/api/mover-study?market=bse")).json()
        bad = await client.get("/api/mover-study?market=nasdaq")
    assert filings["material"] == [] and filings["authority"] == "watch_only"
    assert missing["status"] == "not_run_yet"
    assert present["market"] == "bse"
    assert bad.status_code == 422

    html = (
        Path(__file__).parents[2] / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="filings-table"' in html and 'id="mover-study-table"' in html
    assert "${esc(brief ? short(f.desc || f.text) : (f.text || f.desc))}" in html  # escaped
    assert 'data-tab="movers"' in html and 'id="tab-movers"' in html


async def test_health_and_news_trial_endpoints_fail_soft(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        health = (await client.get("/api/health")).json()
        news = (await client.get("/api/news-trial")).json()
        (tmp_path / "data/reports").mkdir(parents=True)
        (tmp_path / "data/reports/health.json").write_text(
            json.dumps({"status": "failing", "failing": [{"what": "x", "detail": "y"}]}), "utf-8"
        )
        failing = (await client.get("/api/health")).json()
    assert health["status"] == "not_checked_yet" and health["failing"] == []
    assert news["candidates"] == [] and news["enabled"] == []
    assert failing["failing"][0]["what"] == "x"
