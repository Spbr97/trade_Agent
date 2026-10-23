from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import down_day_leaders as dd

DAYS = pd.bdate_range("2020-01-01", periods=60)


def _panels(down_day: int = 30) -> dict[str, pd.DataFrame]:
    """Three stocks fall 3% on `down_day`; LEAD rises 2% on double volume. Flat otherwise."""
    codes = ["A", "B", "C", "LEAD"]
    close = pd.DataFrame(100.0, index=DAYS, columns=codes)
    close.iloc[down_day:, :3] = 97.0
    close.iloc[down_day:, 3] = 102.0
    close.iloc[down_day + 5 :, 3] = 110.0
    opens = close.shift(1).fillna(100.0)
    vol = pd.DataFrame(3e6, index=DAYS, columns=codes)
    vol.iloc[down_day, 3] = 6e6
    return {"open": opens, "close": close, "high": close, "low": close, "volume": vol}


def test_a_stock_rising_on_volume_while_the_market_falls_is_a_leader() -> None:
    sig = dd.signals(_panels())
    assert sig["market"].iloc[30] == pytest.approx((-0.03 * 3 + 0.02) / 4)
    leaders = sig["all_leaders"]
    assert leaders.iloc[30]["LEAD"] and not leaders.iloc[30][["A", "B", "C"]].any()
    assert leaders.drop(index=DAYS[30]).to_numpy().sum() == 0  # flat days are not down days


def test_the_trade_buys_the_next_open_and_compares_the_same_opens() -> None:
    p = _panels()
    sig = dd.signals(p)
    t = dd.trades(sig["all_leaders"], p, sig["liquid"], hold=10)
    assert list(t["code"]) == ["LEAD"] and t["day"].iloc[0] == DAYS[31]
    # bought at day 31's open (the 102 close of the leader day), sold at day 41's open (110)
    assert t["gross"].iloc[0] == pytest.approx(110 / 102 - 1)
    assert t["market"].iloc[0] == pytest.approx(np.mean([0, 0, 0, 110 / 102 - 1]))


def test_a_signal_too_near_the_end_to_finish_its_hold_is_not_traded() -> None:
    p = _panels(down_day=55)
    sig = dd.signals(p)
    assert dd.trades(sig["all_leaders"], p, sig["liquid"], hold=10).empty
