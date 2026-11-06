"""Bulk and block deals: does a large holder building a position keep the stock rising?

First principles (owner, 2026-11-05: aim for a breakthrough). Who is on the other side, and why do
they keep losing? NSE publishes every client trading over 0.5% of a company in a day (bulk) and
every block-window trade, by name, that evening. A fund building a position may have more to buy,
and the sellers are whoever ignores a dull list. The catch: most bulk "buyers" are high-speed
firms that buy and sell the same stock the same day, which says nothing about a view. And
event_studies, insider buying and index changes all failed the same way: by publication the move
had happened.

Data: NSE's list by CSV (historicalOR/bulk-block-short-deals with csv=true, one request a year). Its
JSON form silently stops at 70 rows, even for one day, so it must not be used. Saved by `fetch` to
data/m14_m18/bulk_block/. `year_counts` shows coverage by year; check it before believing a result.

A client's day in a stock nets its buys and sells. A position builder has only one side that day,
which removes the same-day churn. Point in time: the deal is known that evening, so a signal is that
session, bought at the next open, one call per stock per 20 sessions per rule.

Rules:
  fund_bulk     a named domestic fund (mutual fund, insurer, pension fund, asset manager) bought in
                a bulk deal with no sale that day;
  holder_bulk   any client bought Rs 5 crore or more in a bulk deal with no sale that day;
  fund_block    a named domestic fund bought in the block window;
  repeat_buyer  the same client bought with no sale on at least 2 sessions within 20 sessions;
  seller_short  a client sold Rs 5 crore or more in a bulk deal with no buy that day: SHORT at the
                next open, cover that close.
Exits for the four long rules: that session's close (intraday charges), the 20th or the 60th close
(delivery charges). Universes as in insider_buying: liquid (20-session traded value >= Rs 5 crore,
0.05% slippage each way) and small (Rs 1-5 crore, 0.25%). Both: price >= Rs 20. A Rs 10,000 trade's
real charges. Excess = net minus the equal-weight liquid market over the same open-to-close span.

PASS BAR (written 2026-11-07, before any deal data was downloaded). A variant passes only if:
  1. at least 100 calls;
  2. mean net return per call > 0 after charges;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit;
  4. positive mean excess in both halves (split at the median signal date);
  5. it beats stocks that had already risen as much: net minus the average same-universe stock in
     the same quintile of the prior 60 sessions' return over the same span, mean > 0, t >= 2.0;
  6. positive mean excess in at least 60% of calendar quarters holding 5 or more calls.
(4 rules x 3 exits + the short) x 2 universes = 26 variants; t >= 3 is about Bonferroni's bar for
that many. A pass becomes a proposal for FORWARD paper calls, never a live call. Research only.

Caveats: the price store holds stocks listed today, which flatters long tests. Fund names are
matched by a fixed pattern, so foreign funds are left out, because high-speed firms use "fund"
names too.

RESULT (2026-11-07, about 158,000 bulk and 11,000 block deals, 2016-2026, every year populated):
ALL 26 FAIL. Closest: fund_bulk_liquid_60 (a named domestic fund buying in a bulk deal, liquid
stock, 60 sessions), 363 calls, +5.24% net, +2.53% over the market, t 2.08, both halves about +2.5%,
72% of quarters, and over equally strong stocks only t 1.93. Over shorter holds it adds nothing
(20 sessions t 0.87, same day t -0.31). holder_bulk: any big one-sided buyer trails the market (20
sessions -0.81%, t -1.71). repeat_buyer does worse the longer it is held (liquid 60 sessions -5.32%,
t -2.42): repeated buying is not accumulation that pays. seller_short loses in rupees: stocks
sold heavily rise from the next open (liquid -0.49% vs market, t -5.67), but buying them instead
would give about that back in double charges and slippage.

    python -m tradedesk_lab.bulk_block_deals fetch
    python -m tradedesk_lab.bulk_block_deals
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import time as time_module
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import event_studies as es
from tradedesk_lab import insider_buying as ib
from tradedesk_lab import institutional_stakes as st
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reading as rr
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "bulk_block"
OUT_DIR = OUTPUT / "bulk_block"
URL = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
KINDS = {"bulk": "bulk_deals", "block": "block_deals"}
FIRST_YEAR = 2016
BIG_VALUE = 5e7  # Rs 5 crore
COOLDOWN, REPEAT_WINDOW = 20, 20
EXITS = ("day", "20", "60")
LONG_RULES = ("fund_bulk", "holder_bulk", "fund_block", "repeat_buyer")
SHORT_RULE = "seller_short"
UNIVERSES = ib.UNIVERSES
FUND = re.compile(r"MUTUAL FUND|\bMF\b|INSURANCE|PENSION|PROVIDENT|ASSET MANAGEMENT", re.I)


# --------------------------------------------------------------------------- data


def parse_csv(text: str, kind: str) -> list[dict[str, Any]]:
    """One row per deal. NSE pads its column names with spaces and may start with a BOM."""

    rows = []
    for raw in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        try:
            day = datetime.strptime(row["Date"].title(), "%d-%b-%Y").date()
            qty = float(row["Quantity Traded"].replace(",", ""))
            price = float(row["Trade Price / Wght. Avg. Price"].replace(",", ""))
        except (KeyError, ValueError):
            continue
        side = row.get("Buy / Sell", row.get("Buy/Sell", "")).upper()
        if side in ("BUY", "SELL") and row.get("Symbol") and qty > 0 and price > 0:
            rows.append({
                "kind": kind, "day": day, "symbol": row["Symbol"],
                "client": " ".join(row.get("Client Name", "").upper().split()),
                "side": side, "value": qty * price,
            })  # fmt: skip
    return rows


def fetch(today: date, *, store: Path = STORE, pause_seconds: float = 2.0) -> dict[str, int]:
    """Save each kind's CSV a year per request; a finished saved year is kept."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    store.mkdir(parents=True, exist_ok=True)
    saved: dict[str, int] = {}
    with httpx.Client(headers=_NSE_HEADERS, timeout=180, follow_redirects=True) as client:
        client.get("https://www.nseindia.com/report-detail/display-bulk-and-block-deals")
        for kind, option in KINDS.items():
            for year in range(FIRST_YEAR, today.year + 1):
                path = store / f"{kind}_{year}.csv"
                if path.exists() and year < today.year:
                    continue
                response = client.get(URL, params={
                    "optionType": option, "from": f"01-01-{year}",
                    "to": f"31-12-{year}" if year < today.year else today.strftime("%d-%m-%Y"),
                    "csv": "true",
                })  # fmt: skip
                response.raise_for_status()
                path.write_text(response.text, encoding="utf-8")
                saved[path.name] = len(parse_csv(response.text, kind))
                time_module.sleep(pause_seconds)
    return saved


