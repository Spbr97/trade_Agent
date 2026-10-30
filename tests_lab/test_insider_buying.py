"""Insider buying: only an insider's own open-market purchase counts, dated by when NSE
published it, and each rule fires at most once per stock per 20 sessions."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from tradedesk_lab import insider_buying as ib

from tradedesk.broker.indstocks.models import IST


def _row(**kw) -> dict:
    base = {"symbol": "A", "acqName": "P One", "tdpTransactionType": "Buy",
            "acqMode": "Market Purchase", "personCategory": "Promoter Group",
            "secType": "Equity Shares", "derivativeType": "-", "secVal": "2500000",
            "date": "04-Jan-2023 11:05", "pid": "1", "did": "1", "secAcq": "100"}  # fmt: skip
    return {**base, **kw}


def test_only_an_insiders_own_market_purchase_qualifies() -> None:
    assert ib.qualifying(_row())
    assert ib.qualifying(_row(personCategory="Director"))
    assert not ib.qualifying(_row(tdpTransactionType="Sell"))
    assert not ib.qualifying(_row(acqMode="ESOP"))
    assert not ib.qualifying(_row(acqMode="Inter-se-Transfer"))
    assert not ib.qualifying(_row(personCategory="Employees/Designated Employees"))
    assert not ib.qualifying(_row(derivativeType="Futures"))


def test_a_disclosure_is_dated_when_nse_published_it() -> None:
    assert ib.published_at(_row()) == datetime(2023, 1, 4, 11, 5, tzinfo=IST)
    assert ib.published_at(_row(date="bad")) is None


SESSIONS = [date(2023, 1, 2) + timedelta(days=i) for i in range(60)]


def _frame(rows: list[dict]) -> pd.DataFrame:
    return ib.session_buys(ib.buys(rows), SESSIONS)


def test_buys_after_the_close_count_on_the_next_session_and_add_up() -> None:
    frame = _frame([_row(date="02-Jan-2023 10:00"), _row(date="02-Jan-2023 17:00", pid="2"),
                    _row(date="03-Jan-2023 09:30", pid="3", acqName="P Two")])  # fmt: skip
    day1 = frame[frame["i"] == 1].iloc[0]
    assert day1["value"] == 5_000_000 and day1["people"] == frozenset({"p one", "p two"})


def test_rules_fire_once_per_stock_per_cooldown() -> None:
    rows = [_row(date=f"{d:02d}-Jan-2023 10:00", pid=str(d)) for d in (2, 3, 4, 30)]
    by = _frame(rows)
    assert ib.rule_signals(by, SESSIONS, "any_buy") == [("A", 0), ("A", 28)]
    assert ib.rule_signals(by, SESSIONS, "big_buy") == []  # Rs 25 lakh < Rs 1 crore


def test_a_cluster_needs_a_second_insider_within_30_days() -> None:
    one = _frame([_row(date="02-Jan-2023 10:00"), _row(date="10-Jan-2023 10:00", pid="2")])
    assert ib.rule_signals(one, SESSIONS, "cluster") == []
    two = _frame([_row(date="02-Jan-2023 10:00"),
                  _row(date="10-Jan-2023 10:00", pid="2", acqName="P Two")])  # fmt: skip
    assert ib.rule_signals(two, SESSIONS, "cluster") == [("A", 8)]


def test_heavy_compares_the_buy_with_a_days_usual_trading() -> None:
    by = _frame([_row()])
    turnover = pd.DataFrame(4_000_000.0, index=SESSIONS, columns=["NSE_A"])
    assert ib.rule_signals(by, SESSIONS, "heavy", turnover, {"A": "NSE_A"}) == [("A", 2)]
    turnover[:] = 6_000_000.0  # Rs 25 lakh is under half of Rs 60 lakh
    assert ib.rule_signals(by, SESSIONS, "heavy", turnover, {"A": "NSE_A"}) == []
