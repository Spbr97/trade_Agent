"""Index changes: index funds MUST buy an added stock and sell a dropped one on a known date.

First principles (owner, 2026-10-31: aim for a breakthrough): the purest edge is a trader who is
forced to trade whatever the price. When NSE Indices adds a stock to the Nifty 50, Next 50,
Midcap 150 or Nifty 500, every fund tracking that index has to buy it at the close of the day
before the change takes effect - announced weeks ahead in a press release. Elsewhere this
"index effect" pushes added stocks up between the announcement and the change, then partly
reverses; dropped stocks are sold down into the change and recover afterwards.

Data: every NSE Indices press release from 2016 on (niftyindices.com/Press_Release/
ind_prs<DDMMYYYY>.pdf; no index page exists, so `fetch` tries every date), text saved to
data/m14_m18/index_changes/<date>.txt. `parse` reads the equity releases: the announcement
date, the effective date, and per index the symbols "being included" and "being excluded".

Timing: releases come out after the close as a rule and carry no time, so a release is acted
on at the open of the session AFTER its date. Index funds trade at the close of the last session
before the effective date ("pre-effective close").

Tiers (a stock's event is its highest tier in that release):
  large  Nifty 50, Nifty Next 50, Nifty 100;
  mid    Nifty Midcap 150 / Midcap 100 / Midcap 50, Nifty 200;
  broad  Nifty 500, Nifty Smallcap 250 / Smallcap 100.
Trades (long-only, as all overnight positions here):
  add_runup    an added stock, bought the next open after the release, sold at the
               pre-effective close (the funds' buying day);
  add_after    an added stock, bought the open of the effective date, held 20 sessions;
  drop_after   a dropped stock, bought the open of the effective date, held 20 sessions (the
               forced selling is over).
3 tiers x 3 trades = 9 variants. Filters: price >= Rs 20 and 20-session traded value >= Rs 1
crore at the signal; 0.05% slippage each way; real delivery round trip of a Rs 10,000 call.
Excess = net minus the equal-weight liquid market over the same days.

PASS BAR (written 2026-10-31, before any release was downloaded or parsed). A variant passes
only if:
  1. at least 50 calls from at least 12 different releases;
  2. mean net return per call > 0 after costs;
  3. mean excess > 0 with t >= 3.0, one RELEASE as one unit (a review's changes share dates);
  4. positive mean excess in both halves (split at the median release date).
t >= 3 over releases is stricter than Bonferroni for nine. A pass becomes a proposal for FORWARD
paper calls, never a live call. Research only.

Caveat: the price store holds stocks listed today, so stocks that later delisted are missing -
this flatters the long tests (mostly drop_after), so a pass needs to be clear.

    python -m tradedesk_lab.index_changes fetch [--from 2016-01-01]
    python -m tradedesk_lab.index_changes
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time as time_module
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT, ROOT

STORE = ROOT / "data" / "m14_m18" / "index_changes"
OUT_DIR = OUTPUT / "index_changes_study"
URL = "https://niftyindices.com/Press_Release/ind_prs{stamp}.pdf"
SUFFIXES = ("", "_1", "_2", "_3")
TIERS = {
    "large": ("nifty 50", "nifty next 50", "nifty 100"),
    "mid": ("nifty midcap 150", "nifty midcap 100", "nifty midcap 50", "nifty 200"),
    "broad": ("nifty 500", "nifty smallcap 250", "nifty smallcap 100"),
}
TRADES = ("add_runup", "add_after", "drop_after")
HOLD = 20
SLIPPAGE = 0.0005
MIN_PRICE, MIN_TURNOVER = 20.0, 1e7
PASS = {"min_calls": 50, "min_releases": 12, "min_t": 3.0}

_MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
_RELEASED = re.compile(rf"Mumbai,?\s+{_MONTH}\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I)
# Releases before 2024 head the page with a bare date line instead of "Mumbai, <date>".
_DATE_LINE = re.compile(rf"^\s*(?:Date:\s*)?{_MONTH}\s+(\d{{1,2}}),?\s+(\d{{4}})\s*$", re.I | re.M)
_EFFECTIVE = re.compile(rf"effective\s+from\s+{_MONTH}\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I)
_HEADING = re.compile(
    r"^\s*(?:[A-Z]\.|\d+[).]|[ivx]+[).])?\s*(nifty[\w &/-]*?)\s*(?:index)?\s*:?\s*$", re.I
)
_ROW = re.compile(r"^\s*\d+\s+.+?\s+([A-Z][A-Z0-9&-]{1,19})\s*$")


# --------------------------------------------------------------------------- data


def fetch(start: date, end: date, *, store: Path = STORE, pause_seconds: float = 1.0) -> int:
    """Try every date's press releases once; save the text of each that exists (a date with no
    release is remembered in missing.json so a re-run skips it). A date can carry more than one
    release, published as ind_prs<DDMMYYYY>_1.pdf and _2.pdf, so those are tried too - on a busy
    day the equity review is often the second file."""

    import httpx

    sys.path.insert(0, str(OUTPUT / "deps"))
    import pypdf

    store.mkdir(parents=True, exist_ok=True)
    missing_path = store / "missing.json"
    missing = set(json.loads(missing_path.read_text())) if missing_path.exists() else set()
    found = 0
    headers = {"User-Agent": "Mozilla/5.0 (research; tradedesk)", "Accept": "application/pdf"}
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
        day = start
        while day <= end:
            for suffix in SUFFIXES:
                key = day.isoformat() + suffix
                # Extra files only exist on a day that has a release at all.
                if suffix and not (store / f"{day.isoformat()}.txt").exists():
                    break
                if key in missing or (store / f"{key}.txt").exists():
                    continue
                response = client.get(URL.format(stamp=day.strftime("%d%m%Y") + suffix))
                if response.status_code == 200 and response.content[:4] == b"%PDF":
                    reader = pypdf.PdfReader(io.BytesIO(response.content))
                    text = "\n".join(page.extract_text() or "" for page in reader.pages)
                    same = [p for p in store.glob(f"{day.isoformat()}*.txt")
                            if p.read_text(encoding="utf-8") == text]  # fmt: skip
                    if same:  # the same release served under another name
                        missing.add(key)
                    else:
                        (store / f"{key}.txt").write_text(text, encoding="utf-8")
                        found += 1
                elif response.status_code == 404 or response.content[:4] != b"%PDF":
                    missing.add(key)
                else:
                    response.raise_for_status()
                time_module.sleep(pause_seconds)
                if len(missing) % 50 == 0:
                    missing_path.write_text(json.dumps(sorted(missing)))
            day += timedelta(days=1)
    missing_path.write_text(json.dumps(sorted(missing)))
    return found


def _date(match: re.Match[str] | None) -> date | None:
    if match is None:
        return None
    try:
        return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
    except ValueError:
        return None


def _index_name(line: str) -> str | None:
    m = _HEADING.match(line)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    return name if len(name) <= 40 else None


def parse(text: str) -> dict[str, Any] | None:
    """An equity release's dates and, per index, the symbols included and excluded (None: not
    an equity index-change release)."""

    if "(Equity)" not in text.replace("( Equity)", "(Equity)") and "being included" not in text:
        return None
    released = _date(_RELEASED.search(text)) or _date(_DATE_LINE.search(text))
    effective = _date(_EFFECTIVE.search(text))
    if released is None or effective is None:
        return None
    changes: dict[str, dict[str, list[str]]] = {}
    index, mode = None, None
    for line in text.splitlines():
        low = line.lower()
        name = _index_name(line)
        if name is not None:
            index, mode = name, None
            continue
        if "being excluded" in low or "being dropped" in low:
            mode = "excluded"
            continue
        if "being included" in low or "being added" in low:
            mode = "included"
            continue
        row = _ROW.match(line)
        if row and index and mode:
            changes.setdefault(index, {"included": [], "excluded": []})[mode].append(row.group(1))
    if not changes:
        return None
    return {"released": released, "effective": effective, "changes": changes}


def tier_of(index: str) -> str | None:
    for tier, names in TIERS.items():
        if index in names:
            return tier
    return None


def events(releases: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One event per (release, stock, direction): the stock's highest tier in that release."""

    order = {t: n for n, t in enumerate(TIERS)}
    out = []
    for rel in releases:
        best: dict[tuple[str, str], str] = {}
        for index, sides in rel["changes"].items():
            tier = tier_of(index)
            if tier is None:
                continue
            for direction, symbols in (("add", sides["included"]), ("drop", sides["excluded"])):
                for sym in symbols:
                    have = best.get((sym, direction))
                    if have is None or order[tier] < order[have]:
                        best[(sym, direction)] = tier
        out += [{"released": rel["released"], "effective": rel["effective"], "symbol": sym,
                 "direction": direction, "tier": tier}
                for (sym, direction), tier in sorted(best.items())]  # fmt: skip
    return out


