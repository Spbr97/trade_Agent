"""Reading results overnight: buy good numbers at the open, sell the same day or let them run.

The owner's target (2026-11-05): a few successful trades that start intraday and can run for 2-3
months. Chart patterns have failed that repeatedly, so this starts from information instead.

Who is on the other side? Company results come out in the evening as a machine-readable filing. Most
people act on headlines, a price jump or a broker note days later, and earnings drift already showed
the market takes weeks to absorb a results surprise. Drift waits for the reaction to CLOSE before it
buys, so it pays for that wait. This reads the numbers themselves and is in the market at the next
open. The rival explanation, and the main risk: the opening gap has priced the numbers already.

Data: NSE's quarterly results filings, the publication minute from its list and the figures from
each filing's XBRL (corporates-financial-results to early 2025, integrated-filing-results after).
Saved by the fetch commands to data/m14_m18/results_figures/. A stock uses consolidated figures if
it has 12 or more consolidated filings, otherwise standalone, so a comparison never mixes the two.
Figures come from the filing's three-month context: profit (ProfitLossForPeriod, or
ProfitLossForThePeriod for banks) and sales (RevenueFromOperations, or InterestEarned for banks).

Point in time. A filing counts only if the market was shut when it appeared: published from 15:30,
before 09:00, or on a non-session day. Entry is the next session's open, 0.05% slippage each way.
A filing cannot signal if it is a revision, if it appeared 90 or more days after its quarter
ended, or if a comparison quarter was published later than it. Liquid stocks only: 20-session
traded value >= Rs 5 crore known before the entry, price >= Rs 20.

Rules (quarter against the same quarter a year before; both years' profit must be positive):
  strong        profit up 25%+ and sales up 10%+;
  sue           profit surprise >= 2: (profit - year-ago profit) / the spread of that same
                difference over the previous 8 quarters, with profit positive now;
  quiet_strong  strong, and the open is -1% to +2% from the previous close: not priced yet;
  loud_strong   strong, and the open is +3% or more: priced, and is it still running?
  weak_short    profit down 25%+ and sales not up (year-ago profit positive): SHORT, same day only.
Exits for the four long rules:
  day      that session's close, intraday charges;
  20, 60   the close of the 20th or 60th session, delivery charges;
  runner   sell at the first close if it is below the entry, intraday charges. Otherwise hold, and
           sell at the first close 10% below the highest close, or at the 60th, delivery charges.
Charges: a Rs 10,000 trade's real charges. Excess = net minus the equal-weight liquid market over
the same open-to-close span.

PASS BAR (written 2026-11-05, before any figure was downloaded). A variant passes only if:
  1. at least 100 calls;
  2. mean net return per call > 0 after charges;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit;
  4. positive mean excess in both halves (split at the median signal date);
  5. it beats stocks that had already risen as much: net minus the average same-universe stock in
     the same quintile of the prior 60 sessions' return over the same span, mean > 0, t >= 2.0;
  6. positive mean excess in at least 60% of calendar quarters holding 5 or more calls.
4 rules x 4 exits + the short = 17 variants; t >= 3 is about Bonferroni's bar for seventeen. A pass
becomes a proposal for FORWARD paper calls, never a live call. Research only.

ADDED 2026-11-06, a second bar for a mirror trade. The first run could read only filings from 2022
on (older ones never define their "OneD" context), so it covered entries from 2023-07. There,
buying strong results at the open lost by that close (strong_day -0.74% vs market, t -4.61;
quiet_strong_day -0.67%, t -3.73). That suggests SHORTING them at the open and covering at the
close, a hypothesis found in that data, so it cannot be judged on it. Two variants,
strong_short_day and quiet_strong_short_day, are judged ONLY on entries before 2023-07-01, whose
figures had not been read when this was written. Same six-part bar, intraday charges. A pass still
needs forward paper trades. The 17 original variants and their bar are unchanged and now run on
all the history. `_recent` lines show the mirror on the data that suggested it, never judged.

Caveats: the price store holds stocks listed today, which flatters long tests. The open is known
only at the auction's end, so an open-based filter needs the 09:08 pre-open price in real use.
Profit includes one-offs; a study of the number itself, not of analysts' expectations.

RESULT (2026-11-06, fixed parser, 41,939 filings, entries 2019-07 to 2026-11, no format warning):
ALL 17 FAIL, AND BOTH MIRRORS FAIL THEIR BAR.
                       calls  net/call  vs market (week t)
  strong_day           3,927   -1.03%   -0.84% (t -6.97)  good numbers fall from the open, every era
  quiet_strong_day     2,160   -0.96%   -0.79% (t -6.57)
  strong_20            3,680   +0.52%   -1.65% (t -3.71)
  strong_60            3,611   +4.14%   -0.27% (t -0.37)
  sue_60                 874   +2.86%   +1.66% (t  1.46)
  loud_strong_60         855   +5.56%   +2.01% (t  1.68)  closest long; peers t 1.25
  strong_runner        3,817   -0.34%   -0.71% (t -2.36)  the start-intraday-then-carry exit loses
  weak_short_day       1,317   -0.28%   -0.45% (t -2.95)  bad numbers do not fall from the open
Mirror, held out, entries 2019-08 to 2023-06:
  strong_short_day     1,271   +0.56%   +0.43% (t  2.33)  62% win; 2nd half -0.07%: FAIL
  quiet_strong_short     775   +0.40%   +0.37% (t  2.21)  62% win; 57% of quarters: FAIL
Not judged, the data that suggested it, 2023-07 on:
  strong_short_day     2,656   +0.51%   +0.24% (t  1.53)  59% win
  quiet_strong_short   1,385   +0.49%   +0.20% (t  1.15)  60% win
The opening gap does not price good results away, buyers are simply worse off at the open. The short
made about +0.5% a trade and won about 60% in both periods, but much of that is the whole market
sliding from its open (-0.13% and -0.27% over these calls), and the edge over the market is under
the bar and thins with time. A lead for forward paper trades at most, not a pass.

    python -m tradedesk_lab.results_reading fetch-index
    python -m tradedesk_lab.results_reading fetch-figures
    python -m tradedesk_lab.results_reading
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time as time_module
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import amount_test as am
from tradedesk_lab import event_studies as es
from tradedesk_lab import institutional_stakes as st
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "results_figures"
INDEX_DIR = STORE / "index"
FIGURES = STORE / "figures.jsonl"
OUT_DIR = OUTPUT / "results_reading"
OLD_URL = "https://www.nseindia.com/api/corporates-financial-results"
NEW_URL = "https://www.nseindia.com/api/integrated-filing-results"
NEW_TYPE = "Integrated Filing- Financials"
OLD_FIRST, NEW_FIRST = date(2018, 7, 1), date(2025, 1, 1)
MARKET_OPEN, MARKET_SHUT = time(9, 0), time(15, 30)
MAX_LATE_DAYS = 90
MIN_CONSOLIDATED = 12
SLIPPAGE = 0.0005
MIN_TURNOVER, MIN_PRICE = 5e7, 20.0
PROFIT_UP, SALES_UP = 0.25, 0.10
SUE_MIN, SUE_QUARTERS = 2.0, 8
QUIET = (-0.01, 0.02)
LOUD = 0.03
TRAIL, LONG_HOLD = 0.10, 60
EXITS = ("day", "20", "60", "runner")
LONG_RULES = ("strong", "sue", "quiet_strong", "loud_strong")
NOTIONAL = 10_000.0
QUARTER_CONTEXT = "OneD"
HELD_OUT_END = date(2023, 7, 1)
MIRRORS = {"strong_short_day": "strong", "quiet_strong_short_day": "quiet_strong"}
SUSPECT_UNREADABLE = 0.2
_PROFIT = ("ProfitLossForPeriod", "ProfitLossForThePeriod")
_SALES = ("RevenueFromOperations", "InterestEarned")


# --------------------------------------------------------------------------- parsing


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_figures(text: str | bytes) -> dict[str, float] | None:
    """Profit and sales from the filing's plain three-month context; None if it has neither."""

    root = ET.fromstring(text)
    quarter: set[str] = set()
    defined: set[str] = set()
    for node in root.iter():
        if _local(node.tag) != "context":
            continue
        parts = {_local(x.tag): (x.text or "").strip() for x in node.iter()}
        if "segment" in parts or "startDate" not in parts or "endDate" not in parts:
            continue
        try:
            span = date.fromisoformat(parts["endDate"]) - date.fromisoformat(parts["startDate"])
        except ValueError:
            continue
        defined.add(node.get("id") or "")
        if 80 <= span.days <= 100:
            quarter.add(node.get("id") or "")
    # Filings before 2023 use NSE's "OneD" for the quarter without defining that context. Where it
    # is defined, its span must still be a quarter.
    if QUARTER_CONTEXT not in defined:
        quarter.add(QUARTER_CONTEXT)
    found: dict[str, float] = {}
    named: dict[str, float] = {}
    for node in root:
        name = _local(node.tag)
        context = node.get("contextRef")
        if context in quarter and name in (*_PROFIT, *_SALES):
            try:
                value = float(node.text or "")
            except ValueError:
                continue
            key = "profit" if name in _PROFIT else "sales"
            found.setdefault(key, value)
            if context == QUARTER_CONTEXT:
                named.setdefault(key, value)
    found |= named  # a March filing's year-to-date context can also span three months
    return found if {"profit", "sales"} <= found.keys() else None


