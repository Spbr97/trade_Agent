"""Universe-by-date from liquidity rules (PLAN.md 5.2, 5.4).

Membership is computed from the candles on hand *as of a date*, never from today's index
list, so a backtest on 2023 sees the stocks that were liquid in 2023 (survivorship bias is
reduced, not eliminated: stocks whose history the API no longer serves are still missing -
record that caveat next to every backtest result).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.data.candle_store import ist_dates

if TYPE_CHECKING:
    from tradedesk.data.candle_store import CandleStore


@dataclass(frozen=True)
class UniverseRules:
    min_avg_turnover_inr: float = 5e7  # 20-session average of close*volume
    min_price: float = 50.0
    lookback_sessions: int = 20
    min_sessions_present: int = 20  # must have a full lookback of candles


_DEFAULT_RULES = UniverseRules()  # frozen and shared, so it's a name not a call in defaults below


def trading_days(store: CandleStore, reference_code: str, start: date, end: date) -> list[date]:
    """Sessions between start and end (inclusive), taken from the reference index's daily
    candles. The index trades on every session, so its candle dates are the calendar."""
    s = datetime.combine(start, time.min, tzinfo=IST)
    e = datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST)
    df = store.load(reference_code, Interval.D1, s, e, adjusted=False)
    return sorted(set(ist_dates(df)))


def universe_on(
    store: CandleStore,
    candidates: list[str],
    on: date,
    rules: UniverseRules = _DEFAULT_RULES,
) -> list[str]:
    """Codes that pass the liquidity rules using only candles with open date <= `on`."""
    if not candidates:
        return []
    cutoff = int(datetime.combine(on + timedelta(days=1), time.min, tzinfo=IST).timestamp())
    placeholders = ",".join("?" * len(candidates))
    sql = f"""
        WITH d AS (
            SELECT scrip_code, ts, close, close * volume AS turnover,
                   row_number() OVER (PARTITION BY scrip_code ORDER BY ts DESC) AS rn
            FROM candles
            WHERE interval = '1day' AND ts < ? AND scrip_code IN ({placeholders})
        )
        SELECT scrip_code
        FROM d
        WHERE rn <= ?
        GROUP BY scrip_code
        HAVING count(*) >= ?
           AND avg(turnover) >= ?
           AND max(CASE WHEN rn = 1 THEN close END) >= ?
        ORDER BY scrip_code
    """
    rows = store.con.execute(
        sql,
        [
            cutoff,
            *candidates,
            rules.lookback_sessions,
            rules.min_sessions_present,
            rules.min_avg_turnover_inr,
            rules.min_price,
        ],
    ).fetchall()
    return [r[0] for r in rows]


def universe_history(
    store: CandleStore,
    candidates: list[str],
    rebuild_dates: list[date],
    rules: UniverseRules = _DEFAULT_RULES,
) -> dict[date, list[str]]:
    """Membership at each rebuild date (e.g. the first session of every month)."""
    return {d: universe_on(store, candidates, d, rules) for d in rebuild_dates}


def month_starts(calendar: list[date]) -> list[date]:
    """First trading session of each month in `calendar`."""
    out: list[date] = []
    seen: set[tuple[int, int]] = set()
    for d in calendar:
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out
