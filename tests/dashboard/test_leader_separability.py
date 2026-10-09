from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.leader_separability import load_latest_separability


def test_dashboard_exposes_fail_closed_leader_separability_panel(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    assert "/api/self-learning/leader-separability" in {
        route.path for route in app.routes
    }

    body = load_latest_separability("nse")
    assert body["status"] == "not_available"
    assert body["eligible_for_live"] is False
    assert body["active_model_changed"] is False
    assert body["baseline_accuracy_improved"] is False
    assert "not a pass" in body["detail"]

    html = (
        Path(__file__).parents[2]
        / "src"
        / "tradedesk"
        / "dashboard"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")
    assert "/api/self-learning/leader-separability?market=${market}" in html
    assert 'id="leader-model-top1"' in html
    assert 'id="leader-model-exec-accuracy"' in html
    assert 'id="leader-model-gate-table"' in html
    assert "Active model changed: NO. Baseline improved: NO." in html