def _parse(value: Any, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip().title(), fmt)
    except ValueError:
        return None


def old_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A row of the older list, normalised; None if it is not a plain quarter's filing."""

    if str(row.get("cumulative")) != "Non-cumulative":
        return None
    nature = "consolidated" if row.get("consolidated") == "Consolidated" else "standalone"
    return _normal(row.get("symbol"), row.get("toDate"), row.get("broadCastDate"), nature,
                   False, row.get("seqNumber"), row.get("xbrl"))  # fmt: skip


def new_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A row of the integrated list, normalised."""

    nature = "consolidated" if row.get("consolidated") == "Consolidated" else "standalone"
    return _normal(row.get("symbol"), row.get("qe_Date"), row.get("broadcast_Date"), nature,
                   str(row.get("type_Sub")) != "Original", row.get("seq_Id"), row.get("xbrl"))  # fmt: skip  # noqa: E501


def _normal(symbol: Any, quarter: Any, published: Any, nature: str, revised: bool,
            record: Any, xbrl: Any) -> dict[str, Any] | None:  # fmt: skip
    q = _parse(quarter, "%d-%b-%Y")
    at = _parse(published, "%d-%b-%Y %H:%M:%S")
    if q is None or at is None or not symbol or (q + timedelta(days=1)).day != 1:
        return None
    if q.month not in (3, 6, 9, 12):
        return None
    return {"symbol": str(symbol).strip(), "quarter": q.date().isoformat(),
            "published": at.replace(tzinfo=IST).isoformat(), "nature": nature, "revised": revised,
            "record_id": f"{nature[0]}{record}", "xbrl": str(xbrl or "")}  # fmt: skip


def choose_nature(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """symbol -> the set of figures it is compared on, so no comparison mixes the two."""

    counts: Counter[str] = Counter()
    quarters: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r["nature"] == "consolidated":
            quarters[r["symbol"]].add(r["quarter"])
        counts[r["symbol"]] += 1
    return {
        s: "consolidated" if len(quarters[s]) >= MIN_CONSOLIDATED else "standalone" for s in counts
    }


# --------------------------------------------------------------------------- signals


def entry_session(sessions: list[date], at: datetime) -> int | None:
    """The row of the session whose open first follows `at`, or None if the market was open."""

    day, clock = at.date(), at.time()
    i = int(pd.Index(sessions).searchsorted(day))
    is_session = i < len(sessions) and sessions[i] == day
    if is_session and MARKET_OPEN <= clock < MARKET_SHUT:
        return None
    if is_session and clock >= MARKET_SHUT:
        i += 1
    return i if i < len(sessions) else None


def _quarter_back(quarter: date, n: int) -> date:
    month = quarter.month - 3 * n
    year = quarter.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    following = date(year + (month == 12), month % 12 + 1, 1)
    return following - timedelta(days=1)


def one_per_quarter(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, date], dict[str, Any]]:
    """(symbol, quarter) -> the filing to use: an original over a revision, then the earliest."""

    out: dict[tuple[str, date], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (bool(r.get("revised")), r["published"])):
        out.setdefault((row["symbol"], date.fromisoformat(row["quarter"])), dict(row))
    return out


def surprise(table: Mapping[tuple[str, date], Mapping[str, Any]], symbol: str,
             quarter: date) -> float | None:  # fmt: skip
    """How unusual this year-on-year change in profit is against the previous 8 quarters'."""

    diffs: list[float] = []
    now = table[(symbol, quarter)]
    for n in range(0, SUE_QUARTERS + 1):
        here = table.get((symbol, _quarter_back(quarter, n)))
        year_ago = table.get((symbol, _quarter_back(quarter, n + 4)))
        if here is None or year_ago is None or here["published"] > now["published"]:
            return None
        if year_ago["published"] > now["published"]:
            return None
        diffs.append(float(here["profit"]) - float(year_ago["profit"]))
    spread = statistics.stdev(diffs[1:])
    return diffs[0] / spread if spread > 0 else None


