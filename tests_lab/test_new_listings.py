from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from tradedesk_lab import new_listings as nl

DAYS = [date(2018, 1, 1) + timedelta(days=i) for i in range(120)]


def _panels(new: dict[str, tuple[int, list[float]]]) -> nl.Panels:
    """Two seasoned stocks from day 0 plus `new` = {code: (listing index, closes from it)}.
    Opens equal the previous close (the listing day opens at 100); highs equal closes."""
    closes = {"OLD1": [100.0] * len(DAYS), "OLD2": [200.0] * len(DAYS)}
    opens = {k: list(v) for k, v in closes.items()}
    for code, (i0, path) in new.items():
        c = [np.nan] * i0 + path + [path[-1]] * (len(DAYS) - i0 - len(path))
        closes[code] = c
        opens[code] = [np.nan] * i0 + [100.0] + c[i0:-1]
    close = pd.DataFrame(closes, index=DAYS)
    vol = close.notna() * 1_000_000.0
    return nl.Panels(pd.DataFrame(opens, index=DAYS), close.copy(), close, vol)


def test_listings_skip_the_history_start_and_bulk_days() -> None:
    p = _panels({"NEW": (10, [104.0] * 5)})
    assert nl.listings(p) == {"NEW": 10}  # OLD1/OLD2 begin with the data, not a listing
    crowd = _panels({f"S{i}": (20, [101.0] * 3) for i in range(nl.BULK_START + 1)})
    assert nl.listings(crowd) == {}  # 21 stocks first seen on one day: a history start


def test_rules_find_strong_debuts_and_post_listing_highs() -> None:
    p = _panels({
        "UP": (10, [108.0, 107.0, 106.0, 109.0, 110.0]),  # +8% debut, new high on day 13
        "FLAT": (12, [101.0, 100.0, 100.5, 100.8]),  # +1% debut, never above 101
    })  # fmt: skip
    listed = {"UP": 10, "FLAT": 12}
    assert sorted(nl.signals(p, listed, "debut")) == [("FLAT", 12), ("UP", 10)]
    assert nl.signals(p, listed, "strong_debut") == [("UP", 10)]
    assert nl.signals(p, listed, "listing_high") == [("UP", 13)]


def test_outcome_buys_the_next_open_and_nets_costs_and_market() -> None:
    p = _panels({"UP": (10, [108.0, 110.0, 120.0])})
    mkt = nl.market(p).fillna(0.0) * 0  # a flat market
    out = nl.outcome(p, mkt, "UP", 10, 2, lambda entry, exit_px: 0.005)
    entry, exit_px = 108.0 * (1 + nl.SLIPPAGE), 120.0 * (1 - nl.SLIPPAGE)
    assert abs(out["net"] - (exit_px / entry - 1 - 0.005)) < 1e-12
    assert out["excess"] == out["net"]
    assert nl.outcome(p, mkt, "UP", len(DAYS) - 2, 5, lambda *_: 0.0) is None


def test_the_pass_bar_counts_weeks_not_calls() -> None:
    days = [DAYS[i] for i in range(0, 119, 1)]
    good = pd.DataFrame({"day": days, "net": 0.02, "excess": [0.02 + 0.01 * (i % 3) for i in
                                                                range(len(days))]})  # fmt: skip
    assert nl.judge(good)["passed"]
    same_week = good.assign(day=DAYS[0])
    verdict = nl.judge(same_week)
    assert not verdict["passed"] and verdict["weeks"] == 1
