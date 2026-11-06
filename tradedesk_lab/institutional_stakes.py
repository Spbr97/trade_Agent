"""Institutional stakes: when funds raise their stake in a stock, does the buying carry on?

First principles (owner, 2026-11-05: aim for a breakthrough). Who is on the other side, and why
do they keep losing? A large fund cannot build a position in a day: it buys for weeks or
quarters, and the rest of the market only sees it in the quarterly filing, up to 21 days after
the quarter ends. If funds keep buying after that filing, a Rs 10,000 buyer can stand in front
of a flow that is slow, repeating and forced by the fund's own size. The sellers are whoever
ignores a stale, dull quarterly table. Unlike index changes and insider buying, both of which
failed because the event was priced in by publication, nothing here says the buying has ended.

The rival explanation, and the main risk: funds buy what has already risen. Then a rising stake
only marks a strong stock, and our rule setups already showed that holding strong stocks in a
rising market is not an edge. Bar 5 below exists for that.

Data: NSE's shareholding-pattern filings (corporate-share-holdings-master for the list and
publication minute, each filing's XBRL for the stakes), the Sep 2021 quarter on. Saved by `fetch`
to data/m14_m18/shareholding/. Stakes are computed from share counts because the XBRL's own
percent changes scale between formats (8.06 in 2021, 0.1926 in 2026).

  fpi    foreign portfolio investors in the public category, not promoter-group FPIs
  mf     mutual funds
  inst   fpi + mf + alternative funds + insurance + pension funds, the lines both formats share

Point in time. A signal is the session whose close first follows `broadcastDate`, bought at the
next open as drift calls are. A filing cannot signal if it is a revised one, if NSE published it
more than 3 days after submission (the Sep 2021 quarter was backfilled in January 2022; it is a
base only), if it is not a quarter-end filing, or if its base quarter was published later than it.
Changes compare consecutive quarters only.

Rules (changes in percentage points of the company's shares):
  fpi_up        fpi up 1.0 or more;
  mf_up         mf up 1.0 or more;
  both_up       fpi and mf each up 0.25 or more, two independent sets of buyers;
  two_quarters  inst up in this quarter and the one before, 1.0 or more in total: still buying;
  discovery     inst was under 5% and rose 1.0 or more: an under-owned stock being found;
  breadth       more mutual-fund holders by 2 or more and mf up: more funds, not one fund's bet.
Universes as in insider_buying: liquid (20-session traded value >= Rs 5 crore, 0.05% slippage each
way) and small (Rs 1-5 crore, 0.25%). Both: price >= Rs 20. Exits: the close 20 and 60 sessions
after the signal. Costs: the real delivery round trip of a Rs 10,000 call. Excess = net minus the
equal-weight liquid market over the same days.

PASS BAR (written 2026-11-05, before any stake data was downloaded). A variant passes only if:
  1. at least 100 calls;
  2. mean net return per call > 0 after costs;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit;
  4. positive mean excess in both halves (split at the median signal date);
  5. it beats stocks that had already risen as much: net minus the average same-universe stock in
     the same quintile of the prior 60 sessions' return, on the signal day, mean > 0 with t >= 2.0;
  6. positive mean excess in at least 60% of calendar quarters holding 5 or more calls.
6 rules x 2 universes x 2 exits = 24 variants; t >= 3 is about Bonferroni's bar for twenty-four.
A pass becomes a proposal for FORWARD paper calls, never a live call. Research only.

Caveats: about five years, nearly all a rising market, and no 2020 - a pass still needs forward
proof. The price store holds stocks listed today, which flatters every long test: a pass needs to
be clear, and a fail is a fail. Calendar quarters overlap at 60 sessions.

RESULT (2026-11-06, fixed parser, 36,977 filings, entries 2022-01 to 2026-10, no format warning):
ALL 24 FAIL. Closest: both_up_liquid_60 (FPI and mutual funds each up 0.25 points, 60 sessions),
1,064 calls, +4.37% net, +3.78% over the market, t 1.99, +4.18% over equally strong stocks, t 2.16,
both halves positive, 63% of quarters. It misses only the main t >= 3, and the best t of 24 tries
being about 2 is what chance alone would give. discovery_liquid_60 +4.49%, t 1.51, nearly all in the
first half. Mutual funds alone: t 0.78; more funds holding: t -0.56. Small stocks trail the market.
A first run on 2026-11-05 was void: the parser read no FPI line for Sep 2022 - Mar 2025.

    python -m tradedesk_lab.institutional_stakes fetch-index
    python -m tradedesk_lab.institutional_stakes fetch-stakes
    python -m tradedesk_lab.institutional_stakes
"""

