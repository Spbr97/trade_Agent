"""Letting drift winners run: does a longer exit beat selling at +10%?

The owner's target (2026-11-05) is trades that can run for 2-3 months. The live drift rule sells at
+10% or the 20th close, and takeoff_capture.py chose that because a slot freed sooner earned more
rupees on Rs 1 lakh, not because a cap makes a winner better. Ten-slot simulation later showed the
slots sit empty about 60% of the time, so a longer hold may cost less than it looked.

Same entries as the live rule: drift events, bought at the next open, less the calls marked to skip
with real money (a market down day with NIFTY below its 200-day average). Same rupees: Rs 10,000.

Exits, against the live one (+10% target, otherwise the 20th close):
  trail10       sell at the first close 10% below the highest close, or the 60th;
  trail15       the same at 15%;
  hold60        the 60th close;
  first_close   sell at the entry day's close if it is below the entry (intraday charges), else
                trail10: the trade starts intraday and is only carried if it is working;
  half_runner   half sells at +10%, half trails 10%, both by the 60th close, each half charged as a
                Rs 5,000 trade.
Holding longer earns more from the market alone, so these are judged on money over the market: each
call's net less the equal-weight liquid market over its own span (a half_runner uses each half's).

PASS BAR (written 2026-11-05, before any of these exits was run). An exit passes only if:
  1. its excess beats the live exit's on the same calls: mean paired gain > 0, t >= 2.5, one entry
     WEEK as one unit (about Bonferroni's bar for five);
  2. that gain is positive in both halves (split at the median entry date);
  3. it earns at least 1.2x the live exit's rupees a year on Rs 1 lakh in ten slots, calls taken in
     order while a slot is free, a half_runner holding its slot until its last half sells;
  4. its worst fall on closed trades in that book is no more than 1.5x the live exit's.
A pass is a proposal for the owner, not a change to the live rule. Research only.

Caveat: the price store holds stocks listed today, which flatters every long test.

RESULT (2026-11-05, 1,520 live-rule calls 2016-2026): ALL 5 FAIL. Longer holds make more per call,
but nearly all of it is the market: none beats the live exit over the market by the bar.
                 net/call  vs market  gain t  days  win   Rs a year  worst fall
  live            +1.28%    +1.00%      -      15   59%     9,239     -15,867
  trail10         +2.07%    +1.07%     0.41    35   45%    12,038     -28,052
  trail15         +3.13%    +1.52%     0.62    47   49%    14,131     -28,421
  hold60          +4.73%    +1.87%     0.80    60   56%    10,301     -51,069
  first_close     +1.17%    +0.61%    -0.68    17   26%     8,337     -32,024
  half_runner     +1.87%    +0.71%    -1.20    48   62%     7,844     -23,501
The live +10% rule stays. trail15's extra rupees came with nearly double the fall and are market
exposure an index holds more cheaply. first_close, the version that starts as a day trade, is the
worst for the win rate: a drift stock that closes its first day down often recovers later.

    python -m tradedesk_lab.drift_runner
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import amount_test as am
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT, ROOT

DB = ROOT / "data" / "tradedesk.duckdb"
OUT_DIR = OUTPUT / "drift_runner"
NIFTY = "NSE_40000001"
TARGET = 0.10
LIVE_LAST, LONG_LAST = 20, 60
EXITS = ("trail10", "trail15", "hold60", "first_close", "half_runner")
SLOTS, SLOT_INR = 10, 10_000.0
PASS = {"min_t": 2.5, "min_rupee_ratio": 1.2, "max_fall_ratio": 1.5}
MIN_MARKET_TURNOVER = 5e7
DOWN_DAY = -0.01


def target_exit(
    opens: Any, highs: Any, closes: Any, e: int, sessions: int, target: float = TARGET
) -> tuple[int, float] | None:
    """(row, price): the first session reaching the target, or the last session's close. A later
    session that gaps past the target fills at its open, as a resting sell order would."""

    last = e + sessions - 1
    if last >= len(closes):
        return None
    level = opens[e] * (1 + target)
    for k in range(e, last + 1):
        if math.isfinite(highs[k]) and highs[k] >= level:
            gap = opens[k] if k > e and math.isfinite(opens[k]) else 0.0
            return k, max(level, gap)
    return (last, closes[last]) if math.isfinite(closes[last]) else None


def trail_exit(closes: Any, e: int, trail: float, sessions: int = LONG_LAST) -> tuple[int, float] | None:  # noqa: E501
    """(row, price): the first close `trail` below the highest close, or the last session's."""

    last = e + sessions - 1
    if last >= len(closes):
        return None
    peak = -math.inf
    for k in range(e, last + 1):
        price = closes[k]
        if not math.isfinite(price):
            continue
        if price <= peak * (1 - trail):
            return k, float(price)
        peak = max(peak, price)
    return (last, float(closes[last])) if math.isfinite(closes[last]) else None


