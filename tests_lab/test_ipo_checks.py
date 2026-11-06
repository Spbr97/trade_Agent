"""ipo_checks: the attacks must be fair, and a weak band must fail them."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import ipo_checks as ck


def _frame(excess: list[float], gain: float = 0.2, start: date = date(2017, 1, 2)) -> pd.DataFrame:
    days = [start + timedelta(days=40 * i) for i in range(len(excess))]
    return pd.DataFrame({"symbol": "X", "day": days, "gain": gain, "s20": excess, "s20_x": excess})


def _no_cost(entry: float, exit_px: float) -> float:
    return 0.0


def _with_neighbours(excess: list[float]) -> pd.DataFrame:
    return pd.concat([_frame(excess, 0.12), _frame(excess, 0.17), _frame(excess, 0.27)])


def test_the_buyer_pays_the_worse_entry_and_the_charges() -> None:
    frame = _frame([0.10])
    got = ck.buyer_net(frame, lambda a, b: 0.005).iloc[0]
    assert got == pytest.approx(1.10 / 1.01 - 1 - 0.005)


def test_a_steady_band_survives() -> None:
    got = ck.run_checks(_with_neighbours(list(np.resize([0.04, 0.09], 80))), _no_cost)
    assert got["survives"], got["fails"]


def test_a_few_big_winners_alone_do_not_survive() -> None:
    excess = [0.50] * 5 + [-0.01] * 75
    got = ck.run_checks(_frame(excess), _no_cost)
    assert "nothing left without the 5 best" in got["fails"]
    assert "median excess not positive" in got["fails"]


def test_a_band_that_needs_its_exact_edges_fails() -> None:
    inside = _frame(list(np.resize([0.04, 0.09], 80)), 0.20)
    neighbours = _frame(list(np.resize([-0.04, -0.09], 200)), 0.32)
    below = _frame(list(np.resize([-0.04, -0.09], 200)), 0.07)
    got = ck.run_checks(pd.concat([inside, neighbours, below]), _no_cost)
    assert "depends on the exact band edges" in got["fails"]


def test_something_that_stopped_working_fails_the_recent_check() -> None:
    frame = _frame(list(np.resize([0.04, 0.09], 40)), 0.2, date(2017, 1, 2))
    late = _frame(list(np.resize([-0.04, -0.09], 20)), 0.2, date(2023, 6, 1))
    got = ck.run_checks(pd.concat([frame, late]), _no_cost)
    assert "not positive from 2023" in got["fails"]


def test_the_quarter_t_counts_quarters_not_ipos() -> None:
    frame = pd.concat([_frame([0.01, 0.03, 0.02, 0.04])] * 25)
    assert ck.quarter_t(frame, "s20_x")[2] == 2


def test_fine_bands_sit_each_gain_in_one_place() -> None:
    frame = pd.concat([_frame([0.1], 0.12), _frame([0.2], 0.61)])
    got = ck.fine_bands(frame)
    assert got["+10%"]["ipos"] == 1
    assert got["+55%"]["ipos"] == 1
