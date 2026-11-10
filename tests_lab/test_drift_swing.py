"""drift_swing: both plans see the same clean calls and the exit decision follows its rule."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import drift_swing as ds
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import nifty_swing as ns


def test_a_price_jump_taints_thirty_sessions_before_and_twenty_after() -> None:
    closes = np.full((80, 2), 100.0)
    opens = closes.copy()
    opens[40, 0] = 50.0  # an unadjusted split on row 40 of the first stock
    closes[40:, 0] = 50.0
    opens[41:, 0] = 50.0
    got = ds.near_jump(opens, closes)
    assert not got[:, 1].any()
    tainted = np.nonzero(got[:, 0])[0]
    assert (tainted.min(), tainted.max()) == (40 - ds.JUMP_AHEAD, 40 + ds.JUMP_BACK)


def _market(rows: int = 230) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    days = pd.date_range("2020-01-01", periods=rows, freq="B")
    close = pd.DataFrame(100.0, index=days, columns=["A", "B", "C"])
    volume = pd.DataFrame(2_000_000.0, index=days, columns=["A", "B", "C"])  # Rs 20 crore a day
    return close, volume, pd.Series(np.linspace(200, 100, rows), index=days)


def test_a_falling_market_down_day_is_marked() -> None:
    close, volume, falling = _market()
    close.iloc[220] = 98.0
    close.iloc[221:] = 98.0
    got = ds.marked_days(close, volume, falling)
    assert list(np.nonzero(got)[0]) == [220]
    rising = pd.Series(np.linspace(100, 200, len(close)), index=close.index)
    assert not ds.marked_days(close, volume, rising).any()


def test_any_day_cells_are_fixed_liquid_and_unmarked() -> None:
    close, volume, _ = _market()
    volume["C"] = 100_000.0  # Rs 1 crore a day, under the calls' floor
    marked = np.zeros(len(close), dtype=bool)
    marked[100:150] = True
    got = ds.any_day_cells(close, volume, marked, share=0.5)
    assert np.array_equal(got, ds.any_day_cells(close, volume, marked, share=0.5))
    assert not got[:, 2].any()
    assert not got[100:150].any()
    assert not got[:20].any()  # no 20-session turnover yet
    assert 0.35 < got[20:100, :2].mean() < 0.65


def _outcomes(net_r: float, rows: int = 12) -> ns.Outcomes:
    grid = np.full((rows, 2), np.nan)
    net = grid.copy()
    net[3, 0] = net_r
    entry, qty = np.where(np.isfinite(net), 200.0, np.nan), np.where(np.isfinite(net), 50.0, 0.0)
    return ns.Outcomes(entry, entry - 10, qty, net * 500, net, np.where(np.isfinite(net), 9, -1))


def test_a_call_keeps_both_plans_results_side_by_side() -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(12)]
    calls = pd.DataFrame({"code": ["X", "Y"], "entry_i": [4, 6], "exit_i": [20, 22],
                          "net": [0.04, -0.02], "marked": [False, True]})  # fmt: skip
    reaction = np.full((12, 2), 0.07)
    got = ds.plan_trades(calls, _outcomes(1.5), {"X": 0, "Y": 1}, days, reaction)
    assert list(got["traded"]) == [True, False]
    first = got.iloc[0]
    assert (first["signal_row"], first["entry_row"], first["exit_row"]) == (3, 4, 9)
    assert first["day"] == days[4]
    assert first["manual_net"] == pytest.approx(750 / (50 * 200))
    assert first["live_net"] == 0.04
    assert bool(got.iloc[1]["marked"])


def test_money_keeps_profit_and_loss_apart() -> None:
    got = ds.money([500, -200, 300, -700])
    assert (got["net_inr"], got["total_profit"], got["total_loss"]) == (-100, 800, -900)
    assert got["worst_fall_inr"] == 700
    assert got["win_rate"] == 0.5


def test_the_same_calls_are_compared_week_by_week() -> None:
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 8), date(2024, 1, 15)]
    trades = pd.DataFrame({"day": days, "traded": [True, True, True, False],
                           "manual_net": [0.02, 0.04, -0.01, np.nan],
                           "live_net": [0.01, 0.01, 0.03, 0.05]})  # fmt: skip
    got = ds.paired(trades)
    assert got["calls"] == 3
    assert got["difference"] == pytest.approx((0.02 + -0.04) / 2)


def test_the_manual_plan_must_make_as_much_and_fall_no_further() -> None:
    live = {"net_inr": 1000.0, "worst_fall_inr": 400.0}
    assert ds.which_exit({"net_inr": 1200.0, "worst_fall_inr": 300.0}, live)["manual_beats_live"]
    less = ds.which_exit({"net_inr": 900.0, "worst_fall_inr": 300.0}, live)
    assert (less["manual_beats_live"], less["why_not"]) == (False, ["made fewer rupees"])
    deeper = ds.which_exit({"net_inr": 1200.0, "worst_fall_inr": 500.0}, live)
    assert deeper["why_not"] == ["fell further"]


def test_the_marking_uses_the_live_rules_floor() -> None:
    assert eh.MIN_TURNOVER == 5e7 and eh.MIN_PRICE == 50.0