def read_store(store: Path = STORE) -> list[dict[str, Any]]:
    rows = []
    for kind in KINDS:
        for path in sorted(store.glob(f"{kind}_*.csv")):
            rows.extend(parse_csv(path.read_text(encoding="utf-8"), kind))
    return rows


def year_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Coverage by year: a capped or missing year shows here before it can distort a result."""

    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        out[str(r["day"].year)][str(r["kind"])] += 1
        if FUND.search(str(r["client"])) and r["side"] == "BUY":
            out[str(r["day"].year)]["fund_buys"] += 1
    return {y: dict(c) for y, c in sorted(out.items())}


# --------------------------------------------------------------------------- signals


def one_sided(rows: Iterable[Mapping[str, Any]], kind: str | None = "bulk") -> pd.DataFrame:
    """Each (day, symbol, client) that traded one side only: side, value. Same-day buying and
    selling by one client is churn, so it is dropped."""

    picked = [r for r in rows if kind is None or r["kind"] == kind]
    if not picked:
        return pd.DataFrame(columns=["day", "symbol", "client", "side", "value"])
    frame = pd.DataFrame(picked)
    grouped = frame.groupby(["day", "symbol", "client"])
    sides = grouped["side"].nunique()
    both = sides[sides > 1].index
    out = grouped.agg(side=("side", "first"), value=("value", "sum"))
    return out[~out.index.isin(both)].reset_index()


def rule_days(rows: list[Mapping[str, Any]], rule: str) -> list[tuple[str, date]]:
    """(symbol, deal day) for every day a rule is met, before the cooldown."""

    source = "block" if rule == "fund_block" else "bulk"
    days = one_sided(rows, source)
    if days.empty:
        return []
    side = "SELL" if rule == SHORT_RULE else "BUY"
    days = days[days["side"] == side]
    if rule in ("fund_bulk", "fund_block"):
        days = days[days["client"].map(lambda c: bool(FUND.search(c))).astype(bool)]
    elif rule in ("holder_bulk", SHORT_RULE):
        days = days[days["value"] >= BIG_VALUE]
    elif rule != "repeat_buyer":
        raise ValueError(rule)
    return sorted({(s, d) for s, d in zip(days["symbol"], days["day"], strict=True)})


def repeat_signals(
    rows: list[Mapping[str, Any]], sessions: list[date], window: int = REPEAT_WINDOW
) -> list[tuple[str, int]]:
    """(symbol, row) when a client buys a stock on a second session within `window` sessions."""

    days = one_sided(rows, "bulk")
    if days.empty:
        return []
    days = days[days["side"] == "BUY"]
    index = pd.Index(sessions)
    out = set()
    for (symbol, _), group in days.groupby(["symbol", "client"]):
        last = None
        for day in sorted(group["day"]):
            i = int(index.searchsorted(day))
            if i >= len(sessions) or sessions[i] != day:
                continue
            if last is not None and 0 < i - last <= window:
                out.add((str(symbol), i))
            last = i
    return sorted(out)


def apply_cooldown(
    signals: Iterable[tuple[str, int]], gap: int = COOLDOWN
) -> list[tuple[str, int]]:
    """One call per stock per `gap` sessions, the earliest first."""

    last: dict[str, int] = {}
    out = []
    for symbol, i in sorted(signals, key=lambda x: (x[1], x[0])):
        if i - last.get(symbol, -(10**9)) >= gap:
            out.append((symbol, i))
            last[symbol] = i
    return out


def signals_for(
    rows: list[Mapping[str, Any]], rule: str, sessions: list[date]
) -> list[tuple[str, int]]:
    if rule == "repeat_buyer":
        return apply_cooldown(repeat_signals(rows, sessions))
    index = pd.Index(sessions)
    found = []
    for symbol, day in rule_days(rows, rule):
        i = int(index.searchsorted(day))
        if i < len(sessions) and sessions[i] == day:
            found.append((symbol, i))
    return apply_cooldown(found)


# --------------------------------------------------------------------------- study


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", nargs="?", default="study", choices=("fetch", "check", "study"))
    args = parser.parse_args(argv)
    if args.command == "fetch":
        print(json.dumps(fetch(datetime.now(IST).date())))
    rows = read_store()
    counts = year_counts(rows)
    print(json.dumps(counts))
    if args.command != "study":
        return

    p = nl.load()
    sessions = list(p.close.index)
    codes = es.symbols()
    measure = rr.Measure(p)
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": st.PASS, "year_counts": counts,
        "variants": {},
    }  # fmt: skip
    for rule in (*LONG_RULES, SHORT_RULE):
        found = signals_for(rows, rule, sessions)
        short = rule == SHORT_RULE
        for uni, (low, high, slip) in UNIVERSES.items():
            kept = []
            for symbol, i in found:
                code = codes.get(symbol)
                e = i + 1
                if code not in measure.column or e >= len(sessions):
                    continue
                j = measure.column[code]
                usual, close = float(measure.turnover.iat[e, j]), float(measure.closes[i, j])
                if math.isfinite(usual) and low <= usual < high and close >= ib.MIN_PRICE:
                    kept.append((code, e))
            for exit_rule in ("day",) if short else EXITS:
                got = [
                    c for code, e in kept
                    if (c := measure.call(
                        code, e, exit_rule, short, slippage=slip, band=(low, high)
                    ))
                ]  # fmt: skip
                calls = pd.DataFrame(got, columns=["code", "day", "net", "excess", "peer", "days"])
                key = f"{rule}_{uni}_{exit_rule}"
                report["variants"][key] = {"signals": len(found), **st.judge(calls)}
                print(key, json.dumps(report["variants"][key], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
