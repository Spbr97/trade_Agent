"""ipo_listing: the issue price must be real, and a hold must be measured from the listing open."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import ipo_listing as il
from tradedesk_lab import new_listings as nl


def _row(**kw: object) -> dict:
    return {"securityType": "Equity", "symbol": "X", "issuePrice": "    82",
            "listingDate": "05-NOV-2026", **kw}  # fmt: skip


def test_nses_padded_prices_and_dates_are_read() -> None:
    assert il.number("    82") == 82.0
    assert il.number("1,665") == 1665.0
    assert il.number("-") is None
    assert il.parse_day("05-NOV-2026") == date(2026, 11, 5)
    assert il.parse_day("-") is None


def test_only_mainboard_issues_with_a_price_and_a_date_count() -> None:
    rows = [_row(), _row(securityType="SME"), _row(issuePrice="-"), _row(listingDate="-")]
    kept, dropped = il.clean(rows, {})
    assert len(kept) == 1
    assert dropped == {"no issue price": 1, "not listed": 1}


def test_a_later_split_drops_an_ipo_but_an_earlier_one_does_not() -> None:
    later = {"X": [date(2027, 1, 1)]}
    assert il.clean([_row()], later)[0] == []
    earlier = {"X": [date(2020, 1, 1)]}
    assert len(il.clean([_row()], earlier)[0]) == 1


DAYS = list(pd.date_range("2026-01-01", periods=80, freq="B").date)


def _panels(opening: float = 120.0, first: int = 5) -> nl.Panels:
    closes = pd.DataFrame({"X": [np.nan] * first + [130.0] * (80 - first)}, index=DAYS)
    opens = closes.copy()
    opens.iloc[first, 0] = opening
    return nl.Panels(opens, closes, closes, closes * 0 + 1e6)


def _ipo(issue: float = 100.0, first: int = 5) -> dict:
    return {"symbol": "X", "issue": issue, "listed": DAYS[first]}


def test_the_gain_is_from_the_issue_price_and_a_hold_from_the_listing_open() -> None:
    frame, dropped = il.outcomes([_ipo()], _panels(), lambda e, x: 0.01, {"X": "X"})
    assert not dropped
    row = frame.iloc[0]
    assert row["gain"] == pytest.approx(0.20)
    assert row["close"] == pytest.approx(130 * (1 - il.SLIPPAGE) / (120 * (1 - il.SLIPPAGE)) - 1)
    assert row["close_x"] == pytest.approx(row["close"] - 0.01)


def test_a_hold_past_the_data_has_no_figure() -> None:
    frame, _ = il.outcomes([_ipo(first=30)], _panels(first=30), lambda e, x: 0.0, {"X": "X"})
    assert "s60" not in frame.columns or pd.isna(frame.iloc[0]["s60"])
    assert frame.iloc[0]["s20"] == pytest.approx(130 / 120 - 1)


def test_an_open_that_cannot_match_the_issue_price_is_dropped() -> None:
    assert il.outcomes([_ipo(issue=1000.0)], _panels(), lambda e, x: 0.0, {"X": "X"})[1] == {
        "open far from the issue price": 1
    }


def test_a_first_candle_on_another_day_is_dropped() -> None:
    ipo = {"symbol": "X", "issue": 100.0, "listed": DAYS[7]}
    assert il.outcomes([ipo], _panels(), lambda e, x: 0.0, {"X": "X"})[1] == {
        "first candle not the listing date": 1
    }


def _frame(excess: list[float]) -> pd.DataFrame:
    days = [date(2019, 1, 7) + timedelta(days=7 * i) for i in range(len(excess))]
    return pd.DataFrame({"day": days, "gain": 0.1, "close": excess, "close_x": excess})


def test_a_steady_loss_from_holding_says_sell() -> None:
    got = il.judge(_frame(list(np.resize([-0.02, -0.04], 80))), "close")
    assert got["verdict"] == "sell"


def test_a_steady_gain_from_holding_says_hold() -> None:
    assert il.judge(_frame(list(np.resize([0.02, 0.04], 80))), "close")["verdict"] == "hold"


def test_too_few_ipos_or_a_split_record_give_no_verdict() -> None:
    assert il.judge(_frame([-0.02, -0.04] * 10), "close")["verdict"] == "none"
    mixed = [-0.03] * 40 + [0.03] * 40
    assert il.judge(_frame(mixed), "close")["verdict"] == "none"


def _hot(hot_gain: float, n: int = 240) -> pd.DataFrame:
    # Stretches of 30 alternate hot and cold; each IPO's gain follows the stretch it is in.
    gains = [hot_gain if (i // 30) % 2 else 0.0 for i in range(n)]
    gains = [g + (0.01 if i % 2 else -0.01) for i, g in enumerate(gains)]
    days = [date(2017, 1, 2) + timedelta(days=7 * i) for i in range(n)]
    return pd.DataFrame({"day": days, "gain": gains})


def test_a_hot_market_is_found_when_stretches_persist() -> None:
    assert il.hot_market(_hot(0.3))["passed"]


def test_no_persistence_means_no_hot_market() -> None:
    assert not il.hot_market(_hot(0.0))["passed"]
