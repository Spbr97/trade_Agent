"""dashboard/write_guard.py: anything that changes state and arrives through the Cloudflare
tunnel needs the keyring-held token; a browser on the PC itself never does."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import tradedesk.dashboard.write_guard as write_guard
import tradedesk.my_picks as my_picks
from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState

TUNNEL = {"cf-connecting-ip": "203.0.113.7", "cf-ray": "abc123-BOM"}
PICK = {"market": "nse", "symbol": "TARIL", "direction": "up"}


@pytest.mark.parametrize(
    ("host", "headers", "local"),
    [
        ("127.0.0.1", {}, True),
        ("::1", {}, True),
        ("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"}, False),  # cloudflared, loopback
        ("127.0.0.1", {"x-forwarded-for": "203.0.113.7"}, False),
        ("192.168.1.20", {}, False),
        (None, {}, False),
    ],
)
def test_only_loopback_without_tunnel_headers_counts_as_local(host, headers, local) -> None:
    assert write_guard.is_direct_local(host, headers) is local


def _client(tmp_path: Path, monkeypatch, token: str | None) -> httpx.AsyncClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(write_guard, "load_token", lambda: token)
    monkeypatch.setattr(my_picks, "resolve_symbol", lambda market, symbol: ("NSE_15174", "TARIL"))
    app = create_app(DashboardState())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_tunnel_writes_are_refused_while_no_token_is_set(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch, None) as client:
        refused = await client.post("/api/my-picks", json=PICK, headers=TUNNEL)
        local = await client.post("/api/my-picks", json=PICK)
    assert refused.status_code == 401 and refused.json()["token_required"] is True
    assert "dashboard-token" in refused.json()["error"]
    assert local.status_code == 200


async def test_tunnel_writes_need_the_exact_token(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch, "s3cret-token") as client:
        missing = await client.post("/api/my-picks", json=PICK, headers=TUNNEL)
        wrong = await client.post(
            "/api/my-picks", json=PICK, headers={**TUNNEL, "x-dashboard-token": "guess"}
        )
        right = await client.post(
            "/api/my-picks", json=PICK, headers={**TUNNEL, "x-dashboard-token": "s3cret-token"}
        )
        review = await client.post(
            "/api/review/decide?item_id=x&status=approved", headers=TUNNEL
        )
        read = await client.get("/api/my-picks", headers=TUNNEL)
    assert missing.status_code == 401 and wrong.status_code == 401
    assert right.status_code == 200 and right.json()["symbol"] == "TARIL"
    assert review.status_code == 401  # the guard is route-agnostic: review approvals too
    assert read.status_code == 200  # reads are unchanged


def test_page_sends_the_token_on_writes_and_asks_for_it_once() -> None:
    html = (
        Path(__file__).parents[2] / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert "headers.set('X-Dashboard-Token', token)" in html
    assert "body.token_required" in html
