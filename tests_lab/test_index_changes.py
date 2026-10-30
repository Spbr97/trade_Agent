"""Index changes: release parsing, a stock's highest tier, the forced-buying windows and the
one-release-one-unit bar."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from tradedesk_lab import index_changes as ic

RELEASE = """
PRESS RELEASE
Mumbai, February 23, 2026
Replacements in indices
The Index Maintenance Sub -Committee (Equity) of NSE Indices Limited has decided to make
replacement of stocks in various indices as part of its periodic review as listed hereunder.
These changes shall become effective from March 30, 2026 (close of March 27, 2026).
A. Replacements on account of semi-annual review of broad market indices:
1) Nifty 50
The following companies are being excluded:
Sr. No. Company Name Symbol
1 Old Large Ltd. OLDLARGE
The following companies are being included:
Sr. No. Company Name Symbol
1 Bajaj Auto Ltd. BAJAJ-AUTO
2) Nifty 500
The following companies are being included:
Sr. No. Company Name Symbol
1 Bajaj Auto Ltd. BAJAJ-AUTO
2 Mahindra & Mahindra Ltd. M&M
3) Nifty Bank
The following companies are being included:
1 Some Bank Ltd. SOMEBANK
"""


def test_an_equity_release_gives_its_dates_and_changes_per_index() -> None:
    rel = ic.parse(RELEASE)
    assert rel is not None
    assert rel["released"] == date(2026, 2, 23) and rel["effective"] == date(2026, 3, 30)
    assert rel["changes"]["nifty 50"] == {"included": ["BAJAJ-AUTO"], "excluded": ["OLDLARGE"]}
    assert rel["changes"]["nifty 500"]["included"] == ["BAJAJ-AUTO", "M&M"]
    assert ic.parse("Changes in Nifty Fixed Income indices, effective from March 2, 2026") is None


def test_each_stock_counts_once_at_its_highest_tier_and_sector_indices_are_left_out() -> None:
    evs = ic.events([ic.parse(RELEASE)])
    got = {(e["symbol"], e["direction"]): e["tier"] for e in evs}
    assert got == {("BAJAJ-AUTO", "add"): "large", ("M&M", "add"): "broad",
                   ("OLDLARGE", "drop"): "large"}  # fmt: skip


def test_the_runup_ends_at_the_funds_buying_close_and_after_trades_hold_20() -> None:
    sessions = [date(2026, 2, 2) + timedelta(days=i) for i in range(80)]
    ev = {"released": date(2026, 2, 23), "effective": date(2026, 3, 30)}
    entry, exit_row = ic.windows(sessions, ev, "add_runup")
    assert sessions[entry] == date(2026, 2, 24) and sessions[exit_row] == date(2026, 3, 29)
    entry, exit_row = ic.windows(sessions, ev, "drop_after")
    assert sessions[entry] == date(2026, 3, 30) and exit_row - entry == ic.HOLD - 1
    late = {"released": date(2026, 4, 20), "effective": date(2026, 4, 25)}
    assert ic.windows(sessions, late, "add_after") is None  # not 20 sessions of data left


def test_one_release_is_one_unit_of_evidence() -> None:
    rows = [{"released": date(2020, 1, 1) + timedelta(days=30 * r), "net": 0.03 + 0.001 * (r % 3),
             "excess": 0.02 + 0.001 * (r % 3)} for r in range(14) for _ in range(5)]  # fmt: skip
    verdict = ic.judge(pd.DataFrame(rows))
    assert verdict["calls"] == 70 and verdict["releases"] == 14 and verdict["passed"] is True
    few = ic.judge(pd.DataFrame(rows[:50]))  # 50 calls but only 10 releases
    assert few["passed"] is False and few["fails"][0] == "only 50 calls from 10 releases"
