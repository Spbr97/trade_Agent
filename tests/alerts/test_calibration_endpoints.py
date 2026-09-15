"""Dashboard endpoints for "how close and how successful are our predictions" (2026-09-15
request): the current calibration snapshot and its day-by-day history, both built on
prediction/calibration.py::drift_check() - already the thing `tradedesk ml check-drift` runs
daily, now made visible rather than a background pass/fail."""

from __future__ import annotations

from pathlib import Path

import httpx

from tradedesk.dashboard import DashboardState, create_app
from tradedesk.prediction.predict import log_shadow


async def test_ml_calibration_endpoint_reads_real_shadow_log(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001, E501
    import tradedesk.prediction.calibration as calib

    shadow_log = tmp_path / "shadow.jsonl"
    log_shadow(shadow_log, "s0", 0.65, "v1")
    monkeypatch.setattr(calib, "SHADOW_LOG_PATH", shadow_log)
    monkeypatch.setattr(calib, "NSE_JOURNAL_PATH", tmp_path / "no_journal.sqlite")

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = (await c.get("/api/ml-calibration")).json()
        assert r["n_logged"] == 1
        assert r["n_resolved_checked"] == 0  # no journal -> nothing resolved yet
        assert r["paused"] is False
        assert r["buckets"] == []


async def test_ml_calibration_history_endpoint(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    import tradedesk.prediction.calibration as calib

    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr(calib, "CALIBRATION_HISTORY_PATH", history_path)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/ml-calibration/history")).json() == []

        calib.log_daily_calibration_snapshot(
            shadow_log=tmp_path / "no_shadow.jsonl",
            journal_path=tmp_path / "no_journal.sqlite",
            history_path=history_path,
        )
        history = (await c.get("/api/ml-calibration/history")).json()
        assert len(history) == 1 and history[0]["n_logged"] == 0
