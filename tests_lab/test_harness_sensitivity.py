from tradedesk_lab.harness.sensitivity import sweep_parameter


def test_flat_response_is_a_plateau():
    result = sweep_parameter("x", 10.0, lambda v: 0.05)
    assert result.low_value == 8.0 and result.high_value == 12.0
    assert result.is_plateau


def test_all_negative_is_also_a_plateau_not_a_spike():
    result = sweep_parameter("x", 10.0, lambda v: -0.02)
    assert result.is_plateau


def test_isolated_positive_spike_surrounded_by_negatives_is_not_a_plateau():
    def run(v: float) -> float:
        return 0.10 if v == 10.0 else -0.05

    result = sweep_parameter("x", 10.0, run)
    assert result.base_expectancy_r == 0.10
    assert result.low_expectancy_r == -0.05
    assert result.high_expectancy_r == -0.05
    assert not result.is_plateau
