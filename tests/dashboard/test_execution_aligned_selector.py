from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.execution_aligned_selector import load_execution_aligned_status


def test_dashboard_exposes_fail_closed_execution_aligned_panel(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    assert "/api/self-learning/execution-aligned" in {
        route.path for route in app.routes
    }

    body = load_execution_aligned_status("nse")
    assert body["status"] == "not_available"
    assert body["prospective"]["status"] == "not_registered"
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
    assert "/api/self-learning/execution-aligned?market=${market}" in html
    assert 'id="execution-selector-validation"' in html
    assert 'id="execution-selector-progress"' in html
    assert 'id="execution-selector-gate-table"' in html
    assert "Active model changed: NO. Baseline improved: NO." in html
