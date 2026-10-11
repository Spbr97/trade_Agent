from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.pre_takeoff_selector import load_pre_takeoff_status
from tradedesk.pre_takeoff_watch import load_pre_takeoff_watch


def test_dashboard_exposes_fail_closed_pre_takeoff_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    routes = {route.path for route in app.routes}
    assert "/api/self-learning/pre-takeoff-selector" in routes
    assert "/api/self-learning/pre-takeoff-watch" in routes

    selector = load_pre_takeoff_status("nse")
    assert selector["status"] == "not_started"
    assert selector["eligible_for_live"] is False
    assert selector["baseline_accuracy_improved"] is False

    watch = load_pre_takeoff_watch("crypto")
    assert watch["status"] == "not_collected"
    assert watch["tradeable_calls"] == 0
    assert watch["eligible_for_live"] is False
    assert watch["baseline_accuracy_improved"] is False
    assert watch["collector_health"]["status"] == "not_collected"
    assert watch["watch_plans"]["evidence"]["status"] == "collecting_qualification"

    html = (
        Path(__file__).parents[2]
        / "src"
        / "tradedesk"
        / "dashboard"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")
    assert "/api/self-learning/pre-takeoff-selector?market=${market}" in html
    assert "/api/self-learning/pre-takeoff-watch?market=${market}" in html
    assert 'id="pre-takeoff-selector-curve-table"' in html
    assert 'id="pre-takeoff-watch-table"' in html
    assert 'id="pre-takeoff-watch-m15-quality"' in html
    assert 'id="pre-takeoff-watch-registration"' in html
    assert 'id="pre-takeoff-watch-health"' in html
    assert 'id="pre-takeoff-watch-sessions"' in html
    assert 'id="pre-takeoff-watch-wilson"' in html
    assert 'id="pre-takeoff-watch-loss-streak"' in html
    assert 'id="pre-takeoff-watch-qualification"' in html
    assert 'id="pre-takeoff-watch-progress"' in html
    assert "Only a later completed-bar trigger can create measured trade evidence." in html