def legs(rule: str, opens: Any, highs: Any, closes: Any, e: int) -> list[tuple[int, float, float, bool]] | None:  # noqa: E501
    """Each part of a call as (row sold, price, share of the Rs 10,000, same-day); None if the
    history stops before the rule can finish."""

    if rule == "live":
        out = target_exit(opens, highs, closes, e, LIVE_LAST)
        return [(out[0], out[1], 1.0, False)] if out else None
    if rule in ("trail10", "trail15"):
        out = trail_exit(closes, e, 0.10 if rule == "trail10" else 0.15)
        return [(out[0], out[1], 1.0, False)] if out else None
    if rule == "hold60":
        last = e + LONG_LAST - 1
        if last >= len(closes) or not math.isfinite(closes[last]):
            return None
        return [(last, float(closes[last]), 1.0, False)]
    if rule == "first_close":
        if not math.isfinite(closes[e]) or e + LONG_LAST - 1 >= len(closes):
            return None
        if closes[e] < opens[e]:
            return [(e, float(closes[e]), 1.0, True)]
        out = trail_exit(closes, e, 0.10)
        return [(out[0], out[1], 1.0, False)] if out else None
    if rule == "half_runner":
        first = target_exit(opens, highs, closes, e, LONG_LAST)
        second = trail_exit(closes, e, 0.10)
        if first is None or second is None:
            return None
        return [(first[0], first[1], 0.5, False), (second[0], second[1], 0.5, False)]
    raise ValueError(rule)


def book(calls: pd.DataFrame, years: float) -> dict[str, float]:
    """Rs 1 lakh in ten slots, calls taken in order while one is free: rupees a year, and the
    worst fall in rupees on closed trades."""

    busy: list[int] = []
    taken = []
    for row in calls.sort_values("e", kind="stable").itertuples():
        busy = [x for x in busy if x >= row.e]
        if len(busy) < SLOTS:
            busy.append(int(row.x))
            taken.append((int(row.x), float(row.net) * SLOT_INR))
    if not taken:
        return {"taken": 0, "rupees_a_year": 0.0, "worst_fall": 0.0}
    taken.sort()
    equity = np.cumsum([r for _, r in taken])
    peak = np.maximum.accumulate(np.maximum(equity, 0.0))
    return {
        "taken": len(taken),
        "rupees_a_year": float(equity[-1] / years),
        "worst_fall": float((equity - peak).min()),
    }


def _week_t(frame: pd.DataFrame, col: str) -> tuple[float, float, int]:
    weeks = frame.groupby(frame["day"].map(lambda d: d.isocalendar()[:2]))[col].mean()
    n = len(weeks)
    if n < 2:
        return (float(weeks.mean()) if n else math.nan, math.nan, n)
    sd = float(weeks.std(ddof=1))
    mean = float(weeks.mean())
    return mean, (mean / (sd / math.sqrt(n)) if sd > 0 else math.nan), n