def read_releases(store: Path = STORE) -> list[dict[str, Any]]:
    out = []
    for path in sorted(store.glob("*.txt")):
        rel = parse(path.read_text(encoding="utf-8"))
        if rel is not None:
            out.append(rel)
    return out


# --------------------------------------------------------------------------- study


def windows(sessions: list[date], ev: Mapping[str, Any], trade: str) -> tuple[int, int] | None:
    """(entry row bought at its open, exit row sold at its close) for one trade."""

    idx = pd.Index(sessions)
    after_release = int(idx.searchsorted(ev["released"], side="right"))
    effective = int(idx.searchsorted(ev["effective"]))
    if trade == "add_runup":
        entry, exit_row = after_release, effective - 1
    else:
        entry, exit_row = effective, effective + HOLD - 1
    if entry >= len(sessions) or exit_row >= len(sessions) or exit_row < entry:
        return None
    return entry, exit_row


def outcome(p: nl.Panels, mkt: pd.Series, code: str, entry: int, exit_row: int, cost: Any) -> (
    dict[str, float] | None
):  # fmt: skip
    buy = float(p.open[code].iat[entry]) * (1 + SLIPPAGE)
    sell = float(p.close[code].iat[exit_row]) * (1 - SLIPPAGE)
    if not (math.isfinite(buy) and math.isfinite(sell) and buy > 0):
        return None
    net = sell / buy - 1 - cost(buy, sell)
    m = float((1 + mkt.iloc[entry : exit_row + 1].fillna(0.0)).prod() - 1)
    return {"net": net, "excess": net - m}


