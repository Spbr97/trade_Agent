"""results_reading: the figures must come from the right lines, and no signal may use anything the
market could not yet see."""

from __future__ import annotations

import math
from datetime import date, datetime

import pytest
from tradedesk_lab import results_reading as rr

from tradedesk.broker.indstocks.models import IST


def _context(cid: str, start: str, end: str, segment: bool = False) -> str:
    seg = "<xbrli:segment><x>1</x></xbrli:segment>" if segment else ""
    return (
        f'<xbrli:context id="{cid}"><xbrli:entity>{seg}</xbrli:entity><xbrli:period>'
        f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
        "</xbrli:period></xbrli:context>"
    )


def _fact(name: str, cid: str, value: object) -> str:
    return f'<in:{name} contextRef="{cid}">{value}</in:{name}>'


def _xbrl(*parts: str) -> str:
    return (
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in="http://x">'
        + "".join(parts) + "</xbrli:xbrl>"
    )  # fmt: skip


def test_figures_come_from_the_quarter_not_the_year_to_date_or_a_segment() -> None:
    text = _xbrl(
        _context("FourD", "2026-04-01", "2026-09-30"),
        _context("Seg", "2026-07-01", "2026-09-30", segment=True),
        _context("OneD", "2026-07-01", "2026-09-30"),
        _fact("ProfitLossForPeriod", "FourD", 200),
        _fact("RevenueFromOperations", "Seg", 999),
        _fact("ProfitLossForPeriod", "OneD", 100),
        _fact("RevenueFromOperations", "OneD", 500),
    )
    assert rr.parse_figures(text) == {"profit": 100.0, "sales": 500.0}


def test_a_banks_filing_uses_its_own_labels() -> None:
    text = _xbrl(
        _context("OneD", "2026-07-01", "2026-09-30"),
        _fact("ProfitLossForThePeriod", "OneD", 70),
        _fact("InterestEarned", "OneD", 300),
    )
    assert rr.parse_figures(text) == {"profit": 70.0, "sales": 300.0}


def test_a_filing_missing_a_figure_is_unusable() -> None:
    text = _xbrl(_context("OneD", "2026-07-01", "2026-09-30"), _fact("InterestEarned", "OneD", 3))
    assert rr.parse_figures(text) is None


def test_both_lists_normalise_alike_and_a_revision_is_marked() -> None:
    old = rr.old_row({
        "symbol": "X", "toDate": "31-Dec-2024", "broadCastDate": "09-Jan-2025 21:39:43",
        "consolidated": "Non-Consolidated", "cumulative": "Non-cumulative", "seqNumber": "1",
        "xbrl": "u.xml",
    })  # fmt: skip
    new = rr.new_row({
        "symbol": "X", "qe_Date": "30-SEP-2026", "broadcast_Date": "08-Oct-2026 22:07:33",
        "consolidated": "Consolidated", "type_Sub": "Revision", "seq_Id": "2", "xbrl": "v.xml",
    })  # fmt: skip
    assert (old["quarter"], old["nature"], old["revised"]) == ("2024-12-31", "standalone", False)
    assert (new["quarter"], new["nature"], new["revised"]) == ("2026-09-30", "consolidated", True)
    assert old["published"].startswith("2025-01-09T21:39:43")


def test_a_cumulative_or_mid_quarter_filing_is_not_used() -> None:
    row = {
        "symbol": "X", "toDate": "31-Dec-2024", "broadCastDate": "09-Jan-2025 21:39:43",
        "consolidated": "Consolidated", "cumulative": "Cumulative", "seqNumber": "1", "xbrl": "u",
    }  # fmt: skip
    assert rr.old_row(row) is None
    assert rr.old_row({**row, "cumulative": "Non-cumulative", "toDate": "30-Nov-2024"}) is None


def test_a_stock_is_compared_on_one_set_of_figures() -> None:
    def row(q: str, nature: str) -> dict[str, str]:
        return {"symbol": "X", "quarter": q, "nature": nature}

    few = [row(f"20{y}-03-31", "consolidated") for y in range(20, 25)]
    assert rr.choose_nature(few) == {"X": "standalone"}
    many = [row(f"20{y}-{m}", "consolidated") for y in range(20, 24)
            for m in ("03-31", "06-30", "09-30")]  # fmt: skip
    assert rr.choose_nature(many) == {"X": "consolidated"}


SESSIONS = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 9)]  # Mon, Tue, then a Fri


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=IST)


@pytest.mark.parametrize(
    ("at", "row"),
    [
        (_at(5, 8), 0),  # before the open: that morning
        (_at(5, 8, 59), 0),
        (_at(5, 9), None),  # the market is open, so this rule does not apply
        (_at(5, 15, 29), None),
        (_at(5, 15, 30), 1),  # shut: the next session's open
        (_at(5, 21), 1),
        (_at(7, 12), 2),  # a non-session day: any hour waits for the next open
        (_at(9, 18), None),  # no next session in the data yet
    ],
)
def test_the_entry_is_the_first_open_after_publication(at: datetime, row: int | None) -> None:
    assert rr.entry_session(SESSIONS, at) == row


def test_quarters_step_back_across_a_year() -> None:
    assert rr._quarter_back(date(2026, 3, 31), 1) == date(2025, 12, 31)
    assert rr._quarter_back(date(2026, 9, 30), 4) == date(2025, 9, 30)
    assert rr._quarter_back(date(2026, 6, 30), 9) == date(2024, 3, 31)


def _filing(q: date, profit: float, sales: float, at: str | None = None, **kw: object) -> dict:
    published = at or f"{q.isoformat()}T18:00:00+05:30"
    return {"symbol": "X", "quarter": q.isoformat(), "profit": profit, "sales": sales,
            "published": published, "revised": False, **kw}  # fmt: skip


