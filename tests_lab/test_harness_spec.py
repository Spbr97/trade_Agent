from tradedesk_lab.harness.spec import KillCriteria, chance_streak_limit, evaluate


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


def test_chance_streak_limit_grows_with_sample_and_loss_rate():
    assert chance_streak_limit(1987, 0.61) >= 18  # r_tsmom20's shape: 39% wins, ~2,000 trades
    assert chance_streak_limit(300, 0.61) < chance_streak_limit(3000, 0.61)
    assert chance_streak_limit(1000, 0.4) < chance_streak_limit(1000, 0.8)
    assert chance_streak_limit(0, 0.5) == 0 and chance_streak_limit(100, 1.0) == 0


def test_loss_rate_scales_the_streak_cap_but_never_below_the_configured_one():
    kw = dict(net_expectancy_r=0.05, sample_size=1987, max_drawdown_pct=0.15)
    assert not evaluate(_criteria(), losing_streak=18, **kw).passed  # fixed cap of 10
    scaled = evaluate(_criteria(), losing_streak=18, loss_rate=0.61, **kw)
    assert scaled.passed and scaled.measured["allowed_losing_streak"] >= 18
    # A streak far beyond what chance produces still fails, and a tiny sample keeps the cap.
    assert not evaluate(_criteria(), losing_streak=60, loss_rate=0.61, **kw).passed
    small = evaluate(_criteria(), net_expectancy_r=0.05, sample_size=300,
                     max_drawdown_pct=0.15, losing_streak=40, loss_rate=0.5)
    assert not small.passed

