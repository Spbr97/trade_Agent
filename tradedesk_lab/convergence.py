"""Convergence: when separate kinds of evidence agree on a stock, is a 60-session hold better?

Three separate studies nearly passed the same way: a domestic fund's bulk buy (+2.53% over the
market, t 2.08), foreign and mutual funds both raising their stake (+3.78%, t 1.99) and strong
results gapping up 3%+ (+2.01%, t 1.68), each in liquid stocks over 60 sessions. Each could be
luck, the best of many tries. But they come from different data, so if something real sits behind
them, a stock showing two at once should do clearly better than one showing either alone.

HONEST LIMIT: these three were picked AFTER seeing those results, on the same history, so this can
only partly test them. It can kill the idea. A pass cannot prove it, and earns a forward trial at
most.

Events, each dated by when the market could know it, using the three studies' own code:
  fund_bulk    bulk_block_deals: a named domestic fund bought with no sale that day. Next open.
  both_up      institutional_stakes: FPI and mutual funds each up 0.25+ points. First close after
               publication, then the next open.
  loud_strong  results_reading: strong results with the open 3%+ above the last close. That open.
A call is made at the entry of the event that brings a stock to the number needed, counting
distinct kinds within the window before it, once per stock per 60 sessions. Liquid stocks only
(20-session traded value >= Rs 5 crore, price >= Rs 20), 0.05% slippage each way, a Rs 10,000
trade's delivery charges, sold at the 60th close. Excess = net minus the equal-weight liquid
market, same span.

Variants: two_in_20 (2+ kinds within 20 sessions), two_in_60 (2+ within 60), all_three (3 within 60).

PASS BAR (written 2026-11-07, before any combination was run). A variant passes only if:
  1. at least 100 calls;
  2. mean net return per call > 0 after charges;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit;
  4. positive mean excess in both halves (split at the median signal date);
  5. it beats stocks that had already risen as much: net minus the average same-universe stock in
     the same quintile of the prior 60 sessions' return over the same span, mean > 0, t >= 2.0;
  6. positive mean excess in at least 60% of calendar quarters holding 5 or more calls.
Also reported, not part of the bar: the mean excess of each kind alone over the same dates, because
if agreeing evidence adds nothing, a combination should not beat its parts.

Caveats: both_up exists only from 2022, so most calls are from then, nearly all a rising market. The
price store holds stocks listed today, which flatters long tests.

    python -m tradedesk_lab.convergence
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import bulk_block_deals as bb
from tradedesk_lab import event_studies as es
from tradedesk_lab import institutional_stakes as st
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reading as rr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "convergence"
HOLD = 60
VARIANTS = {"two_in_20": (2, 20), "two_in_60": (2, 60), "all_three": (3, 60)}
KINDS = ("fund_bulk", "both_up", "loud_strong")


def convergent(
    events: Iterable[tuple[str, int, str]], need: int, window: int, gap: int = HOLD
) -> list[tuple[str, int]]:
    """(code, entry row) when an event brings a stock to `need` distinct kinds within `window`
    sessions, counting only events at or before that entry, one call per stock per `gap`."""

    by_code: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for code, e, kind in events:
        by_code[code].append((e, kind))
    out = []
    for code, rows in by_code.items():
        rows.sort()
        last = -(10**9)
        for e, _ in rows:
            kinds = {k for when, k in rows if e - window < when <= e}
            if len(kinds) >= need and e - last >= gap:
                out.append((code, e))
                last = e
    return sorted(out, key=lambda x: (x[1], x[0]))


def liquid_at(measure: rr.Measure, code: str, e: int) -> bool:
    """Liquid before the entry, on the study's own turnover and price floors."""

    if e < 1 or code not in measure.column:
        return False
    j = measure.column[code]
    usual, before = float(measure.turnover.iat[e, j]), float(measure.closes[e - 1, j])
    return math.isfinite(usual) and usual >= rr.MIN_TURNOVER and before >= rr.MIN_PRICE


def fund_bulk_events(
    rows: list[Mapping[str, Any]], sessions: list[date], codes: Mapping[str, str]
) -> list[tuple[str, int, str]]:
    return [
        (codes[symbol], i + 1, "fund_bulk")
        for symbol, i in bb.signals_for(rows, "fund_bulk", sessions)
        if symbol in codes and i + 1 < len(sessions)
    ]


def both_up_events(
    stakes: list[dict[str, Any]], sessions: list[date], codes: Mapping[str, str]
) -> list[tuple[str, int, str]]:
    out = []
    for symbol, at in st.stake_signals(stakes, "both_up"):
        i = es.signal_row(sessions, at)
        if symbol in codes and i is not None and i + 1 < len(sessions):
            out.append((codes[symbol], i + 1, "both_up"))
    return out


def loud_strong_events(
    table: Mapping[tuple[str, date], Mapping[str, Any]], measure: rr.Measure,
    sessions: list[date], codes: Mapping[str, str],
) -> list[tuple[str, int, str]]:  # fmt: skip
    out = []
    for symbol, quarter in sorted(table):
        code = codes.get(symbol)
        if code not in measure.column:
            continue
        numbers = rr.classify(table, symbol, quarter)
        if numbers is None or not numbers["strong"]:
            continue
        e = rr.entry_session(sessions, numbers["published"])
        if e is None or e < 1:
            continue
        j = measure.column[code]
        opening, before = float(measure.opens[e, j]), float(measure.closes[e - 1, j])
        if math.isfinite(opening) and math.isfinite(before) and before > 0:
            if "loud_strong" in rr.rules_hit(numbers, opening / before - 1):
                out.append((code, e, "loud_strong"))
    return out


def calls_for(measure: rr.Measure, picks: Iterable[tuple[str, int]]) -> pd.DataFrame:
    rows = [c for code, e in picks if (c := measure.call(code, e, str(HOLD)))]
    return pd.DataFrame(rows, columns=["code", "day", "net", "excess", "peer", "days"])


def main() -> None:
    p = nl.load()
    sessions = list(p.close.index)
    codes = es.symbols()
    measure = rr.Measure(p)
    events = [
        *fund_bulk_events(bb.read_store(), sessions, codes),
        *both_up_events(st.read_stakes(), sessions, codes),
        *loud_strong_events(rr.one_per_quarter(rr.read_figures()), measure, sessions, codes),
    ]
    events = [x for x in events if liquid_at(measure, x[0], x[1])]
    counts = {k: sum(1 for x in events if x[2] == k) for k in KINDS}
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": st.PASS, "events": counts,
        "variants": {}, "alone": {},
    }  # fmt: skip
    print(json.dumps(counts), flush=True)
    for name, (need, window) in VARIANTS.items():
        calls = calls_for(measure, convergent(events, need, window))
        report["variants"][name] = st.judge(calls)
        print(name, json.dumps(report["variants"][name], default=str), flush=True)
    for kind in KINDS:
        alone = convergent([x for x in events if x[2] == kind], 1, 1)
        report["alone"][kind] = st.judge(calls_for(measure, alone))
        print("alone", kind, json.dumps(report["alone"][kind], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
