"""IPO allotments: what is one worth, and once it lists, should it be sold at the open or held?

The owner applies to IPOs regularly and usually sells on listing day unless the stock has
potential (2026-11-05), so this is real money today. new_listings.py tested BUYING after a debut.
This asks the holder's different question, from the issue price they paid. Retail's quota at the
issue price is the one place a small account stands ahead of a fund, because the listing gain is a
gift from the issuer to get the issue sold. Studies of Indian IPOs find subscription demand drives
that gain, and mixed results after it.

Data: NSE's public-past-issues list (mainboard "Equity", issue price, listing date), saved by
`fetch` to data/m14_m18/ipo/past_issues.json, and stored daily candles. An IPO counts only if its
first candle is on its listing date. Dropped, and counted: no issue price, a later split or bonus
in corporate_actions (the store's prices are adjusted, so its open would not match the issue
price), or a listing open under 40% or over 450% of the issue price, the same fault in a hidden
form.

Described, not judged: the listing open's gain over the issue price, how often it loses, by year.

The decision. Having the shares at the listing open, hold to that session's close, or the 5th,
20th or 60th close, or sell at once? Selling costs the same whenever it happens, so a hold is
measured from the open: 0.05% slippage on the sale at the open given up, and excess = its return
less the equal-weight liquid market over that span. Judged for all IPOs, and by how the open went:
below the issue price, 0-10% up, 10-30% up, over 30% up.

PASS BAR (written 2026-11-07, before any IPO data was downloaded). A verdict needs at least 40
IPOs, |t| >= 3.0 on the mean excess, one listing WEEK as one unit, and the same sign in both
halves (split at the median listing date). Positive: holding beats selling at the open. Negative:
selling at the open beats holding. Otherwise: no verdict, so neither is shown better. 5 groups x 4
holds = 20 tests.

Also judged: does a hot market mean a bigger gift? The mean listing gain of the previous 10
listings is known before an IPO lists. Passes if IPOs following a hot stretch (the top third) gain
more at the open than those following a cold one (the bottom third), Welch t >= 3.0 over listing
weeks, in both halves.

Caveats: the store holds stocks listed today, which flatters holding, so a "hold" verdict needs
extra care and a "sell" verdict is the stronger for it. Allotment odds, retail discounts and
subscription figures are not in this data. No listing-day intraday path before 2025.

RESULT (2026-11-07, 457 mainboard IPOs 2016-2026 kept; dropped: 16 no issue price, 55 a later
split or bonus, 35 no price history, 14 first candle off the date). Mean listing-open gain +18.8%,
median +9.1%, 21% open below the issue price, about Rs 2,800 on a Rs 15,000 allotment, before the
odds of getting one.
A verdict for 4 of the 20 tests, all HOLD, and 3 of them in one band:
  open 10-30% up   close +2.77%, 66% win, +3.41% vs market, t 4.58; 20 sessions +7.58%, 59% win,
                   +7.80%, t 3.55; 60 sessions +13.40%, 63% win, +14.61%, t 3.45
  all IPOs         close +0.78%, +1.57% vs market, t 3.65, thin in the second half
  under 10% up, below the issue price, over 30% up: no verdict at any hold, median mostly negative
Hot market fails: IPOs after a hot stretch gain +25.0% against +13.6%, t 1.70. No test says sell at
the open is better. ipo_checks.py could not break the band; ipo_forward.py trials it forward.

    python -m tradedesk_lab.ipo_listing fetch
    python -m tradedesk_lab.ipo_listing
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import event_studies as es
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "ipo" / "past_issues.json"
OUT_DIR = OUTPUT / "ipo_listing"
URL = "https://www.nseindia.com/api/public-past-issues"
HOLDS = {"close": 1, "s5": 5, "s20": 20, "s60": 60}
BUCKETS = (("below_issue", -math.inf, 0.0), ("up_0_10", 0.0, 0.10), ("up_10_30", 0.10, 0.30),
           ("up_30_plus", 0.30, math.inf))  # fmt: skip
SLIPPAGE = 0.0005
MIN_IPOS, MIN_T = 40, 3.0
OPEN_RANGE = (0.40, 4.50)
HOT_LOOKBACK = 10
ALLOTMENT_INR = 15_000.0  # about one retail lot, for scale only


def number(value: Any) -> float | None:
    """A price from NSE's padded text, or None."""

    text = str(value or "").replace(",", "").strip()
    return float(text) if re.fullmatch(r"\d+(\.\d+)?", text) else None


