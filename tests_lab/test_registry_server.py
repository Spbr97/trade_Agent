import json

import httpx
import pytest
from tradedesk_lab.registry import Registry
from tradedesk_lab.server import create_app


def test_registry_candidates_and_holdout_reservation(tmp_path):
    path = tmp_path / "registry.sqlite"
    with Registry(path) as r:
        run = r.begin({"market": "nse"})
        a = r.candidate(run, "logistic", {"C": 1}, {"brier": 0.2})
        b = r.candidate(run, "logistic", {"C": 2}, {"brier": 0.1})
        assert a != b and r.best()["id"] == b
        assert len(r.run(run)["candidates"]) == 2
        r.reserve_holdout("nse:2026-01-01:2026-03-01", run)
        with pytest.raises(ValueError):
            r.reserve_holdout("nse:2026-01-01:2026-03-01", "another-run")
        with pytest.raises(ValueError):
            r.best("brier; DROP TABLE candidates")
        r.finish(run, {"result": "NO_TRADE"})
    with Registry(path, readonly=True) as r:
        assert r.run(run)["report"]["result"] == "NO_TRADE"


@pytest.mark.asyncio
async def test_preview_empty_and_calls_are_readonly(tmp_path):
    output = tmp_path / "lab"
    folder = tmp_path / "data/watchlists"
    folder.mkdir(parents=True)
    saved = folder / "2026-09-15.json"
    saved.write_text(
        json.dumps({"on": "2026-09-15", "generated_at": "2026-09-15T16:00:00", "entries": []}),
        encoding="utf-8",
    )
    original = saved.read_bytes()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(tmp_path, output)), base_url="http://test"
    ) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/api/summary")).json()["report"] is None
        assert (await client.get("/api/research/agreement")).status_code == 404
        assert (await client.get("/api/forward")).json()["activation"] is None
        assert (await client.get("/api/decisions?market=nse")).json()["as_of"] == "2026-09-15"
        assert (await client.get("/api/decisions?market=../../other")).status_code == 422
        assert (await client.post("/api/summary")).status_code == 405
    assert not output.exists()
    assert saved.read_bytes() == original
