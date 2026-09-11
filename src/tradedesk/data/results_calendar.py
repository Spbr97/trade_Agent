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
