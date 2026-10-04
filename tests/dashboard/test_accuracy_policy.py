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
async def test_crypto_accuracy_program_keeps_market_evidence_separate(
    tmp_path, monkeypatch
) -> None:
    state_path = tmp_path / "crypto-accuracy.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "baseline_frozen_research_only",
                "checkpoint": "C0",
                "source_integrity": {"evidence_mixed": False},
                "live_forward": {"resolved_calls": 47, "wins": 5, "accuracy": 5 / 47},
                "historical_backfill": {"resolved_calls": 461, "wins": 68},
                "qualification": {"eligible_for_live": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "CRYPTO_ACCURACY_STATE", state_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(DashboardState())),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/crypto/accuracy-program")

    body = response.json()
    assert body["live_forward"]["resolved_calls"] == 47
    assert body["historical_backfill"]["resolved_calls"] == 461
    assert body["source_integrity"]["evidence_mixed"] is False
    assert body["qualification"]["eligible_for_live"] is False


@pytest.mark.asyncio
async def test_crypto_accuracy_timing_is_forward_only_and_setup_separated(
    tmp_path, monkeypatch
) -> None:
    state_path = tmp_path / "crypto-timing.json"
    state_path.write_text(
        json.dumps(
            {
                "activation": {
                    "activated_at": "2026-10-04T00:00:00+00:00",
                    "existing_live_calls": 54,
                    "forward_only": True,
                },
                "summary": {
                    "status": "collecting_insufficient_evidence",
                    "registered_calls": 1,
                    "setups_monitored": 1,
                    "qualified_setups": [],
                    "evidence_pooled_across_setups": False,
                    "eligible_for_live": False,
                },
                "source_integrity": {"passed": True, "errors": 0},
                "records": [
                    {
                        "signal_id": "x",
                        "scrip_code": "CDX_XINR",
                        "symbol": "XINR",
                        "setup": "test_setup",
                        "armed_on": "2026-10-04",
                        "assigned_at": "2026-10-04T12:00:00+00:00",
                        "prospective_eligible": True,
                        "cohort_offsets": ["large-payload-must-not-leak"],
                        "timings": ["large-payload-must-not-leak"],
                    }
                ],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "CRYPTO_ACCURACY_TIMING", state_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/crypto/accuracy-timing")

    body = response.json()
    assert body["activation"]["forward_only"] is True
    assert body["summary"]["evidence_pooled_across_setups"] is False
    assert body["summary"]["eligible_for_live"] is False
    assert body["source_integrity"]["passed"] is True
    assert "cohort_offsets" not in body["records"][0]
    assert "timings" not in body["records"][0]


@pytest.mark.asyncio
async def test_crypto_accuracy_dataset_is_compact_and_never_claims_live_readiness(
    tmp_path, monkeypatch
) -> None:
    dataset = tmp_path / "dataset.json"
    universe = tmp_path / "universe.json"
    dataset.write_text(
        json.dumps(
            {
                "id": "frozen-1",
                "status": "not_ready_collecting_point_in_time_history",
                "latest_closed_session": "2026-10-02",
                "membership": {
                    "pre_activation": "unknown_not_inferred",
                    "point_in_time_sessions": 0,
                    "minimum_point_in_time_sessions": 30,
                    "required_active_pairs": 337,
                    "pairs_meeting_minimum_sessions": 0,
                    "minimum_pair_point_in_time_sessions": 1,
                },
                "coverage_summary": {
                    "materialized_pairs": 339,
                    "closed_daily_rows": 356183,
                    "duplicate_daily_rows": 876,
                    "label_rows": 1000000,
                    "label_statuses": {"resolved": 0, "excluded": 1000000},
                },
                "coverage": [{"large": "payload-must-not-leak"}],
                "contract": {"geometries": [{"name": "quick"}]},
                "source_integrity": {"passed": True, "errors": []},
                "baseline_improved": False,
                "eligible_for_live": False,
            }
        ),
        encoding="utf-8",
    )
    universe.write_text(
        json.dumps(
            {
                "status": "collecting",
                "observations": 1,
                "current_active_pairs": 339,
                "membership_before_activation": "unknown_not_inferred",
                "pairs": {"large": "payload-must-not-leak"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "CRYPTO_ACCURACY_DATASET", dataset)
    monkeypatch.setattr(dashboard_app, "CRYPTO_UNIVERSE_STATE", universe)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/crypto/accuracy-dataset")

    body = response.json()
    assert body["coverage_summary"]["materialized_pairs"] == 339
    assert body["coverage_summary"]["duplicate_daily_rows"] == 876
    assert body["coverage_summary"]["label_statuses"]["resolved"] == 0
    assert body["membership"]["pre_activation"] == "unknown_not_inferred"
    assert body["membership"]["minimum_pair_point_in_time_sessions"] == 1
    assert body["membership"]["pairs_meeting_minimum_sessions"] == 0
    assert body["membership"]["required_active_pairs"] == 337
    assert body["source_integrity"]["passed"] is True
    assert body["eligible_for_live"] is False
    assert body["baseline_improved"] is False
    assert "coverage" not in body
    assert "pairs" not in body["universe"]


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


@pytest.mark.asyncio
async def test_setup_stability_distinguishes_historical_pass_from_prospective(
    tmp_path, monkeypatch
) -> None:
    folder = tmp_path / "accuracy-setup-stability"
    folder.mkdir()
    point = {
        "n_selected": 647,
        "observed_success": 0.813,
        "wilson_lower_bound": 0.781,
        "expectancy_r": 0.056,
        "qualified": True,
    }
    payload = {
        "status": "locked_pass",
        "hypotheses": [
            {
                "hypothesis": {"name": "primary_trend_pullback"},
                "stability_pass": True,
                "aggregate": {"accuracy": 0.7935},
                "selector": {"status": "qualified_development"},
            }
        ],
        "nominee": {
            "hypothesis": {"name": "primary_trend_pullback"},
            "development_operating_point": point,
        },
        "locked_test": {
            "status": "locked_pass",
            "n_selected": 223,
            "observed_success": 0.8341,
            "wilson_lower_bound": 0.7797,
            "expectancy_r": 0.0829,
            "qualified": True,
        },
    }
    (folder / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(dashboard_app, "MODELS_DIR", tmp_path)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-setup-stability")

    body = response.json()
    assert body["status"] == "locked_pass"
    assert body["development_operating_point"]["observed_success"] == 0.813
    assert body["locked_test"]["observed_success"] == 0.8341
    assert body["evidence_level"] == "historical_locked_pass_prospective_pending"


@pytest.mark.asyncio
async def test_prospective_shadow_reports_fresh_evidence_without_live_authority(
    tmp_path, monkeypatch
) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "activation": {
                    "forward_after": "2026-10-02",
                    "candidate": {
                        "probability_threshold": 0.55,
                        "top_k_per_arming_session": 2,
                    },
                },
                "summary": {
                    "status": "collecting_insufficient_evidence",
                    "selected_calls": 4,
                    "resolved_calls": 2,
                    "eligible_for_live": False,
                },
                "records": [{"signal_id": "x", "features": {"adx14": 25.0}}],
                "current_errors": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "ACCURACY_PROSPECTIVE_STATE", state)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-prospective-shadow")

    body = response.json()
    assert body["status"] == "collecting_insufficient_evidence"
    assert body["summary"]["eligible_for_live"] is False
    assert body["activation"]["forward_after"] == "2026-10-02"
    assert "features" not in body["records"][0]


@pytest.mark.asyncio
async def test_prospective_monitor_is_read_only_and_surfaces_integrity(
    tmp_path, monkeypatch
) -> None:
    report = tmp_path / "latest.json"
    report.write_text(
        json.dumps(
            {
                "integrity": {"status": "healthy", "passed": True, "failures": []},
                "audit": {"events": 1},
                "availability": {"observed_sessions": 0},
                "uncertainty": {"session_cluster_lower_95": None},
                "double_slippage_stress": {"status": "insufficient_evidence"},
                "review_ready": False,
                "eligible_for_live": False,
                "authority": "read_only_evidence_monitor",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "ACCURACY_PROSPECTIVE_MONITOR", report)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-prospective-monitor")

    body = response.json()
    assert body["status"] == "healthy"
    assert body["review_ready"] is False
    assert body["eligible_for_live"] is False
    assert body["authority"] == "read_only_evidence_monitor"


@pytest.mark.asyncio
async def test_prospective_control_exposes_compact_selection_evidence(
    tmp_path, monkeypatch
) -> None:
    state = tmp_path / "control.json"
    state.write_text(
        json.dumps(
            {
                "version": "accuracy-prospective-control-v1",
                "registered_at": "2026-10-04T00:00:00+00:00",
                "seed": 20261004,
                "n_cohorts": 1000,
                "scope": "matched_random_candidate_selection_not_random_entry_timing",
                "summary": {
                    "status": "collecting_insufficient_evidence",
                    "resolved_model_calls": 2,
                    "mature_sessions": 1,
                    "selection_advantage_r": 0.2,
                    "satisfies_broader_random_timing_gate": False,
                    "eligible_for_live": False,
                },
                "outcome_parity": {"checked_selected_calls": 2, "passed": True},
                "sessions": [
                    {
                        "armed_on": "2026-10-03",
                        "assigned_at": "2026-10-03T12:00:00+00:00",
                        "score_deadline": "2026-10-04T03:45:00+00:00",
                        "prospective_eligible": True,
                        "candidate_count": 10,
                        "model_call_count": 2,
                        "cohort_assignments": [["large-payload-must-not-leak"]],
                    }
                ],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "ACCURACY_PROSPECTIVE_CONTROL", state)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-prospective-control")

    body = response.json()
    assert body["status"] == "collecting_insufficient_evidence"
    assert body["summary"]["eligible_for_live"] is False
    assert body["summary"]["satisfies_broader_random_timing_gate"] is False
    assert body["outcome_parity"]["passed"] is True
    assert "cohort_assignments" not in body["sessions"][0]


@pytest.mark.asyncio
async def test_prospective_timing_exposes_compact_same_stock_evidence(
    tmp_path, monkeypatch
) -> None:
    state = tmp_path / "timing.json"
    state.write_text(
        json.dumps(
            {
                "version": "accuracy-prospective-timing-v1",
                "registered_at": "2026-10-05T00:00:00+00:00",
                "seed": 20261005,
                "n_cohorts": 1000,
                "offset_sessions": [1, 20],
                "scope": "prospective_same_stock_random_future_session_timing",
                "summary": {
                    "status": "collecting_insufficient_evidence",
                    "paired_resolved_calls": 2,
                    "active_sessions": 1,
                    "is_broader_random_timing_control": True,
                    "random_timing_gate_passed": False,
                    "eligible_for_live": False,
                },
                "source_integrity": {"passed": True, "errors": 0},
                "records": [
                    {
                        "signal_id": "x",
                        "scrip_code": "NSE_X",
                        "symbol": "X",
                        "armed_on": "2026-10-05",
                        "assigned_at": "2026-10-05T12:00:00+00:00",
                        "score_deadline": "2026-10-06T03:45:00+00:00",
                        "prospective_eligible": True,
                        "cohort_offsets": ["large-payload-must-not-leak"],
                        "placebos": ["large-payload-must-not-leak"],
                    }
                ],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_app, "ACCURACY_PROSPECTIVE_TIMING", state)
    app = create_app(DashboardState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-prospective-timing")

    body = response.json()
    assert body["status"] == "collecting_insufficient_evidence"
    assert body["summary"]["is_broader_random_timing_control"] is True
    assert body["summary"]["random_timing_gate_passed"] is False
    assert body["source_integrity"]["passed"] is True
    assert "cohort_offsets" not in body["records"][0]
    assert "placebos" not in body["records"][0]
