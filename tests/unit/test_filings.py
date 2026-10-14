"""filings.py: exchange filings are stored once per seq_id and only ever read back for the
time window asked for - a study can never see a filing filed after its decision time."""

from __future__ import annotations

from datetime import datetime

from tradedesk.broker.indstocks.models import IST
from tradedesk.filings import classify, load, parse_rows, previous_close, since_last_close, store


def _row(symbol: str, stamp: str, desc: str, text: str = "", seq: str = "1") -> dict:
    return {"symbol": symbol, "sort_date": stamp, "desc": desc, "attchmntText": text, "seq_id": seq}


def test_categories_come_from_the_exchange_subject_line() -> None:
    assert (
        classify("Outcome of Board Meeting", "submitted the financial results for Q2") == "results"
    )
    assert classify("Bagging/Receiving of orders/contracts", "") == "order_win"
    assert classify("Acquisition", "") == "corporate_action"
    assert classify("News Verification", "sought clarification") == "news_clarification"
    assert classify("Press Release", "") == "press_release"
    assert classify("Copy of Newspaper Publication", "") == "routine"


def test_rows_without_identity_or_time_are_dropped() -> None:
    rows = [
        _row("ELECON", "2026-10-14 13:19:48", "Outcome of Board Meeting", "financial results", "9"),
        _row("", "2026-10-14 13:19:48", "x"),
        _row("X", "not a time", "x", seq="2"),
    ]
    parsed = parse_rows(rows)
    assert [f.symbol for f in parsed] == ["ELECON"]
    assert parsed[0].when == datetime(2026, 10, 14, 13, 19, 48, tzinfo=IST)
    assert parsed[0].category == "results"


def test_store_dedupes_and_load_respects_the_window(tmp_path) -> None:
    first = parse_rows([_row("TARIL", "2026-10-13 15:37:10", "Outcome of Board Meeting",
                             "financial results", "1")])  # fmt: skip
    again = parse_rows([
        _row("TARIL", "2026-10-13 15:37:10", "Outcome of Board Meeting", "financial results", "1"),
        _row("ELECON", "2026-10-14 13:19:48", "Outcome of Board Meeting", "financial results", "2"),
    ])  # fmt: skip
    store(first, tmp_path)
    counts = store(again, tmp_path)
    assert counts == {"2026-10-13": 1, "2026-10-14": 1}

    before_elecon = load(
        datetime(2026, 10, 13, 15, 30, tzinfo=IST),
        datetime(2026, 10, 14, 13, 0, tzinfo=IST),
        tmp_path,
    )
    assert [f.symbol for f in before_elecon] == ["TARIL"]  # a later filing is never visible


def test_previous_close_skips_weekends_and_respects_the_session() -> None:
    assert previous_close(datetime(2026, 10, 14, 9, 0, tzinfo=IST)) == datetime(
        2026, 10, 13, 15, 30, tzinfo=IST
    )
    assert previous_close(datetime(2026, 10, 14, 18, 0, tzinfo=IST)) == datetime(
        2026, 10, 14, 15, 30, tzinfo=IST
    )
    assert previous_close(datetime(2026, 10, 12, 9, 0, tzinfo=IST)) == datetime(
        2026, 10, 9, 15, 30, tzinfo=IST
    )


def test_morning_view_lists_material_filings_since_the_last_close(tmp_path) -> None:
    board, results = "Outcome of Board Meeting", "financial results"
    rows = [
        _row("TARIL", "2026-10-13 15:37:10", board, results, "1"),
        _row("TATAELXSI", "2026-10-13 17:35:36", board, results, "2"),
        _row("RBA", "2026-10-13 18:10:46", "Certificate under SEBI", "", "3"),
        _row("OLD", "2026-10-13 11:00:00", board, results, "4"),
    ]
    store(parse_rows(rows), tmp_path)
    view = since_last_close(datetime(2026, 10, 14, 9, 0, tzinfo=IST), root=tmp_path)
    assert view["total_filings"] == 3  # OLD was before the 13 Oct close
    assert [f["symbol"] for f in view["material"]] == ["TATAELXSI", "TARIL"]  # newest first
