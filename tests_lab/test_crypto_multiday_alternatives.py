from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import crypto_multiday_alternatives as cm


def _panels(prices: list[float], coin: str = cm.BTC) -> tuple[dict[str, pd.DataFrame], list[date]]:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(prices))]
    frame = pd.DataFrame({coin: prices}, index=days)
    return {"open": frame, "close": frame, "mark": frame, "turnover": frame}, days


def test_a_winning_round_trip_pays_costs_and_30_percent_of_the_gain() -> None:
    p, days = _panels([100.0, 100.0, 200.0, 200.0])
    held = pd.Series([True, True, False, False], index=days)
    r = cm.run_single(p, days, held, cm.BTC)  # buy at day 1's open 100, sell at day 3's open 200
    gross = (1 - cm.FEE) / (1 + cm.SLIP) * 200 / 100 * (1 - cm.SLIP) * (1 - cm.FEE)
    assert r["final_equity"] == pytest.approx(1 + 0.7 * (gross - 1))
    assert r["trades"] == 1


def test_a_losing_round_trip_gets_no_tax_credit() -> None:
    p, days = _panels([100.0, 100.0, 50.0, 50.0])
    held = pd.Series([True, True, False, False], index=days)
    r = cm.run_single(p, days, held, cm.BTC)
    assert r["final_equity"] == pytest.approx(
        (1 - cm.FEE) / (1 + cm.SLIP) * 0.5 * (1 - cm.SLIP) * (1 - cm.FEE)
    )


def test_signals_on_a_close_trade_at_the_next_open() -> None:
    # Wanting the coin only after the jump's close must not capture the jump.
    p, days = _panels([100.0, 200.0, 200.0, 200.0])
    held = pd.Series([False, True, True, True], index=days)
    assert cm.run_single(p, days, held, cm.BTC)["final_equity"] < 1


def test_the_random_timing_null_keeps_exposure_and_spell_count() -> None:
    days = pd.date_range("2024-01-01", periods=60).date
    state = pd.Series(([True] * 7 + [False] * 5) * 5, index=days)
    fake = cm.spells_null(state, np.random.default_rng(1))
    assert fake.sum() == state.sum() and len(fake) == len(state)
    changes = lambda s: int((s.astype(int).diff().abs() > 0).sum())  # noqa: E731
    assert abs(changes(fake) - changes(state)) <= 1


def test_phantom_bars_are_blanked_not_traded() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(8)]
    close = pd.DataFrame({cm.BTC: [100.0, 101, 102, 0.00002, 103, 104, 0.0, 105]}, index=days)
    cleaned = cm.clean(close, close)
    assert cleaned[cm.BTC].isna().tolist() == [False, False, False, True, False, False, True, False]
