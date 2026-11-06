"""bulk_block_deals: real CSV quirks, same-day churn dropped, and only prior knowledge used."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from tradedesk_lab import bulk_block_deals as bb

HEAD = (
    '"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ",'
    '"Trade Price / Wght. Avg. Price ","Remarks "\n'
)


def _line(day: str, symbol: str, client: str, side: str, qty: str, price: str) -> str:
    return f'"{day}","{symbol}","Name","{client}","{side}","{qty}","{price}","-"\n'


def test_the_real_csv_shape_is_read_with_its_padded_names_bom_and_commas() -> None:
    deal = _line("06-NOV-2026", "AAA", "hdfc  mutual fund", "BUY", "1,00,000", "1,096.5")
    text = chr(0xFEFF) + HEAD + deal
    (row,) = bb.parse_csv(text, "bulk")
    assert row["day"] == date(2026, 11, 6)
    assert row["client"] == "HDFC MUTUAL FUND"
    assert row["value"] == pytest.approx(100_000 * 1096.5)


def test_a_bad_row_is_skipped_and_not_guessed() -> None:
    bad_date = _line("bad", "AAA", "X", "BUY", "1", "1")
    bad_side = _line("06-NOV-2026", "AAA", "X", "HOLD", "1", "1")
    text = HEAD + bad_date + bad_side
    assert bb.parse_csv(text, "bulk") == []


def _deal(symbol: str, client: str, side: str, value: float = 1e8, day: date = date(2026, 1, 5),
          kind: str = "bulk") -> dict:  # fmt: skip
    return {"kind": kind, "day": day, "symbol": symbol, "client": client, "side": side,
            "value": value}  # fmt: skip


def test_a_client_on_both_sides_the_same_day_is_churn_and_dropped() -> None:
    rows = [_deal("A", "FAST", "BUY"), _deal("A", "FAST", "SELL"), _deal("B", "SLOW", "BUY")]
    assert bb.rule_days(rows, "holder_bulk") == [("B", date(2026, 1, 5))]


def test_two_buys_by_one_client_in_a_day_add_up() -> None:
    rows = [_deal("A", "SLOW", "BUY", 3e7), _deal("A", "SLOW", "BUY", 3e7)]
    assert bb.rule_days(rows, "holder_bulk") == [("A", date(2026, 1, 5))]
    assert bb.rule_days([_deal("A", "SLOW", "BUY", 3e7)], "holder_bulk") == []


@pytest.mark.parametrize(
    ("client", "is_fund"),
    [
        ("HDFC MUTUAL FUND", True),
        ("LIFE INSURANCE CORPORATION OF INDIA", True),
        ("SBI MF", True),
        ("M7 GLOBAL FUND PCC", False),
        ("GRAVITON RESEARCH CAPITAL LLP", False),
    ],
)
def test_only_named_domestic_funds_count_as_funds(client: str, is_fund: bool) -> None:
    assert bool(bb.rule_days([_deal("A", client, "BUY", 1.0)], "fund_bulk")) is is_fund


def test_a_fund_sale_is_not_a_fund_buy_and_block_is_kept_apart_from_bulk() -> None:
    assert bb.rule_days([_deal("A", "HDFC MUTUAL FUND", "SELL")], "fund_bulk") == []
    block = [_deal("A", "HDFC MUTUAL FUND", "BUY", kind="block")]
    assert bb.rule_days(block, "fund_bulk") == []
    assert bb.rule_days(block, "fund_block") == [("A", date(2026, 1, 5))]


def test_the_short_rule_needs_a_big_one_sided_sale() -> None:
    assert bb.rule_days([_deal("A", "X", "SELL")], "seller_short") == [("A", date(2026, 1, 5))]
    assert bb.rule_days([_deal("A", "X", "SELL", 1e6)], "seller_short") == []
    assert bb.rule_days([_deal("A", "X", "BUY")], "seller_short") == []


SESSIONS = [date(2026, 1, 1) + timedelta(days=i) for i in range(60)]


def test_a_repeat_buyer_signals_on_the_second_buy_not_the_first() -> None:
    rows = [_deal("A", "X", "BUY", day=SESSIONS[5]), _deal("A", "X", "BUY", day=SESSIONS[9])]
    assert bb.repeat_signals(rows, SESSIONS) == [("A", 9)]


def test_a_repeat_buy_outside_the_window_does_not_count() -> None:
    rows = [_deal("A", "X", "BUY", day=SESSIONS[5]), _deal("A", "X", "BUY", day=SESSIONS[30])]
    assert bb.repeat_signals(rows, SESSIONS) == []


def test_two_different_clients_are_not_a_repeat() -> None:
    rows = [_deal("A", "X", "BUY", day=SESSIONS[5]), _deal("A", "Y", "BUY", day=SESSIONS[9])]
    assert bb.repeat_signals(rows, SESSIONS) == []


def test_the_cooldown_keeps_the_earliest_call() -> None:
    assert bb.apply_cooldown([("A", 25), ("A", 5), ("A", 24), ("B", 6)]) == [
        ("A", 5), ("B", 6), ("A", 25),
    ]  # fmt: skip


def test_a_deal_on_a_non_session_day_makes_no_signal() -> None:
    sessions = [date(2026, 1, 5), date(2026, 1, 7)]
    rows = [_deal("A", "X", "BUY", day=date(2026, 1, 6))]
    assert bb.signals_for(rows, "holder_bulk", sessions) == []


def test_year_counts_show_coverage_and_fund_buys() -> None:
    rows = [_deal("A", "HDFC MUTUAL FUND", "BUY"), _deal("A", "X", "SELL", kind="block")]
    assert bb.year_counts(rows) == {"2026": {"bulk": 1, "fund_buys": 1, "block": 1}}


def test_one_sided_is_empty_safe() -> None:
    assert isinstance(bb.one_sided([]), pd.DataFrame)
    assert bb.rule_days([], "fund_bulk") == []
