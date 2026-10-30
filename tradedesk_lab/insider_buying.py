"""Insider buying: do promoters and directors buying their own shares in the open market know
something the price does not yet show?

First principles (owner, 2026-10-31: aim for a breakthrough): price patterns are what everyone
mines; edges come from information others lack. An insider who buys in the open market with
their own money - not an ESOP, allotment, gift, pledge or family transfer - has one reason to:
they think the shares are cheap. Studies of insider purchases in the US and elsewhere find the
stocks beat the market for months, most in small, little-followed companies. Earnings drift,
our one proven edge, is the same kind of edge: the market under-reacts to information.

Data: NSE's insider-trading disclosures (SEBI PIT Regulation 7, the corporates-pit API), 2021
on, saved by `fetch` to data/m14_m18/insider_pit/<week>.json. Each row has who traded (person
category), buy or sell, how (acqMode), the rupee value and the minute NSE published it - the
time the market could act, used for every signal here.

A qualifying buy: tdpTransactionType "Buy", acqMode a market purchase, person a promoter,
promoter-group member, director or key managerial person, plain equity shares (no derivative).
A signal: the session whose close first follows a disclosure (after 15:30 or on a holiday: the
next session); one call per stock per 20 sessions; bought at the next open, as drift calls are.

Variants (signal-day rupee value of qualifying buys published that session):
  any_buy    Rs 10 lakh or more;
  big_buy    Rs 1 crore or more;
  cluster    a second different insider buying within 30 days of another (Rs 10 lakh+ total);
  heavy      the buys are worth at least half a day's usual traded value (20-session average).
Two universes, judged apart, because the literature's edge lives in small stocks and a Rs 10,000
call can trade them:
  liquid     20-session traded value >= Rs 5 crore, 0.05% slippage each way;
  small      Rs 1-5 crore, 0.25% slippage each way (wider spreads).
Both: price >= Rs 20. Exits: the close 20 and 60 sessions after the signal. Costs: the real
delivery round trip of a Rs 10,000 call (new_listings.cost_function). Excess = net minus the
equal-weight liquid market over the same days.

PASS BAR (written 2026-10-31, before any insider data was downloaded). A variant passes only if:
  1. at least 100 calls;
  2. mean net return per call > 0 after costs;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit;
  4. positive mean excess in both halves (split at the median signal date).
4 rules x 2 universes x 2 exits = 16 variants; t >= 3 is about Bonferroni's bar for sixteen.
A pass becomes a proposal for FORWARD paper calls, never a live call. Research only.

Caveat: the price store holds stocks listed today, so stocks that later delisted are missing -
this flatters every long test, so a pass needs to be clear, and a fail is a fail.

    python -m tradedesk_lab.insider_buying fetch [--from 2021-01-01]
    python -m tradedesk_lab.insider_buying
"""

from __future__ import annotations

import argparse
import json
import math
import time as time_module
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import event_studies as es
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "insider_pit"
OUT_DIR = OUTPUT / "insider_buying"
NSE_PIT_URL = "https://www.nseindia.com/api/corporates-pit"
SESSION_CLOSE = time(15, 30)
INSIDERS = ("promoter", "director", "key managerial")
MIN_VALUE, BIG_VALUE = 1e6, 1e7
CLUSTER_DAYS = 30
HEAVY_X = 0.5
COOLDOWN = 20
MIN_PRICE = 20.0
UNIVERSES = {"liquid": (5e7, math.inf, 0.0005), "small": (1e7, 5e7, 0.0025)}
RULES = ("any_buy", "big_buy", "cluster", "heavy")
PASS = {"min_calls": 100, "min_t": 3.0}


# --------------------------------------------------------------------------- data


def fetch(start: date, end: date, *, store: Path = STORE, pause_seconds: float = 1.5) -> int:
    """Save NSE's PIT disclosures one week per request; a saved week is not fetched again
    unless it is the current one."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    store.mkdir(parents=True, exist_ok=True)
    saved = 0
    with httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as client:
        client.get("https://www.nseindia.com/")  # sets the cookies the API requires
        day = start
        while day <= end:
            last = min(day + timedelta(days=6), end)
            path = store / f"{day.isoformat()}.json"
            if not path.exists() or last >= end:
                response = client.get(NSE_PIT_URL, params={
                    "index": "equities", "from_date": day.strftime("%d-%m-%Y"),
                    "to_date": last.strftime("%d-%m-%Y"),
                })  # fmt: skip
                response.raise_for_status()
                body = response.json()
                rows = body.get("data", []) if isinstance(body, dict) else body
                path.write_text(json.dumps(rows), encoding="utf-8")
                saved += len(rows)
                time_module.sleep(pause_seconds)
            day = last + timedelta(days=1)
    return saved


def read_store(store: Path = STORE) -> list[dict[str, Any]]:
    seen, out = set(), []
    for path in sorted(store.glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            key = (row.get("pid"), row.get("did"), row.get("acqName"), row.get("secAcq"))
            if key not in seen:
                seen.add(key)
                out.append(row)
    return out


def published_at(row: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.strptime(str(row.get("date")), "%d-%b-%Y %H:%M").replace(tzinfo=IST)
    except ValueError:
        return None


def qualifying(row: Mapping[str, Any]) -> bool:
    """An insider's open-market purchase of plain shares with their own money."""

    return (
        str(row.get("tdpTransactionType")).strip().lower() == "buy"
        and "market purchase" in str(row.get("acqMode")).lower()
        and any(k in str(row.get("personCategory")).lower() for k in INSIDERS)
        and str(row.get("secType")).strip().lower() == "equity shares"
        and str(row.get("derivativeType")).strip() in ("-", "", "None")
    )


