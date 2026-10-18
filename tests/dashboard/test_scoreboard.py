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
