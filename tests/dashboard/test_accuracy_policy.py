from __future__ import annotations

import json

import httpx
import pytest

import tradedesk.dashboard.app as dashboard_app
from tradedesk.dashboard import DashboardState, create_app


@pytest.mark.asyncio
async def test_accuracy_policy_exposes_live_gate_and_shadow_detectors() -> None:
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["min_win_rate"] == 0.80
    assert body["min_win_rate_wilson_lb"] == 0.70
    assert body["min_trades"] == 500
    assert body["min_oos_trades"] == 100
    assert "crypto:r_adx_thrust_tsmom_up_trend_trail" in body["research_only"]


@pytest.mark.asyncio
async def test_accuracy_selector_reports_missing_artifact_as_not_trained(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(dashboard_app, "MODELS_DIR", tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-selector")

    assert response.status_code == 200
    assert response.json()["status"] == "not_trained"


@pytest.mark.asyncio
async def test_accuracy_selector_exposes_shadow_nomination(tmp_path, monkeypatch) -> None:
    point = {
        "threshold": 0.85, "top_k": 1, "n_selected": 120,
        "active_sessions": 40, "observed_success": 0.82,
        "wilson_lower_bound": 0.74, "session_coverage": 0.50,
        "expectancy_r": 0.10, "qualified": True,
    }
    payload = {
        "version": "20261002-010000-base_breakout", "kind": "logistic",
        "trained_on": 500, "accuracy_operating_point": point,
        "accuracy_selector_curve": [point],
    }
    (tmp_path / "20261002-010000-base_breakout.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    monkeypatch.setattr(dashboard_app, "MODELS_DIR", tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-selector")

    body = response.json()
    assert body["status"] == "qualified_shadow"
    assert body["latest"]["operating_point"] == point
    assert "shadow" in body["detail"]


@pytest.mark.asyncio
async def test_accuracy_race_exposes_blockers_without_treating_them_as_pass(
    tmp_path, monkeypatch
) -> None:
    race_dir = tmp_path / "accuracy-race"
    race_dir.mkdir()
    (race_dir / "latest.json").write_text(
        json.dumps(
            {
                "created_at": "2026-10-03T00:02:55+05:30",
                "status": "blocked",
                "detail": "race blocked before fitting: cohort readiness failed",
                "cohort": {
                    "rows": 29964,
                    "sessions": 751,
                    "feature_version": "v4",
                    "economics_coverage": 0.0204,
                    "rule_score_coverage": 0.0,
                    "blockers": ["feature schema is not v4"],
                },
                "nominee": None,
                "locked_test": {"status": "not_opened"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "MODELS_DIR", tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-race")

    body = response.json()
    assert body["status"] == "blocked"
    assert body["cohort"]["economics_coverage"] == 0.0204
    assert body["nominee"] is None
    assert body["locked_test"]["status"] == "not_opened"
