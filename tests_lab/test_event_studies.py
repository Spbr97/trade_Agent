"""Event studies: which filings count as an event, when the market could act on them, and the
pre-registered 60-call bar."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from tradedesk_lab import event_studies as es
from tradedesk_lab import new_listings as nl

from tradedesk.broker.indstocks.models import IST


def test_filings_are_sorted_into_families_and_later_stages_are_ignored() -> None:
    assert es.family("Rights Issue", "terms approved") == "rights"
    assert es.family("Updates", "Board approved issue of equity shares on rights basis") == "rights"
    assert es.family("Public Announcement - Buyback of Shares", "x") == "buyback"
    assert es.family("Daily Buy Back of securities", "buy back update") is None
    assert es.family("Post Buyback Public Announcement", "buyback") is None
    assert es.family("Open Offer", "Public Announcement for Open Offer") == "open_offer"
    assert es.family("Updates", "copy of Detailed Public Statement") == "open_offer"
    assert es.family("Trading Window", "closure of trading window") is None


def _row(sym: str, desc: str, at: str) -> dict:
    return {"symbol": sym, "desc": desc, "text": "", "filed_at": at}


def test_only_the_first_filing_of_a_family_counts_for_180_days() -> None:
    rows = [
        _row("A", "Rights Issue", "2023-01-02T10:00:00+05:30"),
        _row("A", "Rights Issue", "2023-02-10T10:00:00+05:30"),  # same issue, later stage
        _row("A", "Buyback", "2023-02-11T10:00:00+05:30"),  # another family counts
        _row("A", "Rights Issue", "2023-09-01T10:00:00+05:30"),  # 180+ days later: a new issue
    ]
    events = es.first_events(rows)
    assert [(f, at.date().isoformat()) for f, _, at in events] == [
        ("rights", "2023-01-02"), ("buyback", "2023-02-11"), ("rights", "2023-09-01"),
    ]  # fmt: skip


def test_a_filing_after_the_close_counts_on_the_next_session() -> None:
    sessions = [date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4)]
    assert es.signal_row(sessions, datetime(2023, 1, 2, 11, 0, tzinfo=IST)) == 0
    assert es.signal_row(sessions, datetime(2023, 1, 2, 16, 0, tzinfo=IST)) == 1
    assert es.signal_row(sessions, datetime(2022, 12, 31, 9, 0, tzinfo=IST)) == 0  # a Saturday
    assert es.signal_row(sessions, datetime(2023, 1, 4, 18, 0, tzinfo=IST)) is None


def _panels(n: int = 80) -> nl.Panels:
    days = [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]
    frame = pd.DataFrame(100.0, index=days, columns=["NSE_A"])
    return nl.Panels(frame, frame, frame, frame * 1e6)


def test_rights_events_are_also_bought_30_sessions_later() -> None:
    p = _panels()
    events = [("rights", "A", datetime(2023, 1, 3, 10, 0, tzinfo=IST)),
              ("rights", "GONE", datetime(2023, 1, 3, 10, 0, tzinfo=IST))]  # fmt: skip
    sigs = es.event_signals(p, events, {"A": "NSE_A"})
    assert sigs["rights_announce"] == [("NSE_A", 1)] and sigs["rights_after_30"] == [("NSE_A", 31)]


def test_lockin_expiries_fall_on_the_first_session_from_the_calendar_day() -> None:
    sigs = es.lockin_signals(_panels(200), {"NSE_A": 5})
    assert sigs["lockin_30"] == [("NSE_A", 35)] and sigs["lockin_180"] == [("NSE_A", 185)]


def test_the_bar_needs_60_calls_not_the_listing_studys_100() -> None:
    rng = np.random.default_rng(0)
    days = [date(2021, 1, 4) + timedelta(weeks=i) for i in range(70)]
    good = pd.DataFrame({"code": "A", "day": days, "net": 0.05 + rng.normal(0, 0.01, 70)})
    good["excess"] = good["net"]
    assert es.judge(good)["passed"] is True
    few = es.judge(good.iloc[:40])
    assert few["passed"] is False and few["fails"][0] == "only 40 calls"
