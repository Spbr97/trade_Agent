"""news_trial.py: a headline counts only from when we could have seen it, every source ships
disabled, and sources are scored against the session's real movers."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from tradedesk_lab import news_trial as nt

from tradedesk.broker.indstocks.models import IST

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>IDBI Bank shares surge ahead of Q2 earnings</title><link>https://x/1</link>
<pubDate>Tue, 14 Oct 2026 03:30:00 GMT</pubDate></item>
<item><title>Transformers &amp; Rectifiers Q2 profit jumps</title><link>https://x/2</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Tata Elxsi results beat estimates</title><link href="https://y/1"/>
<updated>2026-10-13T12:10:00Z</updated></entry></feed>"""

NAMES = {
    "NSE_1476": "IDBI Bank Limited",
    "NSE_3411": "Tata Elxsi Limited",
    "NSE_15174": "Transformers And Rectifiers (India) Limited",
    "NSE_9": "ABC Ltd",  # too short to be distinctive: never matched
}


def test_rss_and_atom_parse_with_their_own_times() -> None:
    rss = nt.parse_feed(RSS)
    atom = nt.parse_feed(ATOM)
    assert [i["title"] for i in rss][0].startswith("IDBI Bank")
    assert rss[0]["published"] == datetime(2026, 10, 14, 9, 0, tzinfo=IST)
    assert rss[1]["published"] is None
    assert atom[0]["link"] == "https://y/1"
    assert atom[0]["published"] == datetime(2026, 10, 13, 17, 40, tzinfo=IST)


def test_company_names_match_titles_but_short_names_never_do() -> None:
    patterns = nt.company_patterns(NAMES)
    assert nt.match_companies("IDBI Bank shares surge", patterns) == ["NSE_1476"]
    assert nt.match_companies("Transformers & Rectifiers Q2 profit", patterns) == ["NSE_15174"]
    assert nt.match_companies("Tata Elxsi results", patterns) == ["NSE_3411"]
    assert nt.match_companies("ABC of investing", patterns) == []


def test_a_headline_counts_only_from_when_we_could_have_seen_it() -> None:
    seen_late = {
        "seen_at": "2026-10-14T10:00:00+05:30",
        "published_at": "2026-10-14T08:00:00+05:30",
    }
    back_dated = {
        "seen_at": "2026-10-14T08:00:00+05:30",
        "published_at": "2026-10-14T11:00:00+05:30",
    }
    assert nt._headline_time(seen_late) == datetime(2026, 10, 14, 10, 0, tzinfo=IST)
    assert nt._headline_time(back_dated) == datetime(2026, 10, 14, 11, 0, tzinfo=IST)


def test_sources_are_scored_against_real_movers_before_the_open() -> None:
    headlines = [
        {"source": "a", "seen_at": "2026-10-14T08:00:00+05:30", "published_at": None,
         "codes": ["NSE_1476"]},
        {"source": "a", "seen_at": "2026-10-14T13:30:00+05:30", "published_at": None,
         "codes": ["NSE_13643"]},
        {"source": "b", "seen_at": "2026-10-14T08:00:00+05:30", "published_at": None,
         "codes": ["NSE_5"]},
    ]  # fmt: skip
    result = nt.score_session(
        date(2026, 10, 14), ["NSE_1476", "NSE_13643", "NSE_14766"], headlines, ["a", "b"]
    )
    a, b = result["sources"]["a"], result["sources"]["b"]
    assert (a["movers_seen_pre_open"], a["movers_seen_by_close"]) == (1, 2)
    assert (b["movers_seen_by_close"], b["matched_non_movers"]) == (0, 1)


def test_nothing_is_fetched_until_a_source_is_enabled(tmp_path) -> None:
    status = nt.fetch(sources=[], root=tmp_path)
    assert status["note"].startswith("no source enabled")
    assert not (tmp_path / "headlines").exists()


def test_every_configured_source_ships_disabled() -> None:
    sources = nt.load_sources(Path(nt.CONFIG))
    assert sources and all(s["enabled"] is False for s in sources)