from __future__ import annotations

import argparse
import json
import math
import time as time_module
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import event_studies as es
from tradedesk_lab import insider_buying as ib
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "shareholding"
INDEX_DIR = STORE / "index"
STAKES = STORE / "stakes.jsonl"
OUT_DIR = OUTPUT / "institutional_stakes"
NSE_SHP_URL = "https://www.nseindia.com/api/corporate-share-holdings-master"
FIRST_MONTH = date(2021, 10, 1)
MAX_PUBLISH_DELAY = timedelta(days=3)
RULES = ("fpi_up", "mf_up", "both_up", "two_quarters", "discovery", "breadth")
UNIVERSES = ib.UNIVERSES
MIN_PRICE = ib.MIN_PRICE
BIG_MOVE, SMALL_MOVE = 1.0, 0.25
UNDER_OWNED = 5.0
MORE_FUNDS = 2
PEER_LOOKBACK, PEER_BUCKETS = 60, 5
PASS = {"min_calls": 100, "min_t": 3.0, "min_peer_t": 2.0, "min_quarter_share": 0.6}
MIN_QUARTER_CALLS = 5
# The first parser read no FPI line in NSE's Sep 2022 - Mar 2025 format; rows it wrote for those
# quarters carry no `parser` field and are fetched again.
PARSER = 2
REFETCH = ("2022-09-30", "2025-03-31")
SUSPECT_ZERO_SHARE = 0.5

# XBRL dimension members; the two formats spell them differently. A category's own line carries
# one explicit member; the per-holder detail lines carry a typed member, so they are not counted.
_MEMBERS = {
    "InstitutionsForeignPortfolioInvestorMember": "fpi",
    "InstitutionsForeignPortfolioInvestorCategoryOneMember": "fpi",
    "InstitutionsForeignPortfolioInvestorCategoryTwoMember": "fpi",
    # NSE's own spelling in the Sep 2022 - Mar 2025 filings.
    "InstitutionsForeignPortfolioInvestorCatergoryOneMember": "fpi",
    "InstitutionsForeignPortfolioInvestorCatergoryTwoMember": "fpi",
    "MutualFundsOrUtiMember": "mf",
    "MutualFundsOrUTIMember": "mf",
    "AlternativeInvestmentFundsMember": "other",
    "InsuranceCompaniesMember": "other",
    "ProvidentFundsOrPensionFundsMember": "other",
    "ShareholdingPatternMember": "total",
}


# --------------------------------------------------------------------------- parsing


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_xbrl(text: str | bytes) -> dict[str, float] | None:
    """Stakes in percent of the company's shares, and the number of mutual-fund holders. None
    if the filing has no total share count to divide by."""

    root = ET.fromstring(text)
    context_key: dict[str, str] = {}
    for node in root.iter():
        if _local(node.tag) != "context":
            continue
        members = [m.text or "" for m in node.iter() if _local(m.tag) == "explicitMember"]
        typed = any(_local(m.tag) == "typedMember" for m in node.iter())
        if len(members) == 1 and not typed:
            key = _MEMBERS.get(members[0].split(":")[-1])
            if key:
                context_key[node.get("id") or ""] = key
    shares = {"fpi": 0.0, "mf": 0.0, "other": 0.0, "total": 0.0}
    mf_holders = 0.0
    for node in root:
        key = context_key.get(node.get("contextRef") or "")
        name = _local(node.tag)
        if key is None or name not in ("NumberOfShares", "NumberOfShareholders"):
            continue
        try:
            value = float(node.text or "")
        except ValueError:
            continue
        if name == "NumberOfShares":
            shares[key] += value
        elif key == "mf":
            mf_holders += value
    if shares["total"] <= 0:
        return None
    pct = {k: 100.0 * shares[k] / shares["total"] for k in ("fpi", "mf", "other")}
    return {
        "fpi": pct["fpi"], "mf": pct["mf"], "inst": pct["fpi"] + pct["mf"] + pct["other"],
        "mf_holders": mf_holders,
    }  # fmt: skip


def _parse_day(value: Any, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip().title(), fmt)
    except ValueError:
        return None


