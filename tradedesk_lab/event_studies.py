"""Special-situation events: rights issues, buybacks, open offers and IPO lock-in expiries.

Question (owner, 2026-10-28, after OLAELEC's rights-issue rebound and ZETWERK's post-debut
rebound): can news and events, not price patterns, find big movers early? The NSE announcements
archive (data/m14_m18/filings, 2021-01 to 2026-07, from src/tradedesk/filings.py) dates every
filing to the minute, so each event family can be tested on when the market could know it.

Events (first filing per stock and family, then none for 180 days; a filing after 15:30 or on a
holiday counts on the next session; the call is made after that session's close and bought at
the NEXT open, as drift calls are):
  rights_announce  the first rights-issue filing (board approval or terms);
  rights_after_30  the same events, bought 30 sessions later - about when an issue has opened
                   and closed and the rights-entitlement selling is over (the OLAELEC pattern;
                   exact issue dates are in the PDFs, not the filing text);
  buyback          the first buyback filing (board approval or public announcement);
  open_offer       the first open-offer filing (public announcement or detailed statement) -
                   an acquirer must offer to buy shares, usually at a premium;
  lockin_30/90/180 30, 90 and 180 calendar days after a stock's first candle (anchor and
                   pre-IPO lock-ins end then), from daily prices, listings 2017 on.
The offer prices (the "floor") sit in the PDFs, so this tests the announcement itself, not the
gap to the offer price.

Filters: signal-day close >= Rs 50 and traded value >= Rs 5 crore. Exits: the close 20 and 60
sessions after the signal. Costs: the real delivery round trip of a Rs 10,000 call plus 0.05%
slippage each way (tradedesk_lab/new_listings.py's outcome and costs). Excess = net minus the
equal-weight liquid market over the same days.

PASS BAR (written 2026-10-31, before any result was seen). A variant passes only if all hold:
  1. at least 60 calls (event families are smaller than price patterns);
  2. mean net return per call > 0 after costs;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit;
  4. positive mean excess in both halves (split at the median signal date).
7 events x 2 exits = 14 variants; t >= 3 is about Bonferroni's bar for fourteen. A pass becomes a
proposal for forward paper calls, never a live call. Research only.

Caveat: the price store holds stocks listed today, so stocks that later delisted are missing -
this flatters every long test, so a pass needs to be clear, and a fail is a fail.

    python -m tradedesk_lab.event_studies

RESULT (2026-10-31, filings 2021-01 to 2026-09, lock-ins from listings 2017 on): ALL 14 FAIL.
                     calls  net/call  vs market (week t)
  rights_announce_20   136    -3.5%     -4.6% (t -5.5)  27% win - stocks keep falling after
  rights_announce_60   130    +0.5%     -4.5% (t -3.1)        a rights-issue filing
  rights_after_30_20   109    +1.7%     -0.4% (t -0.4)  the OLAELEC rebound is not a pattern
  rights_after_30_60   102    +5.0%     -0.4% (t -0.2)
  buyback_20           198    -0.4%     -2.5% (t -3.7)  bought the next open, the pop fades
  buyback_60           190    -0.0%     -4.2% (t -2.7)
  open_offer_20/60      81    +0.7/+4.1% -2.3/-3.6% (t -1.9)
  lockin_30_20         410    +2.1%     +1.3% (t +1.7)  closest; the rest -1.6% to +1.0%
  lockin 90/180         -                t -1.6 to +0.7
Rights announcements and buyback announcements are followed by UNDERPERFORMANCE (t -5.5, -3.7):
long-only overnight, so they cannot be traded short; at most a reason to avoid buying such a
stock, which is a new hypothesis that would need its own pre-registered test.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

FILINGS = ROOT / "data" / "m14_m18" / "filings"
OUT_DIR = OUTPUT / "event_studies"
GAP_DAYS = 180
RIGHTS_DELAY = 30
LOCKINS = (30, 90, 180)
SESSION_CLOSE = time(15, 30)
PASS = {"min_calls": 60, "min_t": 3.0}

_RIGHTS = re.compile(r"\brights? (issue|basis|equity|entitlement)", re.I)
_BUYBACK = re.compile(r"\bbuy[ -]?back", re.I)
_OPEN_OFFER = re.compile(r"\bopen offer\b|detailed public statement|regulations? 3\(1\)", re.I)
_LATER_STAGE = re.compile(r"daily buy back|closure of buy ?back|post (buyback|offer)", re.I)


def family(desc: str, text: str) -> str | None:
    """Which event family a filing announces (None: not one of them, or a later stage)."""

    both = f"{desc} | {text}"
    if _LATER_STAGE.search(both):
        return None
    if desc == "Rights Issue" or _RIGHTS.search(text):
        return "rights"
    if desc.lower().startswith(("buyback", "public announcement - buyback")) or _BUYBACK.search(
        text
    ):
        return "buyback"
    if desc in ("Open Offer", "Public Announcement-Open Offer") or _OPEN_OFFER.search(text):
        return "open_offer"
    return None


def read_filings(root: Path = FILINGS) -> Iterable[dict[str, Any]]:
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def first_events(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, str, datetime]]:
    """(family, symbol, filed_at): the first filing of a family per stock, then 180 days quiet."""

    found = sorted(
        (fam, r["symbol"], datetime.fromisoformat(r["filed_at"]))
        for r in rows
        if (fam := family(r.get("desc") or "", r.get("text") or ""))
    )
    out: list[tuple[str, str, datetime]] = []
    last: dict[tuple[str, str], datetime] = {}
    for fam, sym, at in sorted(found, key=lambda x: x[2]):
        prev = last.get((fam, sym))
        if prev is None or at - prev > timedelta(days=GAP_DAYS):
            out.append((fam, sym, at))
        last[(fam, sym)] = at
    return out


def signal_row(sessions: list[date], at: datetime) -> int | None:
    """The session whose close is the first one after the filing."""

    day = at.date() if at.time() < SESSION_CLOSE else at.date() + timedelta(days=1)
    i = pd.Index(sessions).searchsorted(day)
    return int(i) if i < len(sessions) else None


def event_signals(
    p: nl.Panels, events: Iterable[tuple[str, str, datetime]], symbols: Mapping[str, str]
) -> dict[str, list[tuple[str, int]]]:
    sessions = list(p.close.index)
    out: dict[str, list[tuple[str, int]]] = {
        "rights_announce": [], "rights_after_30": [], "buyback": [], "open_offer": [],
    }  # fmt: skip
    for fam, sym, at in events:
        code = symbols.get(sym)
        i = signal_row(sessions, at)
        if code not in p.close.columns or i is None:
            continue
        if fam == "rights":
            out["rights_announce"].append((code, i))
            if i + RIGHTS_DELAY < len(sessions):
                out["rights_after_30"].append((code, i + RIGHTS_DELAY))
        else:
            out[fam].append((code, i))
    return out


def lockin_signals(p: nl.Panels, listed: Mapping[str, int]) -> dict[str, list[tuple[str, int]]]:
    sessions = list(p.close.index)
    out: dict[str, list[tuple[str, int]]] = {f"lockin_{d}": [] for d in LOCKINS}
    for code, i0 in listed.items():
        for d in LOCKINS:
            i = int(pd.Index(sessions).searchsorted(sessions[i0] + timedelta(days=d)))
            if i < len(sessions):
                out[f"lockin_{d}"].append((code, i))
    return out


def liquid(p: nl.Panels, code: str, i: int) -> bool:
    c, v = p.close[code].iat[i], p.volume[code].iat[i]
    return bool(c >= nl.MIN_PRICE and c * v >= nl.MIN_TURNOVER)


def judge(calls: pd.DataFrame) -> dict[str, Any]:
    out = nl.judge(calls)
    if out.get("calls") and out["calls"] < PASS["min_calls"]:
        fails = [f for f in out["fails"] if not f.startswith("only ")]
        out["fails"] = [f"only {out['calls']} calls", *fails]
    elif out.get("calls"):
        out["fails"] = [f for f in out["fails"] if not f.startswith("only ")]
    out["passed"] = bool(out.get("calls")) and not out["fails"]
    return out


def symbols(db: Path = nl.DB) -> dict[str, str]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        return dict(con.execute(
            "SELECT trading_symbol, scrip_code FROM instruments WHERE exch='NSE' AND series='EQ'"
        ).fetchall())  # fmt: skip
    finally:
        con.close()


def main() -> None:
    p = nl.load()
    mkt = nl.market(p)
    cost = nl.cost_function()
    events = first_events(read_filings())
    sigs = {**event_signals(p, events, symbols()), **lockin_signals(p, nl.listings(p))}
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS,
        "events_found": {f: sum(1 for e in events if e[0] == f)
                         for f in ("rights", "buyback", "open_offer")},
        "variants": {},
    }  # fmt: skip
    for name, found in sigs.items():
        kept = [(code, i) for code, i in found if liquid(p, code, i)]
        for hold in nl.HOLDS:
            rows = [o for code, i in kept if (o := nl.outcome(p, mkt, code, i, hold, cost))]
            calls = pd.DataFrame(rows, columns=["code", "day", "net", "excess"])
            key = f"{name}_{hold}"
            report["variants"][key] = {"signals": len(found), "liquid": len(kept), **judge(calls)}
            print(key, json.dumps(report["variants"][key], default=str))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
