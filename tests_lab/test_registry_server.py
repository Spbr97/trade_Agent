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
        assert (await client.get("/api/accuracy-milestone")).json() == {
            "available": False,
            "eligible_for_live": False,
            "status": "awaiting_frozen_baseline",
        }
        assert (await client.get("/api/decisions?market=nse")).json()["as_of"] == "2026-09-15"
        assert (await client.get("/api/decisions?market=../../other")).status_code == 422
        assert (await client.post("/api/summary")).status_code == 405
    assert not output.exists()
    assert saved.read_bytes() == original


@pytest.mark.asyncio
async def test_accuracy_milestone_exposes_compact_readonly_baseline(tmp_path):
    output = tmp_path / "lab"
    identifier = "a" * 32
    target = output / "aem_staged/datasets" / identifier
    target.mkdir(parents=True)
    (output / "aem_staged/latest.json").write_text(json.dumps({"id": identifier}), encoding="utf-8")
    manifest = {
        "id": identifier,
        "created_at": "2026-09-24T12:00:00+00:00",
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "plan_id": "frozen-plan",
        "symbols_with_daily_and_m1": 1,
        "evaluation_sessions": 2,
        "events": 4,
        "resolved_trades": 3,
        "strict_success_rate": 2 / 3,
        "mean_net_r": 0.1,
        "accuracy_protocol": {
            "version": "aem-accuracy-v1",
            "minimum_eligibility_observed_rate": 0.8,
            "minimum_prospective_resolved": 100,
            "minimum_prospective_active_sessions": 30,
            "minimum_prospective_observed_rate": 0.8,
            "minimum_prospective_wilson95_lower": 0.7,
            "minimum_random_advantage_r": 0.1,
            "reported_top_k_policies": [1, 2, 3],
        },
        "accuracy_protocol_sha256": "protocol-hash",
        "source": {
            "requests_recorded": 2,
            "coverage": {
                "NSE_1": {
                    "complete_sessions": 1,
                    "missing_or_incomplete_sessions": ["2026-09-02"],
                }
            },
            "symbols": {"NSE_1": {"minute": {"rows": 729}}},
        },
        "audit": {"incomplete_session": 1},
        "diagnostics": {
            "overall": {
                "trade_decisions": 4,
                "resolved_trades": 3,
                "unfilled_decisions": 1,
                "unresolved_decisions": 0,
                "strict_successes": 2,
                "strict_success_rate": 2 / 3,
                "strict_success_wilson95": {"lower": 0.2, "upper": 0.9},
                "mean_net_r": 0.1,
            },
            "session_coverage": {
                "evaluation_sessions": 2,
                "active_sessions": 1,
                "fully_resolved_active_sessions": 1,
                "zero_trade_decision_sessions": 1,
                "active_sessions_at_least_70pct": 1.0,
                "active_sessions_at_least_80pct": 1.0,
            },
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(tmp_path, output)), base_url="http://test"
    ) as client:
        response = await client.get("/api/accuracy-milestone")
        assert response.status_code == 200
        result = response.json()
        assert result["collection"]["collected_rows"] == 729
        assert result["collection"]["expected_rows"] == 750
        assert result["collection"]["exceptions"] == {"NSE_1": ["2026-09-02"]}
        assert result["baseline"]["strict_success_rate"] == pytest.approx(2 / 3)
        assert len(result["scorecard"]["core"]) == 12
        assert len(result["scorecard"]["promotion_gates"]) == 4
        assert result["scorecard"]["promotion_gates"]["stress_economics"]["status"] == "pending"
        assert result["validation"] is None
        assert result["eligible_for_live"] is False
        assert (await client.post("/api/accuracy-milestone")).status_code == 405


@pytest.mark.asyncio
async def test_accuracy_milestone_uses_only_matching_completed_validation(tmp_path):
    output = tmp_path / "lab"
    identifier = "a" * 32
    target = output / "aem_staged/datasets" / identifier
    target.mkdir(parents=True)
    (output / "aem_staged/latest.json").write_text(json.dumps({"id": identifier}))
    # Reuse the compact baseline fixture produced by the preceding test's schema.
    manifest = {
        "id": identifier,
        "created_at": "2026-09-24T12:00:00+00:00",
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "plan_id": "plan",
        "symbols_with_daily_and_m1": 1,
        "evaluation_sessions": 1,
        "events": 1,
        "resolved_trades": 1,
        "strict_success_rate": 0.0,
        "mean_net_r": -1.0,
        "accuracy_protocol": {
            "version": "aem-accuracy-v1",
            "minimum_eligibility_observed_rate": 0.8,
            "minimum_prospective_resolved": 100,
            "minimum_prospective_active_sessions": 30,
            "minimum_prospective_observed_rate": 0.8,
            "minimum_prospective_wilson95_lower": 0.7,
            "minimum_random_advantage_r": 0.1,
            "reported_top_k_policies": [1, 2, 3],
        },
        "accuracy_protocol_sha256": "protocol",
        "source": {
            "requests_recorded": 1,
            "coverage": {"NSE_1": {"complete_sessions": 1, "missing_or_incomplete_sessions": []}},
            "symbols": {"NSE_1": {"minute": {"rows": 375}}},
        },
        "audit": {},
        "diagnostics": {
            "overall": {
                "trade_decisions": 1,
                "resolved_trades": 1,
                "unfilled_decisions": 0,
                "unresolved_decisions": 0,
                "strict_successes": 0,
                "strict_success_rate": 0.0,
                "strict_success_wilson95": {"lower": 0.0, "upper": 0.8},
                "mean_net_r": -1.0,
            },
            "session_coverage": {
                "evaluation_sessions": 1,
                "active_sessions": 1,
                "fully_resolved_active_sessions": 1,
                "zero_trade_decision_sessions": 0,
                "active_sessions_at_least_70pct": 0.0,
                "active_sessions_at_least_80pct": 0.0,
            },
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest))
    validation = {
        "id": "v" * 32,
        "dataset_id": identifier,
        "status": "historical_diagnostic_only",
        "matched_random": {
            "comparison": {"comparison": {"cohort_count": 25, "actual_minus_null_mean_net_r": 0.2}}
        },
        "stress": {"minimum_mean_net_r": -1.2},
        "portfolio": {"selected_fills": 1, "mean_net_r_after_constraints": -1.0},
        "scorecard": {
            "core": {},
            "promotion_gates": {"matched_random_advantage": {"status": "pass"}},
            "all_promotion_gates_pass": False,
        },
    }
    report_path = output / "aem_staged_validation/runs/run/report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(validation))
    (output / "aem_staged_validation/latest.json").write_text(
        json.dumps({"dataset_id": identifier, "path": str(report_path)})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(tmp_path, output)), base_url="http://test"
    ) as client:
        result = (await client.get("/api/accuracy-milestone")).json()
    assert result["validation"]["cohorts"] == 25
    assert result["validation"]["minimum_stress_mean_net_r"] == -1.2
    assert result["scorecard"] == validation["scorecard"]
    assert result["next_gate"] == "accuracy_improvement_experiments"
