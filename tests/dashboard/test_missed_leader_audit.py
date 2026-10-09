from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.leader_discovery import load_latest_audit


def test_dashboard_exposes_fail_closed_missed_leader_panel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    assert "/api/self-learning/missed-leaders" in {route.path for route in app.routes}

    body = load_latest_audit("nse")
    assert body["status"] == "not_available"
    assert body["eligible_for_live"] is False
    assert body["active_model_changed"] is False
    assert "not a pass" in body["detail"]

    html = (
        Path(__file__).parents[2] / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert "/api/self-learning/missed-leaders?market=${market}" in html
    assert 'id="leader-audit-candidate-recall"' in html
    assert 'id="leader-audit-qualified-recall"' in html
    assert 'id="leader-audit-table"' in html
    assert "Baseline improved: NO" in html