def filing_fields(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The fields the study needs from one NSE list row; None if it is not a usable quarter-end
    filing."""

    quarter = _parse_day(row.get("date"), "%d-%b-%Y")
    published = _parse_day(row.get("broadcastDate"), "%d-%b-%Y %H:%M:%S")
    if quarter is None or published is None or not row.get("symbol"):
        return None
    next_day = quarter + timedelta(days=1)
    if next_day.day != 1 or quarter.month not in (3, 6, 9, 12):
        return None
    submitted = _parse_day(row.get("submissionDate"), "%d-%b-%Y")
    return {
        "symbol": str(row["symbol"]).strip(), "quarter": quarter.date().isoformat(),
        "published": published.replace(tzinfo=IST).isoformat(),
        "submitted": submitted.date().isoformat() if submitted else None,
        "revised": str(row.get("revisedStatus") or "").strip().lower() == "revised",
        "record_id": str(row.get("recordId")), "xbrl": str(row.get("xbrl") or ""),
    }  # fmt: skip


# --------------------------------------------------------------------------- signals


def _previous_quarter(quarter: date) -> date:
    return date(quarter.year, quarter.month - ((quarter.month - 1) % 3), 1) - timedelta(days=1)


def _can_signal(row: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    published = datetime.fromisoformat(row["published"])
    if row.get("revised") or not row.get("submitted"):
        return False
    if published.date() - date.fromisoformat(row["submitted"]) > MAX_PUBLISH_DELAY:
        return False
    return datetime.fromisoformat(base["published"]) < published


def _hit(rule: str, now: Mapping[str, float], base: Mapping[str, float],
         older: Mapping[str, float] | None) -> bool:  # fmt: skip
    fpi, mf, inst = (now[k] - base[k] for k in ("fpi", "mf", "inst"))
    if rule == "fpi_up":
        return fpi >= BIG_MOVE
    if rule == "mf_up":
        return mf >= BIG_MOVE
    if rule == "both_up":
        return fpi >= SMALL_MOVE and mf >= SMALL_MOVE
    if rule == "two_quarters":
        if older is None:
            return False
        before = base["inst"] - older["inst"]
        return inst > 0 and before > 0 and inst + before >= BIG_MOVE
    if rule == "discovery":
        return base["inst"] < UNDER_OWNED and inst >= BIG_MOVE
    if rule == "breadth":
        return mf > 0 and now["mf_holders"] - base["mf_holders"] >= MORE_FUNDS
    raise ValueError(rule)


def one_per_quarter(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, date], dict[str, Any]]:
    """(symbol, quarter) -> the filing to use: an original over a revision, then the earliest."""

    out: dict[tuple[str, date], dict[str, Any]] = {}
    ordered = sorted(rows, key=lambda r: (bool(r.get("revised")), r["published"]))
    for row in ordered:
        out.setdefault((row["symbol"], date.fromisoformat(row["quarter"])), dict(row))
    return out


def stake_signals(rows: Iterable[Mapping[str, Any]], rule: str) -> list[tuple[str, datetime]]:
    """(symbol, the minute the market could know) for every filing that meets the rule."""

    table = one_per_quarter(rows)
    out: list[tuple[str, datetime]] = []
    for (symbol, quarter), now in sorted(table.items()):
        base_q = _previous_quarter(quarter)
        base = table.get((symbol, base_q))
        if base is None or not _can_signal(now, base):
            continue
        older = table.get((symbol, _previous_quarter(base_q)))
        if _hit(rule, now, base, older):
            out.append((symbol, datetime.fromisoformat(now["published"])))
    return out


# --------------------------------------------------------------------------- measuring


class Peers:
    """Average forward return of same-universe stocks that had already risen about as much.

    Used so that a rising stake must beat the prior strength it comes with. Gross of costs, so
    the comparison is against a stock held for free: harder for the rule, on purpose."""

    def __init__(self, p: nl.Panels, turnover: pd.DataFrame) -> None:
        self.p = p
        self.turnover = turnover
        self.prior = p.close / p.close.shift(PEER_LOOKBACK) - 1
        self._cache: dict[tuple[int, int, float, float], pd.Series] = {}

    def table(self, i: int, hold: int, low: float, high: float) -> pd.Series:
        key = (i, hold, low, high)
        if key not in self._cache:
            usual, close = self.turnover.iloc[i], self.p.close.iloc[i]
            prior = self.prior.iloc[i].where((usual >= low) & (usual < high) & (close >= MIN_PRICE))
            forward = self.p.close.iloc[i + hold] / self.p.open.iloc[i + 1] - 1
            frame = pd.DataFrame({"prior": prior, "forward": forward})
            frame = frame.replace([math.inf, -math.inf], math.nan).dropna()
            if len(frame) < PEER_BUCKETS:
                self._cache[key] = pd.Series(dtype=float)
            else:
                bucket = pd.qcut(frame["prior"].rank(method="first"), PEER_BUCKETS, labels=False)
                self._cache[key] = frame["forward"].groupby(bucket).transform("mean")
        return self._cache[key]

    def excess(self, code: str, i: int, hold: int, low: float, high: float, net: float) -> float:
        table = self.table(i, hold, low, high)
        return net - float(table[code]) if code in table.index else math.nan


def judge(calls: pd.DataFrame) -> dict[str, Any]:
    """new_listings' bars 1-4 (same 100 calls and t >= 3), plus bars 5 and 6."""

    out = nl.judge(calls)
    if calls.empty:
        return out
    fails = list(out["fails"])
    peers = calls.dropna(subset=["peer"])
    if peers.empty:
        mean_peer, t_peer = math.nan, math.nan
    else:
        mean_peer, t_peer, _ = nl.week_t(peers.assign(excess=peers["peer"]))
    out["mean_vs_peers_week"], out["t_vs_peers"] = mean_peer, t_peer
    if not (mean_peer > 0 and t_peer >= PASS["min_peer_t"]):
        fails.append(f"vs equally strong stocks t {t_peer:.2f} < {PASS['min_peer_t']}")
    by_quarter = calls.groupby(pd.to_datetime(calls["day"]).dt.to_period("Q"))["excess"]
    counts = by_quarter.count()
    means = by_quarter.mean()[counts >= MIN_QUARTER_CALLS]
    share = float((means > 0).mean()) if len(means) else 0.0
    out["quarters"], out["quarters_positive_share"] = len(means), share
    if share < PASS["min_quarter_share"]:
        fails.append(f"positive in {share:.0%} of quarters < {PASS['min_quarter_share']:.0%}")
    out["fails"] = fails
    out["passed"] = not fails
    return out


# --------------------------------------------------------------------------- data


def _months(start: date, end: date) -> Iterator[tuple[date, date]]:
    first = start.replace(day=1)
    while first <= end:
        following = (first + timedelta(days=32)).replace(day=1)
        yield first, min(following - timedelta(days=1), end)
        first = following


def fetch_index(start: date, end: date, *, pause_seconds: float = 1.5) -> int:
    """Save NSE's list of filings one month per request; a finished saved month is kept."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    with httpx.Client(headers=_NSE_HEADERS, timeout=120, follow_redirects=True) as client:
        client.get("https://www.nseindia.com/")  # sets the cookies the API requires
        for first, last in _months(start, end):
            path = INDEX_DIR / f"{first:%Y-%m}.json"
            if path.exists() and last < end:
                continue
            response = client.get(NSE_SHP_URL, params={
                "index": "equities", "from_date": first.strftime("%d-%m-%Y"),
                "to_date": last.strftime("%d-%m-%Y"),
            })  # fmt: skip
            response.raise_for_status()
            rows = response.json()
            rows = rows.get("data", []) if isinstance(rows, dict) else rows
            path.write_text(json.dumps(rows), encoding="utf-8")
            saved += len(rows)
            time_module.sleep(pause_seconds)
    return saved


def read_index(root: Path = INDEX_DIR) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            row = filing_fields(raw)
            if row and row["record_id"] not in seen:
                seen.add(row["record_id"])
                out.append(row)
    return out


def read_stakes(path: Path = STAKES) -> list[dict[str, Any]]:
    """Every parsed filing, last write of a record winning; failed records are left out."""

    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["record_id"]] = row
    return [r for r in latest.values() if "fpi" in r]


def tradable_symbols(p: nl.Panels, codes: Mapping[str, str]) -> set[str]:
    """Stocks that reached the small universe's turnover floor at some point of the study."""

    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    since = [d for d in turnover.index if d >= FIRST_MONTH]
    reached = turnover.loc[since].max() >= min(low for low, _, _ in UNIVERSES.values())
    return {sym for sym, code in codes.items() if code in reached.index and reached[code]}


def fetch_stakes(
    symbols: set[str], *, pause_seconds: float = 0.5, max_errors_in_a_row: int = 25
) -> dict[str, int]:
    """Download and parse each filing's XBRL, keeping only the parsed stakes. Resumable."""

    import httpx

    from tradedesk.data.results_calendar import _NSE_HEADERS

    STORE.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if STAKES.exists():
        for line in STAKES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                stale = REFETCH[0] <= row["quarter"] <= REFETCH[1] and "parser" not in row
                if not stale:
                    done.add(row["record_id"])
    pending = [
        r for r in read_index()
        if r["symbol"] in symbols and r["record_id"] not in done and r["xbrl"].endswith(".xml")
    ]  # fmt: skip
    print(f"{len(pending)} filings to fetch, {len(done)} already done", flush=True)
    counts = {"parsed": 0, "unusable": 0}
    errors_in_a_row = 0
    with (
        httpx.Client(headers=_NSE_HEADERS, timeout=60, follow_redirects=True) as client,
        STAKES.open("a", encoding="utf-8") as out,
    ):
        for n, row in enumerate(pending, 1):
            try:
                response = client.get(row["xbrl"])
                if response.status_code == 404:  # that filing has no file; the rest may well
                    counts["unusable"] += 1
                    missing = {k: v for k, v in row.items() if k != "xbrl"}
                    out.write(json.dumps(missing | {"parser": PARSER, "error": "404"}) + "\n")
                    out.flush()
                    errors_in_a_row = 0
                    time_module.sleep(pause_seconds)
                    continue
                response.raise_for_status()
            except httpx.HTTPError as exc:
                errors_in_a_row += 1
                if errors_in_a_row >= max_errors_in_a_row:
                    raise RuntimeError(f"{errors_in_a_row} errors in a row, last: {exc}") from exc
                time_module.sleep(min(60.0, pause_seconds * 2**errors_in_a_row))
                continue
            errors_in_a_row = 0
            try:
                stakes = parse_xbrl(response.content)
            except ET.ParseError:
                stakes = None
            record = {k: v for k, v in row.items() if k != "xbrl"} | {"parser": PARSER}
            if stakes is None:
                counts["unusable"] += 1
                record["error"] = "no total share count or bad XML"
            else:
                counts["parsed"] += 1
                record |= stakes
            out.write(json.dumps(record) + "\n")
            out.flush()
            if n % 500 == 0:
                print(f"{n}/{len(pending)} {counts}", flush=True)
            time_module.sleep(pause_seconds)
    return counts


def suspect_quarters(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Quarters where most filings show no FPI line: a format the parser cannot read, which
    would turn the next real figures into huge false rises. A study must not be trusted on them."""

    total: dict[str, int] = {}
    zero: dict[str, int] = {}
    for r in rows:
        total[r["quarter"]] = total.get(r["quarter"], 0) + 1
        zero[r["quarter"]] = zero.get(r["quarter"], 0) + (r["fpi"] == 0)
    return {
        q: zero[q] / n for q, n in sorted(total.items())
        if n >= 50 and zero[q] / n > SUSPECT_ZERO_SHARE
    }  # fmt: skip


# --------------------------------------------------------------------------- study


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "command", nargs="?", default="study", choices=("fetch-index", "fetch-stakes", "study")
    )
    args = parser.parse_args(argv)
    if args.command == "fetch-index":
        print("saved", fetch_index(FIRST_MONTH, datetime.now(IST).date()), "rows to", INDEX_DIR)
        return
    p = nl.load()
    codes = es.symbols()
    if args.command == "fetch-stakes":
        print(fetch_stakes(tradable_symbols(p, codes)))
        return

    sessions = list(p.close.index)
    mkt = nl.market(p)
    cost = nl.cost_function()
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    peers = Peers(p, turnover)
    stakes = read_stakes()
    suspect = suspect_quarters(stakes)
    if suspect:
        print("WARNING - parser may have missed a format, results not valid:", suspect, flush=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS,
        "filings_listed": len(read_index()), "filings_parsed": len(stakes),
        "suspect_quarters": suspect,
        "variants": {},
    }  # fmt: skip
    for rule in RULES:
        found = []
        for symbol, at in stake_signals(stakes, rule):
            code, i = codes.get(symbol), es.signal_row(sessions, at)
            if code in p.close.columns and i is not None:
                found.append((code, i))
        for uni, (low, high, slip) in UNIVERSES.items():
            kept = [
                (code, i) for code, i in found
                if math.isfinite(usual := turnover[code].iat[i]) and low <= usual < high
                and p.close[code].iat[i] >= MIN_PRICE
            ]  # fmt: skip
            for hold in nl.HOLDS:
                rows = []
                for code, i in kept:
                    call = ib.outcome(p, mkt, code, i, hold, cost, slip)
                    if call:
                        call["peer"] = peers.excess(code, i, hold, low, high, call["net"])
                        rows.append(call)
                calls = pd.DataFrame(rows, columns=["code", "day", "net", "excess", "peer"])
                key = f"{rule}_{uni}_{hold}"
                report["variants"][key] = {"signals": len(found), **judge(calls)}
                print(key, json.dumps(report["variants"][key], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