def _table(now: dict, base: dict) -> dict:
    return rr.one_per_quarter([now, base])


Q, YA = date(2026, 9, 30), date(2025, 9, 30)
LATER = "2026-10-15T18:00:00+05:30"


def test_strong_needs_both_profit_and_sales() -> None:
    def strong(profit: float, sales: float) -> bool:
        table = _table(_filing(Q, profit, sales, LATER), _filing(YA, 100, 100))
        return rr.classify(table, "X", Q)["strong"]

    assert strong(125, 110)
    assert not strong(124, 110)
    assert not strong(125, 109)


def test_weak_needs_falling_profit_and_no_sales_growth() -> None:
    def weak(profit: float, sales: float) -> bool:
        table = _table(_filing(Q, profit, sales, LATER), _filing(YA, 100, 100))
        return rr.classify(table, "X", Q)["weak"]

    assert weak(75, 100)
    assert not weak(76, 100)
    assert not weak(75, 101)


def test_a_filing_with_no_usable_base_cannot_signal() -> None:
    assert rr.classify(rr.one_per_quarter([_filing(Q, 1, 1, LATER)]), "X", Q) is None
    loss = _table(_filing(Q, 125, 110, LATER), _filing(YA, -5, 100))
    assert rr.classify(loss, "X", Q) is None


def test_a_revision_a_late_filing_or_a_later_base_cannot_signal() -> None:
    revised = _table(_filing(Q, 125, 110, LATER, revised=True), _filing(YA, 100, 100))
    assert rr.classify(revised, "X", Q) is None
    late = _table(_filing(Q, 125, 110, "2026-12-29T18:00:00+05:30"), _filing(YA, 100, 100))
    assert rr.classify(late, "X", Q) is None
    base_after = _table(
        _filing(Q, 125, 110, LATER), _filing(YA, 100, 100, "2026-10-20T18:00:00+05:30")
    )
    assert rr.classify(base_after, "X", Q) is None


def test_an_original_is_preferred_over_an_earlier_revision() -> None:
    table = rr.one_per_quarter(
        [_filing(Q, 1, 1, "2026-10-01T18:00:00+05:30", revised=True), _filing(Q, 2, 2, LATER)]
    )
    assert table[("X", Q)]["profit"] == 2


def _history(last: float) -> dict:
    """13 quarters: year-on-year changes alternate 9 and 11, then the newest change is `last`."""

    quarters = [rr._quarter_back(Q, n) for n in range(13)][::-1]
    profits: dict[date, float] = {}
    for i, q in enumerate(quarters):
        if i < 4:
            profits[q] = 100.0
        else:
            profits[q] = profits[quarters[i - 4]] + (9 if i % 2 else 11)
    profits[Q] = profits[quarters[-5]] + last
    return rr.one_per_quarter(
        [_filing(q, v, 100, LATER if q == Q else None) for q, v in profits.items()]
    )


def test_surprise_measures_the_change_against_its_own_past_spread() -> None:
    assert rr.surprise(_history(40), "X", Q) == pytest.approx(40 / 1.0690, rel=1e-3)
    assert rr.surprise(_history(10), "X", Q) == pytest.approx(10 / 1.0690, rel=1e-3)


def test_surprise_needs_the_full_history() -> None:
    table = _table(_filing(Q, 125, 110, LATER), _filing(YA, 100, 100))
    assert rr.surprise(table, "X", Q) is None


def test_the_gap_rules_split_on_the_open() -> None:
    numbers = {"strong": True, "sue": False, "weak": False}
    assert rr.rules_hit(numbers, 0.0) == ["strong", "quiet_strong"]
    assert rr.rules_hit(numbers, 0.02) == ["strong", "quiet_strong"]
    assert rr.rules_hit(numbers, 0.025) == ["strong"]
    assert rr.rules_hit(numbers, 0.03) == ["strong", "loud_strong"]
    assert rr.rules_hit(numbers, -0.02) == ["strong"]
    assert rr.rules_hit({"strong": False, "sue": False, "weak": True}, 0.05) == ["weak_short"]


CLOSES = [100.0, 101.0, 110.0, 120.0, 108.0, 130.0]


def test_the_day_exit_sells_at_that_close() -> None:
    assert rr.exit_row("day", 100.0, CLOSES, 1) == (1, True)


def test_a_fixed_hold_counts_the_entry_session() -> None:
    closes = [100.0] * 25
    assert rr.exit_row("20", 100.0, closes, 1) == (20, False)
    assert rr.exit_row("60", 100.0, closes, 1) is None  # history stops first


def test_the_runner_sells_at_once_if_the_first_close_is_below_the_entry() -> None:
    assert rr.exit_row("runner", 102.0, [101.0] * 70, 0) == (0, True)


def test_the_runner_trails_from_the_highest_close() -> None:
    closes = [100.0, 101.0, 110.0, 120.0, 108.0] + [130.0] * 70
    assert rr.exit_row("runner", 100.0, closes, 0) == (4, False)  # 108 is 10% under 120


def test_the_runner_stops_at_the_sixtieth_session() -> None:
    assert rr.exit_row("runner", 100.0, [101.0] * 70, 0) == (59, False)


def test_a_runner_with_too_little_history_is_left_out() -> None:
    assert rr.exit_row("runner", 100.0, [101.0] * 30, 0) is None


def test_a_missing_close_is_not_traded_or_trailed_through() -> None:
    assert rr.exit_row("day", 100.0, [math.nan], 0) is None
    closes = [100.0, math.nan, 89.0] + [89.0] * 70
    assert rr.exit_row("runner", 100.0, closes, 0) == (2, False)