def classify(table: Mapping[tuple[str, date], Mapping[str, Any]], symbol: str,
             quarter: date) -> dict[str, Any] | None:  # fmt: skip
    """What this filing's numbers say, or None if it cannot signal. Gap rules are added later,
    because they need the open."""

    now = table[(symbol, quarter)]
    base = table.get((symbol, _quarter_back(quarter, 4)))
    if base is None or now.get("revised") or base["published"] > now["published"]:
        return None
    if datetime.fromisoformat(now["published"]).date() - quarter >= timedelta(days=MAX_LATE_DAYS):
        return None
    profit, then = float(now["profit"]), float(base["profit"])
    sales, sales_then = float(now["sales"]), float(base["sales"])
    if then <= 0 or sales_then <= 0:
        return None
    grew, sold = profit / then - 1, sales / sales_then - 1
    strong = profit > 0 and grew >= PROFIT_UP and sold >= SALES_UP
    weak = grew <= -PROFIT_UP and sold <= 0
    score = surprise(table, symbol, quarter)
    return {
        "strong": strong,
        "weak": weak,
        "sue": profit > 0 and score is not None and score >= SUE_MIN,
        "published": datetime.fromisoformat(now["published"]),
    }


def rules_hit(numbers: Mapping[str, Any], gap: float) -> list[str]:
    hits = []
    if numbers["strong"]:
        hits.append("strong")
        if QUIET[0] <= gap <= QUIET[1]:
            hits.append("quiet_strong")
        if gap >= LOUD:
            hits.append("loud_strong")
    if numbers["sue"]:
        hits.append("sue")
    if numbers["weak"]:
        hits.append("weak_short")
    return hits


