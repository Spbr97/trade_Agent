from datetime import date

from tradedesk.data.results_calendar import parse_nse_board_meetings_json


def _row(symbol: str, when: str, purpose: str, desc: str = "") -> dict[str, str]:
    return {"bm_symbol": symbol, "bm_date": when, "bm_purpose": purpose, "bm_desc": desc}


def test_intimation_rows_about_results_are_kept_and_normalised() -> None:
    rows = [
        _row("WIPRO", "15-Oct-2026", "Board Meeting Intimation",
             "to consider the Unaudited Financial results for the quarter"),
        _row("WIPRO", "15-Oct-2026", "Financial Results"),
        _row("XYZ", "16-Oct-2026", "Fund Raising"),
        _row("ABC", "17-Oct-2026", "Board Meeting Intimation", "to consider a buyback"),
        _row("", "17-Oct-2026", "Financial Results"),
    ]
    events = parse_nse_board_meetings_json(rows)
    assert [(e.symbol, e.event_date, e.purpose) for e in events] == [
        ("WIPRO", date(2026, 10, 15), "Financial Results")
    ]
    assert events[0].source == "nse-api"
