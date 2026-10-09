from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.intraday_contract_race import load_intraday_contract_status


def test_dashboard_exposes_fail_closed_intraday_contract_panel(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(DashboardState())
    assert "/api/self-learning/intraday-contract" in {route.path for route in app.routes}

    body = load_intraday_contract_status("nse")
    assert body["status"] == "not_started"
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
    assert "/api/self-learning/intraday-contract?market=${market}" in html
    assert 'id="intraday-contract-validation-paths"' in html
    assert 'id="intraday-contract-diagnostic-paths"' in html
    assert 'id="intraday-contract-accuracy"' in html
    assert "Active model changed: NO. Baseline improved: NO." in html

