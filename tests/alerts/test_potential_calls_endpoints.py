"""Dashboard endpoints for "potential calls" (2026-09-15 request): NSE's own call log (the
first time NSE has had forward tracking of evaluated-but-not-triggered candidates, mirroring
crypto/BSE's signal_tracker.py), a per-market summary for the Report tab, and the full day's
session-report text behind a "click to see the whole picture" expansion."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from tradedesk.dashboard import DashboardState, create_app
from tradedesk.dashboard.app import _self_learning_random_timing_report
from tradedesk.prediction_ledger import canonical_sha256
from tradedesk.random_timing_control import (
    CONTROL_VERSION as RANDOM_TIMING_CONTROL_VERSION,
)
from tradedesk.random_timing_control import summarize_random_timing_records


def _passing_dashboard_timing() -> dict:  # type: ignore[type-arg]
    context_sha = "c" * 64
    records = []
    for index in range(100):
        record = {
            "control_version": RANDOM_TIMING_CONTROL_VERSION,
            "market": "nse",
            "contract_version": "quick-profit-v1",
            "selector_policy": "top_1_per_session",
            "selection_context_sha256": context_sha,
            "selection_manifest_sha256": "b" * 64,
            "signal_id": f"signal-{index:03d}",
            "prediction_sha256": f"prediction-{index:03d}",
            "scrip_code": "NSE_TEST",
            "armed_on": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
            "actual_outcome_state": "resolved_call",
            "actual_outcome": "target",
            "actual_entry_on": "2026-01-02",
            "actual_exit_on": "2026-01-02",
            "actual_net_r": 0.5,
            "alternatives": [
                {
                    "rank": rank,
                    "signal_id": f"control-{rank:02d}",
                    "exit_on": "2025-12-31",
                    "net_r": 0.0,
                    "source_sha256": canonical_sha256(
                        {"control_rank": rank}
                    ),
                }
                for rank in range(1, 21)
            ],
            "source_sha256": canonical_sha256({"source": index}),
        }
        record["record_sha256"] = canonical_sha256(record)
        records.append(record)
    timing = summarize_random_timing_records(
        records,
        market="nse",
        contract_version="quick-profit-v1",
        selection_context_sha256=context_sha,
    )
    timing.update(
        evidence_mode="append_only_single_frozen_selector_terminal_cohort",
        evidence_ledger_sha256="d" * 64,
    )
    timing["replay_sha256"] = canonical_sha256(
        {key: value for key, value in timing.items() if key != "replay_sha256"}
    )
    return timing


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
        timing = (await client.get("/api/self-learning/random-timing?market=nse")).json()
        personal = (await client.get("/api/personal-calls?market=nse")).json()
    assert payload["status"] == "waiting_for_first_refresh"
    assert payload["active_model_changed"] is False
    assert payload["promotion_authorized"] is False
    assert challenger["latest"] is None
    assert challenger["experiments"] == 0
    assert challenger["active_model_changed"] is False
    assert challenger["promotion_authorized"] is False
    assert timing["challenger_gate"]["status"] == "not_registered"
    assert timing["challenger_gate"]["random_timing_gate_passed"] is False
    assert timing["eligible_for_live"] is False
    assert personal["status"] == "NO QUALIFIED PERSONAL CALL TODAY"
    assert personal["calls"] == []
    assert personal["promotion_authorized"] is False
    assert "explicit user promotion approval" in " ".join(personal["blockers"])


async def test_personal_calls_cannot_be_enabled_by_an_orphan_approval_file(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "models" / "self_learning" / "nse"
    root.mkdir(parents=True)
    (root / "promotion.json").write_text(
        json.dumps(
            {
                "status": "approved",
                "approved_by_user": True,
                "experiment_id": "does-not-exist",
                "contract_version": "quick-profit-v1",
            }
        ),
        encoding="utf-8",
    )
    app = create_app(DashboardState())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        personal = (await client.get("/api/personal-calls?market=nse")).json()
    assert personal["calls"] == []
    assert personal["promotion_authorized"] is False
    assert "no contract-specific challenger" in " ".join(personal["blockers"])


def test_random_timing_research_pass_cannot_become_challenger_pass(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data" / "m14_m18" / "accuracy_prospective_timing"
    path.mkdir(parents=True)
    (path / "state.json").write_text(
        json.dumps(
            {
                "version": "accuracy-prospective-timing-v1",
                "summary": {
                    "status": "random_timing_pass",
                    "paired_resolved_calls": 100,
                    "timing_advantage_r": 0.2,
                    "p_value": 0.01,
                },
                "source_integrity": {"passed": True, "errors": 0},
            }
        ),
        encoding="utf-8",
    )
    report = _self_learning_random_timing_report("nse")

    assert report["research_collector"]["status"] == "random_timing_pass"
    assert report["research_collector"]["summary"]["paired_resolved_calls"] == 100
    assert report["challenger_gate"]["status"] == "not_registered"
    assert report["challenger_gate"]["random_timing_gate_passed"] is False


def test_random_timing_endpoint_fails_closed_on_malformed_collector(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data" / "m14_m18" / "accuracy_prospective_timing"
    path.mkdir(parents=True)
    (path / "state.json").write_text("[]", encoding="utf-8")
    report = _self_learning_random_timing_report("nse")

    assert report["research_collector"]["status"] == "invalid"
    assert report["challenger_gate"]["random_timing_gate_passed"] is False
    with pytest.raises(ValueError, match="invalid market"):
        _self_learning_random_timing_report("combined")


def test_random_timing_dashboard_rejects_contradictory_challenger_gate(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "models" / "self_learning" / "nse"
    root.mkdir(parents=True)
    (root / "latest.json").write_text(
        json.dumps(
            {
                "created_at": "2026-10-08T12:00:00+05:30",
                "controls": {
                    "random_timing": {
                        "status": "random_timing_fail",
                        "random_timing_gate_passed": True,
                        "contract_version": "quick-profit-v1",
                        "selector_policy": "top_1_per_session",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = _self_learning_random_timing_report("nse")
    gate = report["challenger_gate"]
    assert gate["status"] == "invalid_or_unreadable"
    assert gate["random_timing_gate_passed"] is False
    assert "contradicts" in gate["reason"]


def test_random_timing_dashboard_preserves_valid_bound_pass(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "models" / "self_learning" / "nse"
    root.mkdir(parents=True)
    timing = _passing_dashboard_timing()
    (root / "latest.json").write_text(
        json.dumps(
            {
                "created_at": "2026-10-08T12:00:00+05:30",
                "controls": {"random_timing": timing},
            }
        ),
        encoding="utf-8",
    )

    report = _self_learning_random_timing_report("nse")
    assert report["challenger_gate"] == timing


@pytest.mark.parametrize(
    ("timing_update", "reason"),
    [
        ({"market": "bse"}, "incomplete"),
        ({"markets_pooled": True}, "incomplete"),
        ({"contracts_pooled": True}, "incomplete"),
        ({"version": None}, "incomplete"),
        ({"replay_sha256": None}, "incomplete"),
        ({"replay_sha256": "b" * 64}, "replay hash"),
    ],
)
def test_random_timing_dashboard_rejects_incomplete_cross_market_or_pooled_pass(
    tmp_path: Path, monkeypatch, timing_update: dict, reason: str
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "models" / "self_learning" / "nse"
    root.mkdir(parents=True)
    timing = _passing_dashboard_timing()
    timing.update(timing_update)
    (root / "latest.json").write_text(
        json.dumps({"controls": {"random_timing": timing}}), encoding="utf-8"
    )

    gate = _self_learning_random_timing_report("nse")["challenger_gate"]
    assert gate["status"] == "invalid_or_unreadable"
    assert gate["random_timing_gate_passed"] is False
    assert reason in gate["reason"]
