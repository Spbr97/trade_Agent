"""results_short_forward: only unseen sessions count, each trade once, and the rule must be the
study's own."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reading as rr
from tradedesk_lab import results_short_forward as fw

DAYS = list(pd.date_range("2026-10-01", periods=40, freq="B").date)  # to 2026-11-25
START = date(2026, 11, 9)
QUARTER, YEAR_AGO = date(2026, 9, 30), date(2025, 9, 30)


def _measure(turnover: float = 1e9, opening: float = 100.0, close: float = 98.0) -> SimpleNamespace:
    frame = pd.DataFrame({"S": [100.0] * len(DAYS), "M": [100.0] * len(DAYS)}, index=DAYS)
    closes, opens, highs = frame.copy(), frame.copy(), frame.copy()
    i = DAYS.index(START)
    opens.loc[DAYS[i], "S"] = opening
    closes.loc[DAYS[i], "S"] = close
    highs.loc[DAYS[i], "S"] = 103.0
    closes.loc[DAYS[i], "M"] = 101.0
    panels = nl.Panels(opens, highs, closes, frame * 0 + 1e6)
    usual = pd.DataFrame(turnover, index=DAYS, columns=frame.columns)
    return SimpleNamespace(
        p=panels, column={"S": 0, "M": 1}, opens=opens.to_numpy(), closes=closes.to_numpy(),
        turnover=usual, intraday=lambda buy, sell: 0.002, market=lambda e, x: 0.01, cm=None,
    )  # fmt: skip


def _filing(quarter: date, profit: float, sales: float, published: str, **kw: object) -> dict:
    return {"symbol": "S", "quarter": quarter.isoformat(), "profit": profit, "sales": sales,
            "published": published, "revised": False, **kw}  # fmt: skip


def _table(
    published: str = "2026-11-06T18:00:00+05:30", profit: float = 125.0, **kw: object
) -> dict:
    return rr.one_per_quarter([
        _filing(QUARTER, profit, 110.0, published, **kw),
        _filing(YEAR_AGO, 100.0, 100.0, "2025-10-15T18:00:00+05:30"),
    ])  # fmt: skip


def _trades(table: dict, measure: SimpleNamespace | None = None, seen: set | None = None) -> list:
    return fw.new_trades(table, measure or _measure(), {"S": "S"}, seen or set())


def test_the_trial_starts_only_at_its_first_unseen_session() -> None:
    assert fw.FROM == START


def test_a_weekend_filing_shorts_the_first_session_of_the_trial() -> None:
    (trade,) = _trades(_table("2026-11-07T12:00:00+05:30"))
    assert trade["day"] == "2026-11-09"
    assert trade["key"] == "S:2026-09-30"


def test_the_short_makes_money_when_the_stock_falls_from_its_open() -> None:
    (trade,) = _trades(_table())
    entry, cover = 100.0 * (1 - rr.SLIPPAGE), 98.0 * (1 + rr.SLIPPAGE)
    assert trade["net"] == pytest.approx((entry - cover) / entry - 0.002)
    assert trade["excess"] == pytest.approx(trade["net"] + 0.01)  # a short gains if the market rose
    assert trade["mae"] == pytest.approx(103.0 / entry - 1)


def test_entries_before_the_trial_are_not_counted() -> None:
    assert _trades(_table("2026-11-04T18:00:00+05:30")) == []


def test_a_trade_already_recorded_is_not_recorded_twice() -> None:
    assert _trades(_table(), seen={"S:2026-09-30"}) == []


def test_a_filing_in_market_hours_is_not_this_rule() -> None:
    assert _trades(_table("2026-11-09T11:00:00+05:30")) == []


def test_numbers_below_the_rule_are_not_traded() -> None:
    assert _trades(_table(profit=124.0)) == []


def test_a_revision_is_not_traded() -> None:
    assert _trades(_table(revised=True)) == []


def test_an_illiquid_stock_is_not_traded() -> None:
    assert _trades(_table(), _measure(turnover=1e6)) == []


def test_a_session_with_no_prices_yet_waits_for_the_next_run() -> None:
    assert _trades(_table(), _measure(opening=float("nan"))) == []


def test_a_filing_after_the_last_stored_session_waits_too() -> None:
    assert _trades(_table("2026-12-30T18:00:00+05:30")) == []


class FlatCost:
    def round_trip_cost(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(total=20.0)


def _rows(days: int, per_day: int, exit_px: float = 99.0) -> list[dict]:
    calendar = pd.date_range("2026-11-09", periods=days, freq="B").date
    return [
        {"key": f"{d}:{i}", "day": d, "entry": 100.0, "exit": exit_px,
         "net": (100.0 - exit_px) / 100.0 - 0.002,
         "excess": (100.0 - exit_px) / 100.0 - 0.002 + (0.001 if n % 2 else 0.003), "mae": 0.05}
        for n, d in enumerate(calendar) for i in range(per_day)
    ]  # fmt: skip


def test_the_bar_needs_enough_days_as_well_as_enough_trades() -> None:
    assert fw.judge(_rows(10, 20), FlatCost())["status"] == "collecting"
    assert fw.judge(_rows(25, 8), FlatCost())["status"] == "passed"


def test_a_big_record_with_no_money_after_charges_fails() -> None:
    assert fw.judge(_rows(25, 8, exit_px=100.0), FlatCost())["status"] == "failed"


def test_every_amount_is_reported_beside_the_verdict() -> None:
    got = fw.judge(_rows(25, 8), FlatCost())
    assert set(got["by_amount"]) == {"10000", "25000", "50000", "100000"}


def test_an_empty_record_is_still_collecting() -> None:
    assert fw.judge([], FlatCost())["status"] == "collecting"