def parse_day(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value).strip().title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def clean(
    rows: Iterable[Mapping[str, Any]], actions: Mapping[str, list[date]]
) -> tuple[list[dict[str, Any]], Counter]:
    """Mainboard IPOs with a price and a date, and a count of what was left out and why."""

    out, dropped = [], Counter()
    for row in rows:
        if row.get("securityType") != "Equity":
            continue
        symbol = str(row.get("symbol") or "").strip()
        issue, listed = number(row.get("issuePrice")), parse_day(row.get("listingDate"))
        if issue is None or issue <= 0:
            dropped["no issue price"] += 1
        elif listed is None:
            dropped["not listed"] += 1
        elif any(day >= listed for day in actions.get(symbol, [])):
            dropped["later split or bonus"] += 1
        else:
            out.append({"symbol": symbol, "issue": issue, "listed": listed})
    return out, dropped


def outcomes(
    ipos: Iterable[Mapping[str, Any]], p: nl.Panels, market_span: Any, codes: Mapping[str, str]
) -> tuple[pd.DataFrame, Counter]:
    """One row per IPO: gain at the open, and each hold's excess from that open."""

    sessions = list(p.close.index)
    rows, dropped = [], Counter()
    for ipo in ipos:
        code = codes.get(ipo["symbol"])
        if code not in p.close.columns:
            dropped["no price history"] += 1
            continue
        first = p.close[code].first_valid_index()
        if first != ipo["listed"]:
            dropped["first candle not the listing date"] += 1
            continue
        e = sessions.index(first)
        opening = float(p.open[code].iat[e])
        if not OPEN_RANGE[0] <= opening / ipo["issue"] <= OPEN_RANGE[1]:
            dropped["open far from the issue price"] += 1
            continue
        row: dict[str, Any] = {"symbol": ipo["symbol"], "day": first,
                               "gain": opening / ipo["issue"] - 1}  # fmt: skip
        sold = opening * (1 - SLIPPAGE)
        for name, hold in HOLDS.items():
            x = e + hold - 1
            close = float(p.close[code].iat[x]) if x < len(sessions) else math.nan
            if math.isfinite(close):
                row[name] = close * (1 - SLIPPAGE) / sold - 1
                row[f"{name}_x"] = row[name] - market_span(e, x)
        rows.append(row)
    return pd.DataFrame(rows), dropped