def _value(row: Mapping[str, Any]) -> float:
    try:
        return float(str(row.get("secVal")).replace(",", ""))
    except ValueError:
        return 0.0


def buys(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """One row per qualifying buy: symbol, person, value (Rs), published (IST datetime)."""

    out = [
        {"symbol": r["symbol"], "person": str(r.get("acqName")).strip().lower(),
         "value": _value(r), "published": at}
        for r in rows
        if qualifying(r) and (at := published_at(r)) is not None and _value(r) > 0
    ]  # fmt: skip
    return pd.DataFrame(out, columns=["symbol", "person", "value", "published"])


# --------------------------------------------------------------------------- signals


def session_buys(frame: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    """Qualifying buys grouped by (symbol, signal session): value, people, first-published."""

    if frame.empty:
        return pd.DataFrame(columns=["symbol", "i", "value", "people"])
    frame = frame.assign(i=[es.signal_row(sessions, at) for at in frame["published"]])
    frame = frame.dropna(subset=["i"]).astype({"i": int})
    grouped = frame.groupby(["symbol", "i"]).agg(
        value=("value", "sum"), people=("person", lambda s: frozenset(s))
    )
    return grouped.reset_index()


def rule_signals(
    by_session: pd.DataFrame, sessions: list[date], rule: str,
    turnover: pd.DataFrame | None = None, codes: Mapping[str, str] | None = None,
) -> list[tuple[str, int]]:  # fmt: skip
    """(symbol, signal row) per rule, one per stock per COOLDOWN sessions."""

    out: list[tuple[str, int]] = []
    for sym, rows in by_session.sort_values("i").groupby("symbol"):
        last_signal = -(10**9)
        history: list[tuple[int, frozenset[str]]] = []
        for i, value, people in rows[["i", "value", "people"]].itertuples(index=False):
            history.append((i, people))
            if value < MIN_VALUE:
                continue
            hit = False
            if rule == "any_buy":
                hit = True
            elif rule == "big_buy":
                hit = value >= BIG_VALUE
            elif rule == "cluster":
                start = sessions[i] - timedelta(days=CLUSTER_DAYS)
                window = [p for j, p in history if sessions[j] >= start]
                hit = len(frozenset().union(*window)) >= 2
            elif rule == "heavy" and turnover is not None and codes is not None:
                code = codes.get(sym)
                if code in turnover.columns:
                    usual = turnover[code].iat[i]
                    hit = bool(math.isfinite(usual) and usual > 0 and value >= HEAVY_X * usual)
            if hit and i - last_signal >= COOLDOWN:
                out.append((sym, i))
                last_signal = i
    return out


def outcome(
    p: nl.Panels, mkt: pd.Series, code: str, i: int, hold: int, cost: Any, slippage: float
) -> dict[str, Any] | None:
    if i + hold >= len(p.close.index):
        return None
    entry = float(p.open[code].iat[i + 1]) * (1 + slippage)
    exit_px = float(p.close[code].iat[i + hold]) * (1 - slippage)
    if not (math.isfinite(entry) and math.isfinite(exit_px) and entry > 0):
        return None
    net = exit_px / entry - 1 - cost(entry, exit_px)
    m = float((1 + mkt.iloc[i + 1 : i + hold + 1].fillna(0.0)).prod() - 1)
    return {"code": code, "day": p.close.index[i], "net": net, "excess": net - m}


def judge(calls: pd.DataFrame) -> dict[str, Any]:
    """new_listings' judge (its bar is the same 100 calls and t >= 3)."""

    return nl.judge(calls)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", nargs="?", default="study", choices=("fetch", "study"))
    parser.add_argument("--from", dest="start", default="2021-01-01")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        n = fetch(date.fromisoformat(args.start), datetime.now(IST).date())
        print(f"saved {n} rows to {STORE}")
        return

    p = nl.load()
    sessions = list(p.close.index)
    mkt = nl.market(p)
    cost = nl.cost_function()
    codes = es.symbols()
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    raw = read_store()
    frame = buys(raw)
    by_session = session_buys(frame, sessions)
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS,
        "disclosures": len(raw), "qualifying_buys": len(frame),
        "first": str(frame["published"].min()) if len(frame) else None,
        "last": str(frame["published"].max()) if len(frame) else None,
        "variants": {},
    }  # fmt: skip
    for rule in RULES:
        found = rule_signals(by_session, sessions, rule, turnover, codes)
        for uni, (low, high, slip) in UNIVERSES.items():
            kept = []
            for sym, i in found:
                code = codes.get(sym)
                if code not in p.close.columns:
                    continue
                usual, px = turnover[code].iat[i], p.close[code].iat[i]
                if math.isfinite(usual) and low <= usual < high and px >= MIN_PRICE:
                    kept.append((code, i))
            for hold in nl.HOLDS:
                rows = [o for code, i in kept
                        if (o := outcome(p, mkt, code, i, hold, cost, slip))]  # fmt: skip
                calls = pd.DataFrame(rows, columns=["code", "day", "net", "excess"])
                key = f"{rule}_{uni}_{hold}"
                report["variants"][key] = {"signals": len(found), **judge(calls)}
                print(key, json.dumps(report["variants"][key], default=str))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
