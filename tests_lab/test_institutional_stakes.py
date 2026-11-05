"""institutional_stakes: the parser must count the right lines in both XBRL formats, and no signal
may use anything the market could not yet see."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import institutional_stakes as st
from tradedesk_lab import new_listings as nl

from tradedesk.broker.indstocks.models import IST


def _context(context_id: str, member: str | None = None, typed: bool = False) -> str:
    inner = ""
    if member:
        inner = f'<xbrldi:explicitMember dimension="a">in-bse-shp:{member}</xbrldi:explicitMember>'
    if typed:
        inner = '<xbrldi:typedMember dimension="in-bse-shp:Axis"><x>1</x></xbrldi:typedMember>'
    return (
        f'<xbrli:context id="{context_id}"><xbrli:entity><xbrli:segment>{inner}</xbrli:segment>'
        "</xbrli:entity></xbrli:context>"
    )


def _fact(name: str, context: str, value: object) -> str:
    return f'<in-bse-shp:{name} contextRef="{context}">{value}</in-bse-shp:{name}>'


def _xbrl(contexts: list[str], facts: list[str]) -> str:
    return (
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:in-bse-shp="http://bse/shp">'
        + "".join(contexts) + "".join(facts) + "</xbrli:xbrl>"
    )  # fmt: skip


def test_the_old_format_counts_public_fpi_and_never_promoter_fpi_or_detail_lines() -> None:
    text = _xbrl(
        [
            _context("ForeignPortfolioInvestorI", "ForeignPortfolioInvestorMember"),  # promoter
            _context("InstitutionsForeignPortfolioInvestorI",
                     "InstitutionsForeignPortfolioInvestorMember"),
            _context("MutualFundsOrUtiI", "MutualFundsOrUtiMember"),
            _context("InsuranceCompaniesI", "InsuranceCompaniesMember"),
            _context("ShareholdingPatternI", "ShareholdingPatternMember"),
            _context("DetailsOfSharesHeldByMutualFundsOrUti001I", typed=True),
        ],
        [
            _fact("NumberOfShares", "ForeignPortfolioInvestorI", 99),
            _fact("NumberOfShares", "InstitutionsForeignPortfolioInvestorI", 10),
            _fact("NumberOfShares", "MutualFundsOrUtiI", 20),
            _fact("NumberOfShareholders", "MutualFundsOrUtiI", 7),
            _fact("NumberOfShares", "InsuranceCompaniesI", 5),
            _fact("NumberOfShares", "ShareholdingPatternI", 200),
            _fact("NumberOfShares", "DetailsOfSharesHeldByMutualFundsOrUti001I", 15),
            _fact("NumberOfShareholders", "DetailsOfSharesHeldByMutualFundsOrUti001I", 1),
        ],
    )  # fmt: skip
    assert st.parse_xbrl(text) == {"fpi": 5.0, "mf": 10.0, "inst": 17.5, "mf_holders": 7.0}


def test_the_new_format_adds_both_fpi_categories_and_ignores_its_percent_scale() -> None:
    text = _xbrl(
        [
            _context("One", "InstitutionsForeignPortfolioInvestorCategoryOneMember"),
            _context("Two", "InstitutionsForeignPortfolioInvestorCategoryTwoMember"),
            _context("MF", "MutualFundsOrUTIMember"),
            _context("All", "ShareholdingPatternMember"),
            _context("MF15", typed=True),
        ],
        [
            _fact("NumberOfShares", "One", 30),
            _fact("NumberOfShares", "Two", 10),
            _fact("NumberOfShares", "MF", 40),
            _fact("ShareholdingAsAPercentageOfTotalNumberOfShares", "MF", 0.2),
            _fact("NumberOfShareholders", "MF", 3),
            _fact("NumberOfShares", "All", 200),
            _fact("NumberOfShares", "MF15", 40),
        ],
    )
    assert st.parse_xbrl(text) == {"fpi": 20.0, "mf": 20.0, "inst": 40.0, "mf_holders": 3.0}


def test_a_filing_with_no_share_total_is_unusable_and_a_nil_line_counts_as_none() -> None:
    assert st.parse_xbrl(_xbrl([], [])) is None
    total = _fact("NumberOfShares", "All", 5)
    text = _xbrl([_context("All", "ShareholdingPatternMember")], [total])
    assert st.parse_xbrl(text) == {"fpi": 0.0, "mf": 0.0, "inst": 0.0, "mf_holders": 0.0}


def test_only_a_quarter_end_filing_is_used() -> None:
    row = {
        "symbol": "X", "date": "30-SEP-2023", "broadcastDate": "19-OCT-2023 18:37:52",
        "submissionDate": "19-OCT-2023", "revisedStatus": "-", "recordId": 1, "xbrl": "u.xml",
    }  # fmt: skip
    got = st.filing_fields(row)
    assert got is not None
    assert (got["quarter"], got["submitted"], got["revised"]) == ("2023-09-30", "2023-10-19", False)
    assert got["published"].startswith("2023-10-19T18:37:52")
    assert st.filing_fields({**row, "date": "22-JUN-2023"}) is None  # a mid-quarter filing
    assert st.filing_fields({**row, "date": "31-OCT-2023"}) is None  # month end, not quarter end
    assert st.filing_fields({**row, "revisedStatus": "Revised"})["revised"] is True


def test_the_previous_quarter_is_the_calendar_one() -> None:
    assert st._previous_quarter(date(2023, 9, 30)) == date(2023, 6, 30)
    assert st._previous_quarter(date(2023, 3, 31)) == date(2022, 12, 31)


def _filing(quarter: str, published: str, **stakes: object) -> dict[str, object]:
    return {
        "symbol": "X", "quarter": quarter, "published": f"{published}T18:00:00+05:30",
        "submitted": published, "revised": False, "record_id": quarter + published,
        "fpi": 5.0, "mf": 5.0, "inst": 10.0, "mf_holders": 3.0, **stakes,
    }  # fmt: skip


def _two(**stakes: object) -> list[dict[str, object]]:
    return [_filing("2023-06-30", "2023-07-15"), _filing("2023-09-30", "2023-10-15", **stakes)]


@pytest.mark.parametrize(
    ("rule", "hit", "miss"),
    [
        ("fpi_up", {"fpi": 6.0}, {"fpi": 5.9}),
        ("mf_up", {"mf": 6.0}, {"mf": 5.9}),
        ("both_up", {"fpi": 5.25, "mf": 5.25}, {"fpi": 6.0, "mf": 5.2}),
        ("breadth", {"mf": 5.1, "mf_holders": 5.0}, {"mf": 5.0, "mf_holders": 9.0}),
    ],
)
def test_each_rule_triggers_at_its_threshold(rule: str, hit: dict, miss: dict) -> None:
    assert [s for s, _ in st.stake_signals(_two(**hit), rule)] == ["X"]
    assert st.stake_signals(_two(**miss), rule) == []


def test_discovery_needs_an_under_owned_stock() -> None:
    low = [
        _filing("2023-06-30", "2023-07-15", inst=4.0),
        _filing("2023-09-30", "2023-10-15", inst=5.0),
    ]
    assert st.stake_signals(low, "discovery")
    high = [
        _filing("2023-06-30", "2023-07-15", inst=5.0),
        _filing("2023-09-30", "2023-10-15", inst=9.0),
    ]
    assert st.stake_signals(high, "discovery") == []


def test_two_quarters_needs_a_rise_in_both_and_the_total() -> None:
    def rows(a: float, b: float, c: float) -> list[dict[str, object]]:
        return [
            _filing("2023-03-31", "2023-04-15", inst=a),
            _filing("2023-06-30", "2023-07-15", inst=b),
            _filing("2023-09-30", "2023-10-15", inst=c),
        ]

    assert len(st.stake_signals(rows(10, 10.5, 11), "two_quarters")) == 1
    assert st.stake_signals(rows(10, 9.5, 11), "two_quarters") == []  # fell, then rose
    assert st.stake_signals(rows(10, 10.2, 10.4), "two_quarters") == []  # too little


def test_the_signal_time_is_publication_not_the_quarter_end() -> None:
    (_, at), = st.stake_signals(_two(fpi=9.0), "fpi_up")
    assert at == datetime(2023, 10, 15, 18, 0, tzinfo=IST)


def test_a_gap_in_the_quarters_gives_no_signal() -> None:
    rows = [_filing("2023-03-31", "2023-04-15"), _filing("2023-09-30", "2023-10-15", fpi=9.0)]
    assert st.stake_signals(rows, "fpi_up") == []


def test_a_revision_or_a_backfilled_filing_never_signals() -> None:
    revised = _two(fpi=9.0, revised=True)
    assert st.stake_signals(revised, "fpi_up") == []
    backfill = _two(fpi=9.0)
    backfill[1]["published"] = "2024-01-06T23:25:01+05:30"  # 83 days after submission
    assert st.stake_signals(backfill, "fpi_up") == []


def test_a_base_published_after_the_filing_cannot_be_used() -> None:
    rows = [_filing("2023-06-30", "2023-11-01"), _filing("2023-09-30", "2023-10-15", fpi=9.0)]
    assert st.stake_signals(rows, "fpi_up") == []


def test_an_original_filing_is_preferred_over_a_revision() -> None:
    original = _filing("2023-06-30", "2023-07-15")
    revision = _filing("2023-06-30", "2023-07-01", revised=True, fpi=50.0)
    table = st.one_per_quarter([revision, original])
    assert table[("X", date(2023, 6, 30))]["fpi"] == 5.0


def _panels(forward: dict[str, float]) -> tuple[nl.Panels, pd.DataFrame]:
    """Ten stocks whose prior 60-session returns rank by name; `forward` sets the gain from row
    61's open to row 62's close. Row 60 is the signal session."""

    days = pd.date_range("2024-01-01", periods=63).date
    names = [f"S{i}" for i in range(10)]
    close = pd.DataFrame(100.0, index=days, columns=names)
    for i, name in enumerate(names):
        close.loc[days[60:], name] = 100.0 * (1 + 0.1 * i)
    for name, gain in forward.items():
        close.loc[days[62], name] = close.loc[days[61], name] * (1 + gain)
    volume = pd.DataFrame(1e7, index=days, columns=names)
    panel = nl.Panels(close.copy(), close.copy(), close, volume)
    return panel, (close * volume).rolling(20).mean().shift(1)


def test_a_stock_is_compared_with_stocks_that_had_risen_as_much_and_not_others() -> None:
    p, turnover = _panels({"S8": 0.10, "S9": 0.30, "S0": 5.0})
    peers = st.Peers(p, turnover)
    # S8 and S9 are the top fifth; S0's huge gain is in the bottom fifth and must not count.
    assert peers.excess("S9", 60, 2, 0.0, np.inf, 0.30) == pytest.approx(0.30 - 0.20)


def test_a_stock_outside_the_universe_has_no_peer_figure() -> None:
    p, turnover = _panels({})
    assert np.isnan(st.Peers(p, turnover).excess("S9", 60, 2, 1e15, np.inf, 0.1))


def _calls(net: float, excess: list[float], peer: float, start: str = "2023-01-02") -> pd.DataFrame:
    days = pd.date_range(start, periods=len(excess), freq="7D").date
    return pd.DataFrame(
        {"code": "X", "day": days, "net": net, "excess": excess, "peer": peer}
    ).assign(peer=lambda f: f["peer"] + np.resize([0.01, -0.01], len(f)))


def test_a_rule_that_only_marks_strength_fails_the_peer_bar() -> None:
    excess = list(np.resize([0.03, 0.05], 120))
    passing = st.judge(_calls(0.04, excess, 0.03))
    assert passing["passed"], passing["fails"]
    weak = st.judge(_calls(0.04, excess, -0.01))
    assert not weak["passed"]
    assert any("equally strong" in f for f in weak["fails"])


def test_a_rule_that_works_in_too_few_quarters_fails() -> None:
    # Good only in the last 40% of the quarters, and enough to carry both halves' averages.
    n = 150
    excess = [(-0.001 if i < n * 0.65 else 0.2) + (0.001 if i % 2 else 0) for i in range(n)]
    got = st.judge(_calls(0.04, excess, 0.03))
    assert got["quarters_positive_share"] < 0.6
    assert any("of quarters" in f for f in got["fails"])


def test_the_middle_format_with_nses_misspelt_labels_is_read() -> None:
    text = _xbrl(
        [
            _context("One", "InstitutionsForeignPortfolioInvestorCatergoryOneMember"),
            _context("Two", "InstitutionsForeignPortfolioInvestorCatergoryTwoMember"),
            _context("Promoter", "ForeignPortfolioInvestorMember"),
            _context("All", "ShareholdingPatternMember"),
        ],
        [
            _fact("NumberOfShares", "One", 30),
            _fact("NumberOfShares", "Two", 10),
            _fact("NumberOfShares", "Promoter", 99),
            _fact("NumberOfShares", "All", 200),
        ],
    )
    assert st.parse_xbrl(text)["fpi"] == 20.0


def test_a_quarter_where_most_filings_show_no_fpi_is_flagged() -> None:
    rows = [{"quarter": "2023-09-30", "fpi": 0.0}] * 60
    rows += [{"quarter": "2023-12-31", "fpi": 2.0}] * 60
    assert st.suspect_quarters(rows) == {"2023-09-30": 1.0}
    assert st.suspect_quarters([{"quarter": "2023-09-30", "fpi": 0.0}] * 10) == {}
