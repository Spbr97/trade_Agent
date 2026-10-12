from __future__ import annotations

from pathlib import Path

from tradedesk.dashboard.app import create_app
from tradedesk.dashboard.state import DashboardState
from tradedesk.momentum_ignition_watch_m22 import load_momentum_ignition_watch_m22

ROOT = Path(__file__).parents[2]


def test_m22_endpoint_is_read_only_crypto_evidence(tmp_path: Path) -> None:
    app = create_app(DashboardState())
    routes = {route.path for route in app.routes}
    assert "/api/self-learning/momentum-ignition-watch-m22" in routes

    source = (ROOT / "src" / "tradedesk" / "dashboard" / "app.py").read_text(
        encoding="utf-8"
    )
    assert '@app.get("/api/self-learning/momentum-ignition-watch-m22")' in source
    assert "load_momentum_ignition_watch_m22" in source
    assert "await asyncio.to_thread(load_momentum_ignition_watch_m22, market)" in source

    not_applicable = load_momentum_ignition_watch_m22(
        "nse", output_root=tmp_path
    )
    assert not_applicable["status"] == "not_applicable"
    assert not_applicable["tradeable_calls"] == 0
    assert not_applicable["eligible_for_live"] is False
    assert not_applicable["baseline_accuracy_improved"] is False

    not_collected = load_momentum_ignition_watch_m22(
        "crypto", output_root=tmp_path
    )
    assert not_collected["status"] == "not_collected"
    assert not_collected["tradeable_calls"] == 0
    assert not_collected["eligible_for_live"] is False
    assert not_collected["baseline_accuracy_improved"] is False
    assert "unavailable is not a pass" in not_collected["detail"]


def test_m22_dashboard_separates_feed_feature_and_performance_evidence() -> None:
    html = (
        ROOT / "src" / "tradedesk" / "dashboard" / "static" / "index.html"
    ).read_text(encoding="utf-8")

    assert "M22 feed-aware momentum-ignition challenger" in html
    assert html.index("const esc = (value)") < html.index("async function loadLearning()")
    assert "Universe and data readiness — not performance" in html
    assert "Forward performance — separate M22 evidence" in html
    assert "/api/self-learning/momentum-ignition-watch-m22?market=${market}" in html
    assert html.index("/api/self-learning/momentum-ignition-watch?market=${market}") < (
        html.index("/api/self-learning/momentum-ignition-watch-m22?market=${market}")
    )

    for element_id in (
        "momentum-ignition-m22-status",
        "momentum-ignition-m22-snapshot",
        "momentum-ignition-m22-health",
        "momentum-ignition-m22-manifest",
        "momentum-ignition-m22-feed-coverage",
        "momentum-ignition-m22-feature-breadth",
        "momentum-ignition-m22-feature-minimums",
        "momentum-ignition-m22-coverage-gates",
        "momentum-ignition-m22-validity-table",
        "momentum-ignition-m22-registration",
        "momentum-ignition-m22-candidates",
        "momentum-ignition-m22-ignition",
        "momentum-ignition-m22-reclaims",
        "momentum-ignition-m22-economics",
        "momentum-ignition-m22-active",
        "momentum-ignition-m22-resolved",
        "momentum-ignition-m22-accuracy",
        "momentum-ignition-m22-net-r",
        "momentum-ignition-m22-sessions",
        "momentum-ignition-m22-wilson",
        "momentum-ignition-m22-loss-streak",
        "momentum-ignition-m22-qualification",
        "momentum-ignition-m22-progress",
        "momentum-ignition-m22-improved",
        "momentum-ignition-m22-authority",
        "momentum-ignition-m22-detail",
        "momentum-ignition-m22-table",
    ):
        assert f'id="{element_id}"' in html

    renderer = html[
        html.index("function renderMomentumIgnitionWatchM22(report)") : html.index(
            "function renderPerformanceComparison(report)"
        )
    ]
    assert "report.feed_valid_pairs" in renderer
    assert "report.feed_coverage" in renderer
    assert "report.feature_valid_pairs" in renderer
    assert "report.feature_coverage_of_feed" in renderer
    assert "const evidence = plans.evidence || {};" in renderer
    assert "report.evidence" not in renderer
    assert "report.metrics" not in renderer
    assert "not available — not a pass" in renderer
    assert "coverage is data readiness, not accuracy" in renderer
    assert "M22 evidence remains separate from M20 and M21" in renderer
    assert "comparisonRow('M22 feed-aware challenger'" in html
