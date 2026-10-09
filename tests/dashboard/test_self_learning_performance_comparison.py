from pathlib import Path


def test_learning_dashboard_contains_fail_closed_performance_comparison() -> None:
    html = Path("src/tradedesk/dashboard/static/index.html").read_text(encoding="utf-8")

    assert "/api/self-learning/performance-comparison?market=${market}" in html
    assert 'id="learning-before-accuracy"' in html
    assert 'id="learning-after-accuracy"' in html
    assert 'id="learning-filter-expectancy"' in html
    assert 'id="learning-version-table"' in html
    assert 'id="learning-frozen-baseline"' in html
    assert 'id="learning-frozen-challenger"' in html
    assert 'id="learning-improvement-proven"' in html
    assert "unavailable — not a pass" in html
    assert "not pooled" in html
