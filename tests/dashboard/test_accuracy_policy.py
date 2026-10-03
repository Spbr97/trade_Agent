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
                "candidates": [
                    {
                        "kind": "logistic_balanced",
                        "oos_rows": 13305,
                        "operating_point": None,
                        "best_adequately_sampled": {
                            "n_selected": 241,
                            "observed_success": 0.3237,
                        },
                        "best_policy_coverage": {
                            "n_selected": 476,
                            "observed_success": 0.3067,
                            "wilson_lower_bound": 0.2670,
                            "session_coverage": 0.4521,
                        },
                    }
                ],
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
    assert body["candidates"][0]["best_policy_coverage"]["n_selected"] == 476
    assert body["nominee"] is None
    assert body["locked_test"]["status"] == "not_opened"


@pytest.mark.asyncio
async def test_crypto_universe_endpoint_distinguishes_not_run_from_zero(
    tmp_path, monkeypatch
) -> None:
    report = tmp_path / "crypto-universe.json"
    monkeypatch.setattr(dashboard_app, "CRYPTO_UNIVERSE_REPORT", report)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/api/crypto/universe")
        report.write_text(
            json.dumps(
                {
                    "active_inr_pairs": 338,
                    "scanned_pairs": 337,
                    "pairs_with_closed_session": 336,
                    "fetch_errors": 2,
                    "signals_detected": 3,
                    "tradeable_signals": 0,
                    "session": "2026-10-02",
                }
            ),
            encoding="utf-8",
        )
        complete = await client.get("/api/crypto/universe")

    assert missing.json()["status"] == "not_run"
    assert complete.json()["status"] == "complete"
    assert complete.json()["active_inr_pairs"] == 338
    assert complete.json()["tradeable_signals"] == 0


@pytest.mark.asyncio
async def test_accuracy_geometry_exposes_development_without_promoting_diagnostic(
    tmp_path, monkeypatch
) -> None:
    folder = tmp_path / "accuracy-geometry"
    folder.mkdir()
    payload = {
        "status": "abstain",
        "detail": "no quick-profit geometry cleared every development gate",
        "development": {"rows": 17908, "sessions": 600},
        "candidates": [
            {
                "entry_mode": "next_session_open",
                "stop_atr": 1.0,
                "target_r": 0.5,
                "max_hold": 3,
                "n_selected": 17264,
                "observed_success": 0.7393,
                "wilson_lower_bound": 0.7327,
                "expectancy_r": -0.0764,
                "diagnostics": {
                    "setup": [
                        {
                            "value": "trend_pullback",
                            "n": 1811,
                            "accuracy": 0.7935,
                            "wilson_lower_bound": 0.7742,
                            "expectancy_r": 0.0331,
                        }
                    ]
                },
            }
        ],
        "nominee": None,
        "locked_test": {"status": "not_opened_no_nominee", "rows": 4848},
    }
    (folder / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(dashboard_app, "MODELS_DIR", tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-geometry")

    body = response.json()
    assert body["status"] == "abstain"
    assert body["best"]["observed_success"] == 0.7393
    assert body["best_positive_setup_diagnostic"]["accuracy"] == 0.7935
    assert body["nominee"] is None
    assert body["locked_test"]["status"] == "not_opened_no_nominee"
