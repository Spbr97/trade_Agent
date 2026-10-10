from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.selection_readiness import load_selection_readiness_status


def test_dashboard_exposes_fail_closed_selection_readiness_panel(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    assert "/api/self-learning/selection-readiness" in {
        route.path for route in app.routes
    }

    body = load_selection_readiness_status("nse")
    assert body["status"] == "not_started"
    assert body["eligible_for_live"] is False
    assert body["active_model_changed"] is False
    assert body["baseline_accuracy_improved"] is False

    html = (
        Path(__file__).parents[2]
        / "src"
        / "tradedesk"
        / "dashboard"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")
    assert "/api/self-learning/selection-readiness?market=${market}" in html
    assert 'id="selection-readiness-train"' in html
    assert 'id="selection-readiness-calibration"' in html
    assert 'id="selection-readiness-diagnostic"' in html
    assert 'id="selection-readiness-block-table"' in html
    assert "Model trained: NO. Active model changed: NO. Baseline improved: NO." in html
