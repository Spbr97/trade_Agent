from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import dip_book as db

DAYS = pd.bdate_range("2020-01-01", periods=40)


def _setup(stocks: int = 12, signal_day: int = 5) -> tuple[dict, dict]:
    codes = [f"S{k:02d}" for k in range(stocks)]
    close = pd.DataFrame(100.0, index=DAYS, columns=codes)
    opens = close.copy()
    opens.iloc[signal_day + 1 :] = 101.0  # the open after the signal: what a buyer pays
    p = {"open": opens, "close": close, "volume": close * 0 + 3e6}
    signal = pd.DataFrame(False, index=DAYS, columns=codes)
    signal.iloc[signal_day] = True
    rsi2 = pd.DataFrame(50.0, index=DAYS, columns=codes)
    rsi2.iloc[signal_day] = np.arange(stocks)[::-1]  # the last stock is the deepest dip
    ind = {
        "signal": signal,
        "rsi2": rsi2,
        "pool": signal.copy(),
        "sma5": close + 1,  # never closes above its 5-day average: held the full 10 sessions
    }
    return p, ind


def test_the_book_fills_only_its_ten_slots_deepest_dip_first() -> None:
    p, ind = _setup()
    taken = db.run_book(p, ind)
    assert len(taken) == db.SLOTS
    assert sorted(t["j"] for t in taken) == list(range(2, 12))  # the two shallowest wait


def test_it_buys_the_next_open_and_sells_after_at_most_ten_sessions() -> None:
    p, ind = _setup()
    t = db.run_book(p, ind)[0]
    assert (t["e"], t["x"]) == (6, 16)
    assert t["ret"] == pytest.approx(0.0)  # bought at 101, sold at 101


def test_a_close_above_the_five_day_average_sells_at_the_next_open() -> None:
    p, ind = _setup()
    ind["sma5"].iloc[8] = 99.0  # day 8 closes above it
    assert db.exit_index(p["close"].to_numpy(), ind["sma5"].to_numpy(), 6, 0) == 9


def test_a_held_stock_is_not_bought_again_and_a_freed_slot_is_reused() -> None:
    p, ind = _setup(stocks=11)
    ind["signal"].iloc[20] = True  # a second evening, after the first ten were sold at day 16
    taken = db.run_book(p, ind)
    first, second = [t for t in taken if t["i"] == 5], [t for t in taken if t["i"] == 20]
    assert len(first) == db.SLOTS and len(second) == db.SLOTS


def test_random_books_trade_the_same_opens_so_one_stock_pool_matches_the_book() -> None:
    p, ind = _setup(stocks=1)
    p["open"].iloc[16:] = 110.0
    taken = db.run_book(p, ind)
    slot, rt = db.SIZES["rs_25000"]
    totals = db.random_totals(p, ind, taken, slot, rt, books=3)
    assert totals == pytest.approx([slot * (110 / 101 - 1 - rt)] * 3)
