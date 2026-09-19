from tradedesk_lab.harness.spec import KillCriteria, evaluate


def _criteria() -> KillCriteria:
    return KillCriteria(
        min_net_expectancy_r=0.0, min_sample_size=300, max_drawdown_pct=0.30, max_losing_streak=10
    )


def test_passes_when_every_measurement_clears_its_bar():
    result = evaluate(
        _criteria(), net_expectancy_r=0.05, sample_size=500, max_drawdown_pct=0.15, losing_streak=4
    )
    assert result.passed
    assert result.failures == ()


def test_reports_every_failing_criterion_at_once_not_just_the_first():
    result = evaluate(
        _criteria(), net_expectancy_r=-0.1, sample_size=50, max_drawdown_pct=0.55, losing_streak=15
    )
    assert not result.passed
    assert len(result.failures) == 4
    assert any("sample_size" in f for f in result.failures)
    assert any("net_expectancy_r" in f for f in result.failures)
    assert any("max_drawdown_pct" in f for f in result.failures)
    assert any("losing_streak" in f for f in result.failures)


def test_boundary_values_pass_not_fail():
    result = evaluate(
        _criteria(), net_expectancy_r=0.0, sample_size=300, max_drawdown_pct=0.30, losing_streak=10
    )
    assert result.passed
