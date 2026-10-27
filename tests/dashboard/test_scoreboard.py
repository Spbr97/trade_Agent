from __future__ import annotations

from pathlib import Path

import httpx

from tradedesk import profit_gate as pg
from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState


async def test_scoreboard_serves_the_written_verdicts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        empty = (await client.get("/api/scoreboard")).json()
        assert empty == {"written_at": None, "verdicts": []}

        verdict = pg.decide(
            "r_x", "bse", [], [], {"passed": True, "net_r": 0.26, "n_trades": 1091}, alpha=0.01
        )
        pg.write([verdict], twins={"bse": {"r_x": {"twins": 3, "winners": 2}}})
        board = (await client.get("/api/scoreboard")).json()
    row = board["verdicts"][0]
    assert row["status"] == "provisional" and row["quick_twin"]["winners"] == 2
    assert "rule" not in board

    html = (
        Path(__file__).parents[2] / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'data-tab="scoreboard"' in html and 'id="scoreboard-table"' in html
    assert "fetch('/api/scoreboard')" in html


async def test_the_same_day_excess_panel_serves_the_written_report(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        assert (await client.get("/api/same-day-excess")).json()["setups"] == []
        out = tmp_path / "data" / "m14_m18" / "same_day_excess"
        out.mkdir(parents=True)
        (out / "nse_bse.json").write_text(
            '{"generated_at": "2026-10-27T17:00:00+05:30", "distinct_calls": 3, "setups": '
            '[{"setup": "r_x", "n_calls": 3, "n_days": 2, "mean_excess_r": 0.1, '
            '"lower": -3.0, "upper": 8.0, "verdict": "collecting"}]}',
            encoding="utf-8",
        )
        report = (await client.get("/api/same-day-excess")).json()
    assert report["setups"][0]["verdict"] == "collecting"
    html = (
        Path(__file__).parents[2] / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'id="excess-table"' in html and "fetch('/api/same-day-excess')" in html