# --------------------------------------------------------------------------- exits


def exit_row(exit_rule: str, entry: float, closes: Any, e: int) -> tuple[int, bool] | None:
    """(the row sold at, whether it was same-day) or None if the history stops first. `closes` is
    one stock's closes; the entry is that session's open."""

    n = len(closes)
    if exit_rule == "day":
        return (e, True) if math.isfinite(closes[e]) else None
    if exit_rule in ("20", "60"):
        x = e + int(exit_rule) - 1
        return (x, False) if x < n and math.isfinite(closes[x]) else None
    if not math.isfinite(closes[e]):
        return None
    if closes[e] < entry:
        return e, True
    peak = closes[e]
    last = e + LONG_HOLD - 1
    if last >= n:
        return None
    for k in range(e + 1, last + 1):
        price = closes[k]
        if not math.isfinite(price):
            continue
        if price <= peak * (1 - TRAIL):
            return k, False
        peak = max(peak, price)
    return (last, False) if math.isfinite(closes[last]) else None


class Measure:
    """Prices one call: net of charges, against the market and against equally strong stocks."""

    def __init__(self, p: nl.Panels) -> None:
        self.p = p
        self.turnover = (p.close * p.volume).rolling(20).mean().shift(1)
        self.peers = st.Peers(p, self.turnover)
        self.opens, self.closes = p.open.to_numpy(), p.close.to_numpy()
        self.delivery = nl.cost_function()
        self.cm = am.cost_model()
        self.column = {c: j for j, c in enumerate(p.close.columns)}
        self._market: dict[tuple[int, int], float] = {}

    def market(self, e: int, x: int) -> float:
        if (e, x) not in self._market:
            span = self.p.close.iloc[x] / self.p.open.iloc[e] - 1
            ok = self.turnover.iloc[e] >= MIN_TURNOVER
            self._market[(e, x)] = float(span[ok & span.notna()].mean())
        return self._market[(e, x)]

    def intraday(self, buy: float, sell: float) -> float:
        qty = max(1, int(NOTIONAL // max(buy, sell)))
        return am.intraday_cost(self.cm, buy, sell, qty) / (qty * max(buy, sell))

    def call(self, code: str, e: int, exit_rule: str, short: bool = False) -> dict[str, Any] | None:
        j = self.column[code]
        raw = float(self.opens[e, j])
        if not (math.isfinite(raw) and raw > 0):
            return None
        sold = exit_row(exit_rule, raw, self.closes[:, j], e)
        if sold is None:
            return None
        x, same_day = sold
        close = float(self.closes[x, j])
        if short:
            entry, out = raw * (1 - SLIPPAGE), close * (1 + SLIPPAGE)
            net = (entry - out) / entry - self.intraday(out, entry)
        else:
            entry, out = raw * (1 + SLIPPAGE), close * (1 - SLIPPAGE)
            charges = self.intraday(entry, out) if same_day else self.delivery(entry, out)
            net = out / entry - 1 - charges
        mkt = self.market(e, x)
        peer_table = self.peers.table(e - 1, x - e + 1, MIN_TURNOVER, math.inf) if e > 0 else None
        peer = math.nan
        if peer_table is not None and code in peer_table.index:
            peer = float(peer_table[code])
        return {
            "code": code, "day": self.p.close.index[e], "net": net,
            "excess": net + mkt if short else net - mkt,
            "peer": net + peer if short else net - peer,
            "days": x - e + 1,
        }  # fmt: skip


# --------------------------------------------------------------------------- data


def _month_ranges(start: date, end: date) -> Iterable[tuple[date, date]]:
    first = start.replace(day=1)
    while first <= end:
        following = (first + timedelta(days=32)).replace(day=1)
        yield first, min(following - timedelta(days=1), end)
        first = following


def fetch_index(today: date, *, pause_seconds: float = 1.5) -> int:
    """Save both lists of results filings, a month per request; a finished month is kept."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    saved = 0
    with httpx.Client(headers=_NSE_HEADERS, timeout=180, follow_redirects=True) as client:
        client.get("https://www.nseindia.com/")  # sets the cookies the API requires
        for name, url, start, extra in (
            ("old", OLD_URL, OLD_FIRST, {"period": "Quarterly"}),
            ("new", NEW_URL, NEW_FIRST, {"type": NEW_TYPE, "size": 5000}),
        ):
            folder = INDEX_DIR / name
            folder.mkdir(parents=True, exist_ok=True)
            for first, last in _month_ranges(start, today):
                path = folder / f"{first:%Y-%m}.json"
                if path.exists() and last < today:
                    continue
                response = client.get(url, params={
                    "index": "equities", "from_date": first.strftime("%d-%m-%Y"),
                    "to_date": last.strftime("%d-%m-%Y"), **extra,
                })  # fmt: skip
                response.raise_for_status()
                body = response.json()
                rows = body.get("data", []) if isinstance(body, dict) else body
                path.write_text(json.dumps(rows), encoding="utf-8")
                saved += len(rows)
                time_module.sleep(pause_seconds)
    return saved


def read_index(root: Path = INDEX_DIR) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for name, normal in (("old", old_row), ("new", new_row)):
        for path in sorted((root / name).glob("*.json")):
            for raw in json.loads(path.read_text(encoding="utf-8")):
                row = normal(raw)
                if row and row["record_id"] not in seen:
                    seen.add(row["record_id"])
                    out.append(row)
    return out


def to_fetch(rows: Iterable[Mapping[str, Any]], symbols: set[str]) -> list[dict[str, Any]]:
    """Each stock's filings on one set of figures, one a quarter, an original over a revision."""

    rows = [r for r in rows if r["symbol"] in symbols and r["xbrl"].endswith(".xml")]
    nature = choose_nature(rows)
    chosen = [r for r in rows if r["nature"] == nature[r["symbol"]]]
    return list(one_per_quarter(chosen).values())


def fetch_figures(
    symbols: set[str], *, pause_seconds: float = 0.5, max_errors: int = 25
) -> Counter:
    """Download and parse each filing, keeping only the figures. Resumable."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    STORE.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if FIGURES.exists():
        rows = [json.loads(x) for x in FIGURES.read_text(encoding="utf-8").splitlines() if x]
        done = {r["record_id"] for r in rows if "profit" in r}  # failed reads are tried again
    pending = [r for r in to_fetch(read_index(), symbols) if r["record_id"] not in done]
    print(f"{len(pending)} filings to fetch, {len(done)} already done", flush=True)
    counts: Counter = Counter()
    errors = 0
    with (
        httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as client,
        FIGURES.open("a", encoding="utf-8") as out,
    ):
        for n, row in enumerate(pending, 1):
            try:
                response = client.get(row["xbrl"])
                if response.status_code == 404:  # that filing has no file; the rest may well
                    counts["unusable"] += 1
                    missing = {k: v for k, v in row.items() if k != "xbrl"}
                    out.write(json.dumps(missing | {"error": "404"}) + "\n")
                    out.flush()
                    errors = 0
                    time_module.sleep(pause_seconds)
                    continue
                response.raise_for_status()
            except httpx.HTTPError as exc:
                errors += 1
                if errors >= max_errors:
                    raise RuntimeError(f"{errors} errors in a row, last: {exc}") from exc
                time_module.sleep(min(60.0, pause_seconds * 2**errors))
                continue
            errors = 0
            try:
                figures = parse_figures(response.content)
            except ET.ParseError:
                figures = None
            record = {k: v for k, v in row.items() if k != "xbrl"}
            counts["parsed" if figures else "unusable"] += 1
            record |= figures or {"error": "no profit or sales figure"}
            out.write(json.dumps(record) + "\n")
            out.flush()
            if n % 500 == 0:
                print(f"{n}/{len(pending)} {dict(counts)}", flush=True)
            time_module.sleep(pause_seconds)
    return counts


def read_figures(path: Path = FIGURES) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if "profit" in r]


def suspect_years(path: Path = FIGURES) -> dict[str, float]:
    """Years where a large share of filings could not be read: a format the parser has missed."""

    if not path.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["record_id"]] = row
    total: Counter = Counter()
    failed: Counter = Counter()
    for row in latest.values():
        total[row["quarter"][:4]] += 1
        failed[row["quarter"][:4]] += "profit" not in row
    return {
        y: failed[y] / n for y, n in sorted(total.items())
        if n >= 50 and failed[y] / n > SUSPECT_UNREADABLE
    }  # fmt: skip


def liquid_symbols(p: nl.Panels, codes: Mapping[str, str]) -> set[str]:
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    since = [d for d in turnover.index if d >= OLD_FIRST]
    reached = turnover.loc[since].max() >= MIN_TURNOVER
    return {s for s, c in codes.items() if c in reached.index and reached[c]}


# --------------------------------------------------------------------------- study


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "command", nargs="?", default="study", choices=("fetch-index", "fetch-figures", "study")
    )
    args = parser.parse_args(argv)
    if args.command == "fetch-index":
        print("saved", fetch_index(datetime.now(IST).date()), "rows to", INDEX_DIR)
        return
    p = nl.load()
    codes = es.symbols()
    if args.command == "fetch-figures":
        print(dict(fetch_figures(liquid_symbols(p, codes))))
        return

    sessions = list(p.close.index)
    measure = Measure(p)
    table = one_per_quarter(read_figures())
    suspect = suspect_years()
    if suspect:
        print("WARNING - parser may have missed a format, results not valid:", suspect, flush=True)
    picks: dict[str, list[tuple[str, int]]] = defaultdict(list)
    skipped: Counter = Counter()
    hours: Counter = Counter()
    for (symbol, quarter) in sorted(table):
        code = codes.get(symbol)
        if code not in measure.column:
            skipped["no price history"] += 1
            continue
        numbers = classify(table, symbol, quarter)
        if numbers is None:
            skipped["cannot signal"] += 1
            continue
        hours[numbers["published"].hour] += 1
        e = entry_session(sessions, numbers["published"])
        if e is None or e < 1:
            skipped["filed in market hours"] += 1
            continue
        j = measure.column[code]
        opening, before = float(measure.opens[e, j]), float(measure.closes[e - 1, j])
        usual = float(measure.turnover.iat[e, j])
        if not (math.isfinite(opening) and math.isfinite(before) and before > 0):
            skipped["no prices that day"] += 1
            continue
        if not (math.isfinite(usual) and usual >= MIN_TURNOVER and before >= MIN_PRICE):
            skipped["not liquid then"] += 1
            continue
        for rule in rules_hit(numbers, opening / before - 1):
            picks[rule].append((code, e))
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": st.PASS,
        "filings_listed": len(read_index()), "figures": len(table), "suspect_years": suspect,
        "skipped": dict(skipped), "publication_hours": dict(sorted(hours.items())),
        "signals": {k: len(v) for k, v in picks.items()}, "variants": {},
    }  # fmt: skip
    print(json.dumps({k: report[k] for k in ("figures", "skipped", "signals")}), flush=True)
    plan = [(r, x, False) for r in LONG_RULES for x in EXITS] + [("weak_short", "day", True)]
    for rule, exit_rule, short in plan:
        rows = [c for code, e in picks[rule] if (c := measure.call(code, e, exit_rule, short))]
        calls = pd.DataFrame(rows, columns=["code", "day", "net", "excess", "peer", "days"])
        key = f"{rule}_{exit_rule}"
        verdict = st.judge(calls)
        verdict["mean_days_held"] = float(calls["days"].mean()) if len(calls) else None
        report["variants"][key] = verdict
        print(key, json.dumps(verdict, default=str), flush=True)
    for name, source in MIRRORS.items():
        for key, wanted in ((name, True), (f"{name}_recent", False)):
            rows = [
                c for code, e in picks[source]
                if (sessions[e] < HELD_OUT_END) == wanted
                and (c := measure.call(code, e, "day", short=True))
            ]  # fmt: skip
            calls = pd.DataFrame(rows, columns=["code", "day", "net", "excess", "peer", "days"])
            report["variants"][key] = st.judge(calls)
            print(key, json.dumps(report["variants"][key], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
