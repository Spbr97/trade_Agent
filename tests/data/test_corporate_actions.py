from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from tests.data.synth import daily, sessions, with_split
from tradedesk.broker.indstocks.models import Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.corporate_actions import (
    detect_unadjusted,
    parse_nse_corporate_actions_csv,
    parse_nse_date,
    parse_purpose,
)
from tradedesk.data.models import ActionKind


@pytest.mark.parametrize(
    ("purpose", "kind", "factor"),
    [
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", ActionKind.SPLIT, Fraction(1, 5)),  # noqa: E501
        ("Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- Per Share", ActionKind.SPLIT, Fraction(1, 2)),  # noqa: E501
        ("FACE VALUE SPLIT FROM RS.10 TO RS.5", ActionKind.SPLIT, Fraction(1, 2)),
        ("Bonus 1:1", ActionKind.BONUS, Fraction(1, 2)),
        ("Bonus 3:2", ActionKind.BONUS, Fraction(2, 5)),
        ("Bonus Issue 1:4", ActionKind.BONUS, Fraction(4, 5)),
        ("Dividend - Rs 5 Per Share", ActionKind.OTHER, Fraction(1)),
        ("Rights 1:5 @ Premium Rs 100", ActionKind.OTHER, Fraction(1)),
        ("Face Value Split (Sub-Division)", ActionKind.SPLIT, Fraction(1)),  # ratio missing
    ],
)  # fmt: skip
def test_parse_purpose(purpose: str, kind: ActionKind, factor: Fraction) -> None:
    assert parse_purpose(purpose) == (kind, factor)


def test_parse_nse_date_formats() -> None:
    assert parse_nse_date("12-Sep-2026") == date(2026, 9, 12)
    assert parse_nse_date("12-09-2026") == date(2026, 9, 12)
    assert parse_nse_date("2026-09-12") == date(2026, 9, 12)
    assert parse_nse_date("-") is None and parse_nse_date("") is None


NSE_CSV = (
    """﻿SYMBOL,COMPANY NAME,SERIES,FACE VALUE,PURPOSE,EX-DATE,RECORD DATE
SBIN,State Bank of India,EQ,1,Dividend - Rs 13.70 Per Share,16-May-2025,16-May-2025
TATASTEEL,Tata Steel Limited,EQ,1,Face Value Split (Sub-Division) - From Rs 10/- Per Share """
    """To Re 1/- Per Share,28-Jul-2022,29-Jul-2022
NESTLEIND,Nestle India Limited,EQ,1,Bonus 1:1,05-Jan-2024,05-Jan-2024
SOMEDEBT,Some Bond,N1,1000,Interest Payment,01-Jan-2024,01-Jan-2024
BADDATE,Bad Date Ltd,EQ,10,Bonus 1:1,-,-
"""
)


def test_parse_nse_csv_tolerant_columns_and_bom() -> None:
    actions = parse_nse_corporate_actions_csv(NSE_CSV)
    by = {a.symbol: a for a in actions}
    assert set(by) == {"SBIN", "TATASTEEL", "NESTLEIND"}  # debt series and bad date skipped
    assert by["SBIN"].kind is ActionKind.OTHER and not by["SBIN"].adjusts_prices
    assert by["TATASTEEL"].price_factor == Fraction(1, 10)
    assert by["TATASTEEL"].ex_date == date(2022, 7, 28)
    assert by["NESTLEIND"].price_factor == Fraction(1, 2)


def test_parse_nse_csv_rejects_unknown_layout() -> None:
    with pytest.raises(ValueError, match="unrecognised"):
        parse_nse_corporate_actions_csv("a,b,c\n1,2,3\n")


def test_detect_unadjusted_split_but_not_crash() -> None:
    days = sessions(date(2025, 6, 2), 30)
    ex = days[15]
    with CandleStore() as store:
        # 1:1 bonus left unadjusted: bars from ex-date on are exactly half.
        store.upsert_candles(with_split(daily("NSE_A", days, start_price=400), ex, 0.5))
        # A real 45% crash: opens at a random level and closes elsewhere.
        crash = daily("NSE_B", days, start_price=400)
        i = 15
        crash[i] = crash[i].model_copy(
            update={"open": 300.0, "high": 310.0, "low": 200.0, "close": 220.0}
        )
        store.upsert_candles(crash)
        a = detect_unadjusted(store.load("NSE_A", Interval.D1, adjusted=False))
        b = detect_unadjusted(store.load("NSE_B", Interval.D1, adjusted=False))
    assert len(a) == 1 and a[0][0] == ex and a[0][2] in {"bonus 1:1", "split 10->5", "split 2->1"}
    assert b == []
