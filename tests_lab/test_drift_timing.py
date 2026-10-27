from __future__ import annotations

import numpy as np
from tradedesk_lab import drift_timing as dt


def _arrays(path: list[float], highs: list[float] | None = None) -> tuple[np.ndarray, ...]:
    c = np.array(path, float).reshape(-1, 1)
    o = np.vstack([[path[0]], c[:-1]])  # each day opens at the previous close
    h = np.array(highs if highs is not None else path, float).reshape(-1, 1)
    return o, h, c


def test_the_live_exit_takes_the_target_or_the_last_close() -> None:
    o, h, c = _arrays([100, 100, 104, 106, 103, 102])
    assert abs(dt.trade(o, h, c, 0, 100.0, 1, 5) - (102 / 100 - 1 - dt.RT)) < 1e-12
    o, h, c = _arrays([100, 100, 104, 106, 103, 102], highs=[100, 100, 104, 111, 103, 102])
    assert abs(dt.trade(o, h, c, 0, 100.0, 1, 5) - (0.10 - dt.RT)) < 1e-12  # +10% on day 3
    o, h, c = _arrays([100, 100, 115, 116])  # day 2 opens at 100 (prior close) - no gap here
    o[2, 0] = 113.0  # but say it gapped to 113: the target fills at the open, not at 110
    h[2, 0] = 116.0
    assert abs(dt.trade(o, h, c, 0, 100.0, 1, 3) - (0.13 - dt.RT)) < 1e-12
    assert dt.trade(o, h, c, 0, 100.0, 1, 9) is None  # exit beyond the data


def test_buying_the_close_wins_when_the_stock_gaps_up_overnight() -> None:
    o, h, c = _arrays([100, 100, 105, 106, 107])
    o[3, 0] = 105.5  # the reaction closed at 105, next morning opens 105.5
    late = dt.trade(o, h, c, 0, 105 * (1 + dt.LATE_ENTRY), 3, 4)
    base = dt.trade(o, h, c, 0, o[3, 0], 3, 4)
    assert late > base
