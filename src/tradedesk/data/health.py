"""Data-quality report over stored daily candles (PLAN.md 5.4, M3 "done when").

Checks per scrip code:
- missing sessions: reference-calendar days between its first and last bar with no bar
- bad OHLC: non-positive prices, high < low, open/close outside [low, high]
- zero-volume sessions
- big jumps: |close/prev_close - 1| above a threshold
- suspected unadjusted splits/bonuses (see corporate_actions.detect_unadjusted); a jump
  that a stored corporate action explains is reported as a big jump only
- stale: last bar older than `stale_after` sessions before the calendar end
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.data.candle_store import CandleStore, ist_dates
from tradedesk.data.corporate_actions import detect_unadjusted
from tradedesk.data.models import IssueKind, QualityIssue


@dataclass
class QualityReport:
    issues: list[QualityIssue] = field(default_factory=list)
    codes_checked: int = 0

    def by_kind(self) -> dict[IssueKind, int]:
        out: dict[IssueKind, int] = {}
        for i in self.issues:
            out[i.kind] = out.get(i.kind, 0) + i.count
        return out

    def for_code(self, code: str) -> list[QualityIssue]:
        return [i for i in self.issues if i.scrip_code == code]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([i.model_dump() for i in self.issues])


def check_series(
    code: str,
    df: pd.DataFrame,
    calendar: Sequence[date],
    *,
    jump_threshold: float = 0.30,
    stale_after_sessions: int = 3,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if df.empty:
        return [QualityIssue(scrip_code=code, kind=IssueKind.NO_DATA)]
    dates = ist_dates(df)
    first, last = dates[0], dates[-1]

    present = set(dates)
    expected = [d for d in calendar if first <= d <= last]
    missing = [d for d in expected if d not in present]
    if missing:
        issues.append(
            QualityIssue(
                scrip_code=code, kind=IssueKind.MISSING_SESSIONS, on=missing[0],
                detail=f"{len(missing)} of {len(expected)} sessions missing; first {missing[0]}",
                count=len(missing),
            )
        )  # fmt: skip

    o, h, lo, c, v = (df[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    bad = (o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0) | (h < lo) | (o > h) | (o < lo) | (c > h) | (c < lo)
    if bad.any():
        idx = int(np.argmax(bad))
        issues.append(
            QualityIssue(
                scrip_code=code, kind=IssueKind.BAD_OHLC, on=dates[idx],
                detail=f"{int(bad.sum())} bars with inconsistent OHLC", count=int(bad.sum()),
            )
        )  # fmt: skip

    zero_vol = v <= 0
    if zero_vol.any():
        idx = int(np.argmax(zero_vol))
        issues.append(
            QualityIssue(
                scrip_code=code, kind=IssueKind.ZERO_VOLUME, on=dates[idx],
                detail=f"{int(zero_vol.sum())} zero-volume sessions", count=int(zero_vol.sum()),
            )
        )  # fmt: skip

    suspected = {d: (f, label) for d, f, label in detect_unadjusted(df)}
    for d, (f, label) in suspected.items():
        issues.append(
            QualityIssue(
                scrip_code=code, kind=IssueKind.SUSPECTED_UNADJUSTED, on=d,
                detail=f"close/prev_close = {f:.3f} at open and close, looks like {label}",
            )
        )  # fmt: skip

    ret = df["close"].pct_change().to_numpy()
    for i in range(1, len(df)):
        r = ret[i]
        if np.isfinite(r) and abs(r) >= jump_threshold and dates[i] not in suspected:
            issues.append(
                QualityIssue(
                    scrip_code=code, kind=IssueKind.BIG_JUMP, on=dates[i],
                    detail=f"close moved {r:+.1%} vs previous session",
                )
            )  # fmt: skip

    if calendar:
        tail = [d for d in calendar if d > last]
        if len(tail) > stale_after_sessions:
            issues.append(
                QualityIssue(
                    scrip_code=code, kind=IssueKind.STALE, on=last,
                    detail=f"last bar {last}; {len(tail)} sessions since", count=len(tail),
                )
            )  # fmt: skip
    return issues


def run_quality_report(
    store: CandleStore,
    codes: Sequence[str],
    reference_code: str,
    *,
    start: date | None = None,
    end: date | None = None,
    jump_threshold: float = 0.30,
) -> QualityReport:
    from tradedesk.data.universe import trading_days

    end = end or datetime.now(IST).date()
    start = start or (end - timedelta(days=365 * 12))
    calendar = trading_days(store, reference_code, start, end)
    s = datetime.combine(start, time.min, tzinfo=IST)
    e = datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST)
    report = QualityReport()
    for code in codes:
        df = store.load(code, Interval.D1, s, e, adjusted=True)
        report.issues.extend(check_series(code, df, calendar, jump_threshold=jump_threshold))
        report.codes_checked += 1
    return report
