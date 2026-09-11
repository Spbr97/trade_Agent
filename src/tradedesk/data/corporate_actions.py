"""Corporate actions: import NSE's corporate-action CSV, parse split/bonus ratios, and
detect candles that look unadjusted (PLAN.md 5.4: "a 1:1 bonus will look like a 50% crash").

NSE's equities corporate-action export has columns like
SYMBOL, COMPANY NAME, SERIES, FACE VALUE, PURPOSE, EX-DATE, RECORD DATE, ...
and PURPOSE strings such as
  "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
  "Bonus 1:1"  /  "Bonus 3:2"
  "Dividend - Rs 5 Per Share"  (kept as OTHER, never adjusts prices)
Column names and date formats are matched tolerantly because the export changes.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import date, datetime
from fractions import Fraction

import numpy as np
import pandas as pd

from tradedesk.data.candle_store import ist_dates
from tradedesk.data.models import ActionKind, CorporateAction

_SPLIT_RE = re.compile(
    r"from\s*(?:rs|re|inr)?\.?\s*(\d+(?:\.\d+)?)\s*/?-?\s*(?:per\s*share)?\s*to\s*"
    r"(?:rs|re|inr)?\.?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_BONUS_RE = re.compile(r"bonus\s*(?:issue)?\s*(\d+)\s*:\s*(\d+)", re.IGNORECASE)
_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y")


def parse_purpose(purpose: str) -> tuple[ActionKind, Fraction]:
    """Return (kind, price_factor) for an NSE PURPOSE string."""
    text = purpose.strip()
    low = text.lower()
    if "split" in low or "sub-division" in low or "subdivision" in low:
        m = _SPLIT_RE.search(text)
        if m:
            old_fv, new_fv = Fraction(m.group(1)), Fraction(m.group(2))
            if old_fv > 0 and new_fv > 0 and new_fv != old_fv:
                return ActionKind.SPLIT, new_fv / old_fv
        return ActionKind.SPLIT, Fraction(1)  # unparsed ratio: recorded, flagged, not applied
    if "bonus" in low:
        m = _BONUS_RE.search(text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 0 and b > 0:
                return ActionKind.BONUS, Fraction(b, a + b)
        return ActionKind.BONUS, Fraction(1)
    return ActionKind.OTHER, Fraction(1)


def parse_nse_date(text: str) -> date | None:
    t = text.strip().strip('"')
    if not t or t == "-":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def _find_col(fieldnames: Sequence[str], *needles: str) -> str | None:
    for name in fieldnames:
        key = name.strip().strip('"').upper().replace("_", " ")
        if any(key == n or key.startswith(n) for n in needles):
            return name
    return None


def parse_nse_corporate_actions_csv(text: str, source: str = "nse") -> list[CorporateAction]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    names = reader.fieldnames or []
    col_symbol = _find_col(names, "SYMBOL")
    col_purpose = _find_col(names, "PURPOSE")
    col_exdate = _find_col(names, "EX-DATE", "EX DATE", "EXDATE")
    col_series = _find_col(names, "SERIES")
    if not (col_symbol and col_purpose and col_exdate):
        raise ValueError(f"unrecognised corporate-action CSV columns: {names}")
    out: list[CorporateAction] = []
    for row in reader:
        symbol = (row.get(col_symbol) or "").strip()
        purpose = (row.get(col_purpose) or "").strip()
        ex = parse_nse_date(row.get(col_exdate) or "")
        series = (row.get(col_series) or "EQ").strip().upper() if col_series else "EQ"
        if not symbol or ex is None or series not in {"EQ", "BE", ""}:
            continue
        kind, factor = parse_purpose(purpose)
        out.append(
            CorporateAction(
                symbol=symbol, ex_date=ex, kind=kind, price_factor=factor, purpose=purpose,
                source=source,
            )
        )  # fmt: skip
    return out


# Common split / bonus ratios expressed as the close-to-close factor they produce.
_KNOWN_FACTORS: dict[str, float] = {
    "split 10->1": 0.1, "split 10->2": 0.2, "split 10->5": 0.5, "split 5->1": 0.2,
    "split 5->2": 0.4, "split 2->1": 0.5, "split 10->1 or bonus 9:1": 0.1,
    "bonus 1:1": 0.5, "bonus 2:1": 1 / 3, "bonus 3:1": 0.25, "bonus 4:1": 0.2,
    "bonus 1:2": 2 / 3, "bonus 1:3": 0.75, "bonus 1:4": 0.8, "bonus 3:2": 0.4,
    "bonus 1:5": 5 / 6, "bonus 2:3": 0.6, "bonus 1:10": 10 / 11,
}  # fmt: skip


def detect_unadjusted(
    df: pd.DataFrame, *, tolerance: float = 0.06, min_gap: float = 0.15
) -> list[tuple[date, float, str]]:
    """Find days where BOTH the open and the close sit at a known split/bonus ratio of the
    previous close - a gap that never fills, the signature of an unadjusted series.

    Returns (date, observed_factor, best_matching_label). A real crash usually opens at
    an arbitrary ratio and trades a wide range; a split opens *and* closes at the ratio.
    """
    if len(df) < 2:
        return []
    prev_close = df["close"].shift(1)
    open_ratio = (df["open"] / prev_close).to_numpy()
    close_ratio = (df["close"] / prev_close).to_numpy()
    dates = ist_dates(df)
    hits: list[tuple[date, float, str]] = []
    for i in range(1, len(df)):
        o, c = open_ratio[i], close_ratio[i]
        if not np.isfinite(o) or not np.isfinite(c) or c > 1 - min_gap:
            continue
        for label, f in _KNOWN_FACTORS.items():
            if abs(o / f - 1) <= tolerance and abs(c / f - 1) <= tolerance:
                hits.append((dates[i], float(c), label))
                break
    return hits
