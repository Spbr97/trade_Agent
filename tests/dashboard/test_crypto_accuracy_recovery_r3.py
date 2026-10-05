from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import tradedesk_lab.crypto_accuracy_recovery as recovery

import tradedesk.dashboard.app as dashboard_app
from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState


def _get(path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(DashboardState()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_crypto_accuracy_recovery_endpoint_fails_closed_when_artifact_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "CRYPTO_ACCURACY_RECOVERY",
        tmp_path / "missing-state.json",
    )

    response = _get("/api/crypto/accuracy-recovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("baseline_improved") is not True
    assert payload.get("eligible_for_live") is not True


def test_dashboard_contains_crypto_accuracy_recovery_contract() -> None:
    html = Path("src/tradedesk/dashboard/static/index.html").read_text(encoding="utf-8")

    assert "/api/crypto/accuracy-recovery" in html
    assert "crypto-recovery-status" in html
    assert "crypto-recovery-trials" in html
    assert "crypto-recovery-wf-accuracy" in html
    assert "crypto-recovery-live-accuracy" in html


def test_crypto_accuracy_recovery_endpoint_returns_only_compact_verified_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "recovery" / "state.json"
    state_path.parent.mkdir()
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dashboard_app, "CRYPTO_ACCURACY_RECOVERY", state_path)
    monkeypatch.setattr(
        recovery,
        "verify_crypto_accuracy_recovery",
        lambda _output: {
            "status": "no_candidate_cleared_recovery_gates",
            "version": "crypto-accuracy-recovery-r3-v1",
            "id": "run-1",
            "created_at": "2026-10-05T10:34:45+00:00",
            "source": {"tracker_rows": 522, "backfill_rows": 461, "live_rows": 61},
            "trial_counts": {
                "setups": 4,
                "registered_per_setup": 16,
                "registered_total": 64,
                "passing_setups": 0,
            },
            "best_diagnostic": {
                "setup": "nr7_breakout",
                "status": "rejected_or_insufficient",
                "nominee": {
                    "candidate_id": "target_0p75r_hold_7d",
                    "target_r": 0.75,
                    "max_hold_sessions": 7,
                },
                "development": {"observed_accuracy": 0.24},
                "walk_forward": {"observed_accuracy": 0.21, "positive_folds": 0},
                "live_validation": {"observed_accuracy": 0.09},
                "gates": {"walk_forward_accuracy": False},
                "passed_research_gate": False,
            },
            "source_integrity": {"passed": True, "errors": []},
            "research_gate_passed": False,
            "baseline_improved": False,
            "eligible_for_live": False,
            "detail": "research only",
            "artifacts": {"must_not_leak": True},
            "setups": ["must not leak"],
        },
    )

    response = _get("/api/crypto/accuracy-recovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trial_counts"]["registered_total"] == 64
    assert payload["best_diagnostic"]["setup"] == "nr7_breakout"
    assert payload["baseline_improved"] is False
    assert payload["eligible_for_live"] is False
    assert "artifacts" not in payload
    assert "setups" not in payload