def judge(frame: pd.DataFrame, hold: str) -> dict[str, Any]:
    """The verdict on one group and hold: hold, sell, or none."""

    column = f"{hold}_x"
    part = frame.dropna(subset=[column]) if column in frame else frame.iloc[0:0]
    if len(part) < 2:
        return {"ipos": len(part), "verdict": "none"}
    calls = part.assign(excess=part[column])
    mean, t, weeks = nl.week_t(calls)
    days = sorted(calls["day"])
    cut = days[len(days) // 2]
    halves = [nl.week_t(calls[calls["day"] < cut])[0], nl.week_t(calls[calls["day"] >= cut])[0]]
    same = all(h > 0 for h in halves) or all(h < 0 for h in halves)
    verdict = "none"
    if len(part) >= MIN_IPOS and abs(t) >= MIN_T and same:
        verdict = "hold" if mean > 0 else "sell"
    return {
        "ipos": len(part), "weeks": weeks, "mean_return": float(part[hold].mean()),
        "median_return": float(part[hold].median()), "win_rate": float((part[hold] > 0).mean()),
        "mean_excess_week": mean, "t": t, "halves": halves, "verdict": verdict,
    }  # fmt: skip


def describe(frame: pd.DataFrame) -> dict[str, Any]:
    gains = frame["gain"]
    years = frame.groupby(frame["day"].map(lambda d: d.year))["gain"]
    return {
        "ipos": len(frame), "mean_gain": float(gains.mean()), "median_gain": float(gains.median()),
        "below_issue_share": float((gains < 0).mean()), "worst": float(gains.min()),
        "best": float(gains.max()),
        "mean_inr_per_allotment": float(gains.mean() * ALLOTMENT_INR),
        "by_year": {
            str(y): {"ipos": int(g.count()), "mean": float(g.mean()), "median": float(g.median()),
                     "below_issue": float((g < 0).mean())}
            for y, g in years
        },
    }  # fmt: skip


def hot_market(frame: pd.DataFrame) -> dict[str, Any]:
    """Do IPOs after a hot stretch gain more at the open? The stretch uses only earlier listings."""

    ordered = frame.sort_values("day").reset_index(drop=True)
    earlier = ordered["gain"].shift(1).rolling(HOT_LOOKBACK).mean()
    usable = ordered.assign(heat=earlier).dropna(subset=["heat"])
    low, high = usable["heat"].quantile(1 / 3), usable["heat"].quantile(2 / 3)
    hot, cold = usable[usable["heat"] >= high], usable[usable["heat"] <= low]

    def gap(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float, float]:
        def weeks(x: pd.DataFrame) -> pd.Series:
            return x.groupby(x["day"].map(lambda d: d.isocalendar()[:2]))["gain"].mean()

        wa, wb = weeks(a), weeks(b)
        if len(wa) < 2 or len(wb) < 2:
            return math.nan, math.nan
        diff = float(wa.mean() - wb.mean())
        se = math.sqrt(wa.var(ddof=1) / len(wa) + wb.var(ddof=1) / len(wb))
        return diff, diff / se if se > 0 else math.nan

    diff, t = gap(hot, cold)
    days = sorted(usable["day"])
    cut = days[len(days) // 2]
    halves = [gap(hot[hot["day"] < cut], cold[cold["day"] < cut])[0],
              gap(hot[hot["day"] >= cut], cold[cold["day"] >= cut])[0]]  # fmt: skip
    return {
        "hot_ipos": len(hot), "cold_ipos": len(cold), "hot_mean": float(hot["gain"].mean()),
        "cold_mean": float(cold["gain"].mean()), "hot_below_issue": float((hot["gain"] < 0).mean()),
        "cold_below_issue": float((cold["gain"] < 0).mean()), "gap": diff, "t": t, "halves": halves,
        "passed": bool(diff > 0 and t >= MIN_T and all(h > 0 for h in halves)),
    }  # fmt: skip


def fetch() -> int:
    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    with httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as client:
        client.get("https://www.nseindia.com/market-data/all-upcoming-issues-ipo")
        response = client.get(URL)
        response.raise_for_status()
    rows = response.json()
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows), encoding="utf-8")
    return len(rows)


def corporate_actions() -> dict[str, list[date]]:
    import duckdb

    con = duckdb.connect(str(nl.DB), read_only=True)
    try:
        rows = con.execute(
            "SELECT symbol, ex_date FROM corporate_actions WHERE kind IN ('split', 'bonus')"
        ).fetchall()
    finally:
        con.close()
    out: dict[str, list[date]] = {}
    for symbol, day in rows:
        out.setdefault(symbol, []).append(day)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", nargs="?", default="study", choices=("fetch", "study"))
    args = parser.parse_args(argv)
    if args.command == "fetch":
        print("saved", fetch(), "rows")
        return
    p = nl.load()
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)

    def span(e: int, x: int) -> float:
        move = p.close.iloc[x] / p.open.iloc[e] - 1
        return float(move[(turnover.iloc[e] >= nl.MIN_TURNOVER) & move.notna()].mean())

    raw = json.loads(STORE.read_text(encoding="utf-8"))
    ipos, dropped = clean(raw, corporate_actions())
    frame, more = outcomes(ipos, p, span, es.symbols())
    dropped.update(more)
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "kept": len(frame), "dropped": dict(dropped),
        "described": describe(frame), "decisions": {}, "hot_market": hot_market(frame),
    }  # fmt: skip
    groups = {"all": frame} | {
        name: frame[(frame["gain"] >= low) & (frame["gain"] < high)] for name, low, high in BUCKETS
    }
    for name, part in groups.items():
        report["decisions"][name] = {hold: judge(part, hold) for hold in HOLDS}
    print(json.dumps(report, indent=1, default=str))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