def judge(calls: pd.DataFrame) -> dict[str, Any]:
    if calls.empty:
        return {"calls": 0, "passed": False, "fails": ["no calls"]}
    per = calls.groupby("released")["excess"].mean()
    n = len(per)
    mean = float(per.mean())
    sd = float(per.std(ddof=1)) if n > 1 else float("nan")
    t = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    cut = sorted(per.index)[n // 2]
    halves = [float(per[per.index < cut].mean()), float(per[per.index >= cut].mean())]
    out = {
        "calls": len(calls), "releases": n, "win_rate": float((calls["net"] > 0).mean()),
        "mean_net": float(calls["net"].mean()), "median_net": float(calls["net"].median()),
        "mean_excess_release": mean, "t_excess": t, "halves_excess": halves,
        "first": str(min(per.index)), "last": str(max(per.index)),
    }  # fmt: skip
    fails = []
    if len(calls) < PASS["min_calls"] or n < PASS["min_releases"]:
        fails.append(f"only {len(calls)} calls from {n} releases")
    if not out["mean_net"] > 0:
        fails.append("loses money after costs")
    if not (mean > 0 and t >= PASS["min_t"]):
        fails.append(f"excess t {t:.2f} < {PASS['min_t']}")
    if not all(h > 0 for h in halves if math.isfinite(h)):
        fails.append("not positive in both halves")
    out["passed"] = not fails
    out["fails"] = fails
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", nargs="?", default="study", choices=("fetch", "study"))
    parser.add_argument("--from", dest="start", default="2016-01-01")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        n = fetch(date.fromisoformat(args.start), datetime.now(IST).date())
        print(f"saved {n} new releases to {STORE}")
        return

    from tradedesk_lab import event_studies as es

    p = nl.load()
    sessions = list(p.close.index)
    mkt = nl.market(p)
    cost = nl.cost_function()
    codes = es.symbols()
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    releases = read_releases()
    evs = events(releases)
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS,
        "releases": len(releases), "events": len(evs),
        "unmatched_symbols": sorted({e["symbol"] for e in evs if codes.get(e["symbol"])
                                     not in p.close.columns})[:50],
        "variants": {},
    }  # fmt: skip
    for tier in TIERS:
        for trade in TRADES:
            direction = "add" if trade.startswith("add") else "drop"
            rows = []
            for ev in evs:
                if ev["tier"] != tier or ev["direction"] != direction:
                    continue
                code = codes.get(ev["symbol"])
                span = windows(sessions, ev, trade)
                if code not in p.close.columns or span is None:
                    continue
                signal = max(span[0] - 1, 0)
                usual, px = turnover[code].iat[signal], p.close[code].iat[signal]
                if not (math.isfinite(usual) and usual >= MIN_TURNOVER and px >= MIN_PRICE):
                    continue
                if (o := outcome(p, mkt, code, *span, cost)) is not None:
                    rows.append({"released": ev["released"], **o})
            calls = pd.DataFrame(rows, columns=["released", "net", "excess"])
            key = f"{trade}_{tier}"
            report["variants"][key] = judge(calls)
            print(key, json.dumps(report["variants"][key], default=str))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
