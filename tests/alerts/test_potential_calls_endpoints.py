"""Dashboard endpoints for "potential calls" (2026-09-15 request): NSE's own call log (the
first time NSE has had forward tracking of evaluated-but-not-triggered candidates, mirroring
crypto/BSE's signal_tracker.py), a per-market summary for the Report tab, and the full day's
session-report text behind a "click to see the whole picture" expansion."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx

from tradedesk.dashboard import DashboardState, create_app


async def test_nse_calls_endpoint_reads_the_real_log(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001, E501
    import tradedesk.analysis as an

    log = tmp_path / "nse.jsonl"
    log.write_text(
        json.dumps(
            {"symbol": "SBIN", "setup": "trend_pullback", "logged_at": "2026-09-15T10:00:00"}
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(an, "NSE_LOG", log)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        rows = (await c.get("/api/nse/calls")).json()
        assert len(rows) == 1 and rows[0]["symbol"] == "SBIN"


async def test_potential_calls_summary_covers_all_three_markets(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001, E501
    import tradedesk.analysis as an

    today = date.today().isoformat()
    nse_log = tmp_path / "nse.jsonl"
    nse_log.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"symbol": "A", "logged_at": f"{today}T09:00:00", "outcome": "target", "label": 1},  # noqa: E501
                {"symbol": "B", "logged_at": f"{today}T09:00:00", "outcome": "stop", "label": 0},
                {"symbol": "C", "logged_at": f"{today}T09:00:00"},  # unresolved
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    empty_log = tmp_path / "empty.jsonl"
    monkeypatch.setattr(an, "NSE_LOG", nse_log)
    monkeypatch.setattr(an, "CRYPTO_LOG", empty_log)
    monkeypatch.setattr(an, "BSE_LOG", empty_log)

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = (await c.get("/api/potential-calls/summary")).json()
        assert r["nse"]["logged_today"] == 3
        assert r["nse"]["n_total"] == 3
        assert r["nse"]["n_resolved"] == 2
        assert r["nse"]["win_rate"] == 0.5
        assert r["nse"]["evidence_classes"] == {
            "qualified_call": 3,
            "shadow_call": 0,
            "rejected_call": 0,
        }
        assert r["nse"]["outcome_states"] == {
            "pending_call": 1,
            "resolved_call": 2,
            "invalid_call": 0,
            "never_triggered": 0,
        }
        assert r["nse"]["prediction_ledger"] == {
            "sealed": 0,
            "legacy_unsealed": 3,
        }
        assert r["nse"]["contract_performance"]["legacy"] == {
            "resolved": 2,
            "successes": 1,
            "mean_net_r": None,
        }
        empty = {
            "logged_today": 0,
            "n_total": 0,
            "n_resolved": 0,
            "win_rate": None,
            "evidence_classes": {
                "qualified_call": 0,
                "shadow_call": 0,
                "rejected_call": 0,
            },
            "outcome_states": {
                "pending_call": 0,
                "resolved_call": 0,
                "invalid_call": 0,
                "never_triggered": 0,
            },
            "contracts": {"legacy": 0, "quick_profit": 0, "swing": 0},
            "prediction_ledger": {"sealed": 0, "legacy_unsealed": 0},
            "contract_performance": {
                "legacy": {"resolved": 0, "successes": 0, "mean_net_r": None},
                "quick_profit": {"resolved": 0, "successes": 0, "mean_net_r": None},
                "swing": {"resolved": 0, "successes": 0, "mean_net_r": None},
            },
        }
        assert r["crypto"] == empty
        assert r["bse"] == empty

        failures = (await c.get("/api/failure-attribution", params={"market": "nse"})).json()
        assert failures["resolved_calls"] == 2
        assert failures["failed_calls"] == 1
        assert failures["invalid_calls_excluded"] == 0
        assert failures["categories"][0]["code"] == "unclassified_failure"

        dataset = (
            await c.get(
                "/api/learning-dataset/summary",
                params={"market": "nse", "purpose": "prospective"},
            )
        ).json()
        assert dataset["source_records"] == 3
        assert dataset["eligible_rows"] == 0
        assert dataset["exclusions"] == {"missing_signal_id": 3}


async def test_session_report_endpoint_reads_the_real_file(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001, E501
    monkeypatch.chdir(tmp_path)
    sessions_dir = tmp_path / "data" / "reports" / "nse_sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "2026-09-15.md").write_text("# NSE session report\nsome text", encoding="utf-8")  # noqa: E501

    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = (await c.get("/api/session-report", params={"market": "nse"})).json()
        assert r["date"] == "2026-09-15"
        assert "some text" in r["text"]

        r2 = (await c.get("/api/session-report", params={"market": "nse", "on": "2026-09-15"})).json()  # noqa: E501
        assert r2["date"] == "2026-09-15"

        r3 = await c.get("/api/session-report", params={"market": "bse"})
        assert r3.status_code == 404


async def test_self_learning_status_never_implies_model_change_before_refresh(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = (await client.get("/api/self-learning/status?market=nse")).json()
        challenger = (await client.get("/api/self-learning/challenger?market=nse")).json()
    assert payload["status"] == "waiting_for_first_refresh"
    assert payload["active_model_changed"] is False
    assert payload["promotion_authorized"] is False
    assert challenger["latest"] is None
    assert challenger["experiments"] == 0
    assert challenger["active_model_changed"] is False
    assert challenger["promotion_authorized"] is False
