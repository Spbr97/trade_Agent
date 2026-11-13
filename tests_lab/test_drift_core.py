"""drift_core: idle slots earn the index, each call pays one switch, and the bar is applied."""

from __future__ import annotations

import numpy as np
import pandas as pd
from tradedesk_lab import drift_core as dc


def _book() -> pd.DataFrame:
    return pd.DataFrame({"entry_i": [1, 2], "exit_i": [2, 3], "net": [0.05, -0.02]})


def test_slots_are_held_from_entry_to_exit_inclusive() -> None:
    assert dc.held_slots(_book(), 5).tolist() == [0, 1, 2, 1, 0]


def test_the_core_book_earns_the_index_on_free_slots_and_pays_each_switch() -> None:
    nifty = np.array([100.0, 101.0, 101.0, 99.99, 100.99])
    got = dc.daily_rupees(_book(), dc.held_slots(_book(), 5), nifty, amount=10_000, slots=10,
                          switch=0.0035)  # fmt: skip
    assert got["live"].tolist() == [0.0, 0.0, 500.0, -200.0, 0.0]
    assert round(got["nifty"][1], 6) == 1_000.0  # +1% on Rs 1 lakh
    assert round(got["core"][1], 6) == 900 - 35  # nine free slots earn, one switch paid
    assert got["core"][2] == 500.0 - 35  # flat index, a call exits, another enters
    assert round(got["core"][3], 2) == round(-200 + 9 * 10_000 * (99.99 / 101 - 1), 2)


def test_the_bar_names_each_failing_part() -> None:
    years = np.repeat(np.arange(2017, 2027), 250)
    good = pd.DataFrame({"year": years, "core": 12.0, "nifty": 10.0})
    good.loc[::50, ["core", "nifty"]] = [-100.0, -90.0]
    assert dc.judge(good)["passed"]
    late = good.copy()
    early = late["year"] < 2022
    late.loc[early, "core"] = late.loc[early, "nifty"] - 1.0
    fails = dc.judge(late)["fails"]
    assert "ahead in only 5 of 10 full years" in fails and any("halves" in f for f in fails)
    deep = good.copy()
    deep.loc[100:140, "core"] = -500.0
    assert any(f.startswith("worst fall") for f in dc.judge(deep)["fails"])
