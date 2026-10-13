"""Results calendar: import NSE's board-meeting export and answer "results inside my
holding window?" (PLAN.md 1.2: never hold through results; 6.7: filter candidates).

NSE's export ("Corporate Actions → Board Meetings" CSV) has columns along the lines of
SYMBOL, COMPANY NAME, PURPOSE, DETAILS, BOARD MEETING DATE (or DATE). Purpose strings like
"Financial Results", "Financial Results/Dividend", "Audited Results". Matched tolerantly.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from tradedesk.data.corporate_actions import _find_col, parse_nse_date
from tradedesk.data.models import ResultsEvent

_RESULT_WORDS = ("result", "financial", "quarter", "audited", "unaudited")


def is_results_purpose(purpose: str) -> bool:
    low = purpose.lower()
    return any(w in low for w in _RESULT_WORDS)


def parse_nse_board_meetings_csv(
    text: str, *, results_only: bool = True, source: str = "nse"
) -> list[ResultsEvent]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    names = reader.fieldnames or []
    col_symbol = _find_col(names, "SYMBOL")
    col_purpose = _find_col(names, "PURPOSE")
    col_date = _find_col(names, "BOARD MEETING DATE", "BOARDMEETING DATE", "MEETING DATE", "DATE")
    if not (col_symbol and col_date):
        raise ValueError(f"unrecognised board-meeting CSV columns: {names}")
    out: list[ResultsEvent] = []
    for row in reader:
        symbol = (row.get(col_symbol) or "").strip()
        purpose = (row.get(col_purpose) or "").strip() if col_purpose else ""
        when = parse_nse_date(row.get(col_date) or "")
        if not symbol or when is None:
            continue
        if results_only and purpose and not is_results_purpose(purpose):
            continue
        out.append(ResultsEvent(symbol=symbol, event_date=when, purpose=purpose, source=source))
    return out


def sessions_until(event: date | None, today: date, calendar: list[date]) -> int | None:
    """Trading sessions from `today` (exclusive) to `event` (inclusive); None if no event."""
    if event is None:
        return None
    return sum(1 for d in calendar if today < d <= event)


NSE_BOARD_MEETINGS_URL = "https://www.nseindia.com/api/corporate-board-meetings"
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-board-meetings",
}


def parse_nse_board_meetings_json(rows: list[dict[str, str]]) -> list[ResultsEvent]:
    """Results-related meetings from NSE's public board-meetings API.

    Intimation rows carry the purpose "Board Meeting Intimation" and only say "financial
    results" in their description, so those are normalised to "Financial Results" (the
    results_events key is symbol + date + purpose, which keeps re-fetches idempotent)."""

    events: dict[tuple[str, date], ResultsEvent] = {}
    for row in rows:
        symbol = (row.get("bm_symbol") or "").strip()
        when = parse_nse_date(row.get("bm_date") or "")
        purpose = (row.get("bm_purpose") or "").strip()
        description = row.get("bm_desc") or ""
        if not symbol or when is None:
            continue
        if not (is_results_purpose(purpose) or "financial result" in description.lower()):
            continue
        if purpose == "Board Meeting Intimation":
            purpose = "Financial Results"
        events.setdefault(
            (symbol, when),
            ResultsEvent(symbol=symbol, event_date=when, purpose=purpose, source="nse-api"),
        )
    return list(events.values())


def fetch_nse_board_meetings(
    start: date, end: date, *, client: object | None = None
) -> list[ResultsEvent]:
    """Fetch NSE board meetings between two dates (public API; needs a cookie from the home
    page and browser-like headers). Raises on any non-200 response or non-list body."""

    import httpx

    def get(c: httpx.Client) -> list[ResultsEvent]:
        c.get("https://www.nseindia.com/")  # sets the cookies the API requires
        response = c.get(
            NSE_BOARD_MEETINGS_URL,
            params={
                "index": "equities",
                "from_date": start.strftime("%d-%m-%Y"),
                "to_date": end.strftime("%d-%m-%Y"),
            },
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list):
            raise ValueError(f"unexpected NSE board-meetings response: {str(body)[:120]}")
        return parse_nse_board_meetings_json(body)

    if client is not None:
        return get(client)  # type: ignore[arg-type]
    with httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as c:
        return get(c)