def judge(mine: pd.DataFrame, live: pd.DataFrame, years: float) -> dict[str, Any]:
    both = mine.merge(live, on="id", suffixes=("", "_live"))
    both["gain"] = both["excess"] - both["excess_live"]
    mean, t, weeks = _week_t(both, "gain")
    days = sorted(both["day"])
    cut = days[len(days) // 2] if days else None
    halves = [
        _week_t(both[both["day"] < cut], "gain")[0], _week_t(both[both["day"] >= cut], "gain")[0]
    ] if days else []  # fmt: skip
    mine_book, live_book = book(both, years), book(
        both.assign(net=both["net_live"], x=both["x_live"]), years
    )
    fails = []
    if not (mean > 0 and t >= PASS["min_t"]):
        fails.append(f"gain over the market t {t:.2f} < {PASS['min_t']}")
    if not all(h > 0 for h in halves):
        fails.append("gain not positive in both halves")
    if mine_book["rupees_a_year"] < PASS["min_rupee_ratio"] * live_book["rupees_a_year"]:
        fails.append("under 1.2x the live exit's rupees")
    if mine_book["worst_fall"] < PASS["max_fall_ratio"] * live_book["worst_fall"]:
        fails.append("worst fall over 1.5x the live exit's")
    return {
        "calls": len(both), "weeks": weeks, "mean_net": float(both["net"].mean()),
        "win_rate": float((both["net"] > 0).mean()), "mean_excess": float(both["excess"].mean()),
        "mean_gain_over_live": mean, "t_gain": t, "halves_gain": halves,
        "mean_days_held": float(both["days"].mean()), "book": mine_book, "live_book": live_book,
        "passed": not fails, "fails": fails,
    }  # fmt: skip


def _cost_model() -> Any:
    return am.cost_model()


def _delivery(cm: Any, entry: float, exit_px: float, rupees: float) -> float:
    from tradedesk.models import TradeType

    qty = max(1, int(rupees // entry))
    rt = cm.round_trip_cost(
        trade_type=TradeType.DELIVERY, qty=qty, entry_price=Decimal(str(round(entry, 2))),
        exit_price=Decimal(str(round(exit_px, 2))),
    )  # fmt: skip
    return float(rt.total) / (qty * entry)


def main(argv: Sequence[str] | None = None) -> None:
    import duckdb

    p = eh.load_panels(Path(DB))
    close, opening, volume = p["close"], p["open"], p["volume"]
    high = mr.load(Path(DB))["high"].reindex(index=close.index, columns=close.columns)
    events = edv.events(p)
    con = duckdb.connect(str(DB), read_only=True)
    try:
        frame = con.execute(
            "SELECT ts, close FROM candles WHERE interval = '1day' AND scrip_code = ? ORDER BY ts",
            [NIFTY],
        ).df()
    finally:
        con.close()
    frame["day"] = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    nifty = frame.drop_duplicates("day").set_index("day")["close"].reindex(close.index).ffill()
    average = nifty.rolling(200).mean()
    turnover = (close * volume).rolling(20).mean().shift(1)
    move = close.pct_change(fill_method=None).where(turnover >= MIN_MARKET_TURNOVER)
    market_move = move.clip(-0.5, 0.5).mean(axis=1)
    cm = _cost_model()
    column = {c: j for j, c in enumerate(close.columns)}
    opens, highs, closes = opening.to_numpy(), high.to_numpy(), close.to_numpy()
    dates = list(close.index)
    cache: dict[tuple[int, int], float] = {}

    def span_market(e: int, x: int) -> float:
        if (e, x) not in cache:
            span = close.iloc[x] / opening.iloc[e] - 1
            cache[(e, x)] = float(span[(turnover.iloc[e] >= MIN_MARKET_TURNOVER) & span.notna()].mean())  # noqa: E501
        return cache[(e, x)]

    def run(rule: str) -> pd.DataFrame:
        rows = []
        for code, i1 in zip(events["code"], events["i1"], strict=True):
            j, e = column[code], int(i1) + 1
            if e >= len(dates) or not (math.isfinite(opens[e, j]) and opens[e, j] > 0):
                continue
            if market_move.iat[i1] <= DOWN_DAY and nifty.iat[i1] < average.iat[i1]:
                continue
            parts = legs(rule, opens[:, j], highs[:, j], closes[:, j], e)
            if parts is None:
                continue
            entry = float(opens[e, j])
            net, excess = 0.0, 0.0
            for x, price, share, same_day in parts:
                if same_day:
                    qty = max(1, int(SLOT_INR // entry))
                    charge = am.intraday_cost(cm, entry, price, qty) / (qty * entry)
                else:
                    charge = _delivery(cm, entry, price, SLOT_INR * share)
                part = price / entry - 1 - charge
                net += share * part
                excess += share * (part - span_market(e, x))
            rows.append({
                "id": f"{code}:{e}", "day": dates[e], "e": e, "x": max(x for x, *_ in parts),
                "net": net, "excess": excess, "days": max(x for x, *_ in parts) - e + 1,
            })  # fmt: skip
        return pd.DataFrame(rows)

    years = len(dates[int(events["i1"].min()) :]) / 248
    live = run("live")
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS, "years": years,
        "live": {"calls": len(live), "mean_net": float(live["net"].mean()),
                 "mean_excess": float(live["excess"].mean()),
                 "win_rate": float((live["net"] > 0).mean()),
                 "mean_days_held": float(live["days"].mean())},
        "exits": {},
    }  # fmt: skip
    print("live", json.dumps(report["live"]), flush=True)
    for rule in EXITS:
        report["exits"][rule] = judge(run(rule), live, years)
        print(rule, json.dumps(report["exits"][rule], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
