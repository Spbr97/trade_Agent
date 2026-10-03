import numpy as np
import pandas as pd
from tradedesk_lab.accuracy_race_dataset import transparent_rule_score


def test_transparent_rule_score_is_deterministic_finite_and_bounded() -> None:
    frame = pd.DataFrame(
        {
            "adx14": [10.0, 45.0],
            "di_spread": [-20.0, 30.0],
            "dist_ema20_atr": [8.0, 1.0],
            "dist_high20_atr": [-8.0, 0.0],
            "rsi14": [30.0, 70.0],
            "roc20": [-20.0, 20.0],
            "atr_pct_rank": [100.0, 40.0],
            "range_contraction": [2.0, 0.7],
        }
    )

    first = transparent_rule_score(frame)
    second = transparent_rule_score(frame.copy())

    np.testing.assert_array_equal(first, second)
    assert np.isfinite(first).all()
    assert ((0 <= first) & (first <= 100)).all()
    assert first[1] > first[0]
