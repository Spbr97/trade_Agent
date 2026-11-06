"""Forward paper trial: buy an IPO at its listing open when it opens 10-30% over the issue price.

ipo_listing.py found this band keeps rising (20 sessions +7.80% over the market, t 3.55) and
ipo_checks.py could not break it, but the same history cannot prove what it suggested. Only listings
no test has seen can. Research only: it never alerts, sizes or trades. A pass is a proposal.

Rule, unchanged: a mainboard IPO on NSE, listing open 10-30% over the issue price, bought at
that open with 0.05% slippage, sold at the 20th close with 0.05% slippage, a Rs 10,000 trade's
delivery charges. Excess = net minus the equal-weight liquid market over the same span.

PASS BAR (written 2026-11-07, before the first forward listing, FROM 2026-11-09). Judged at 30
closed calls and on every later run: mean net > 0; mean excess > 0, t >= 2.0, one listing WEEK as one
unit; positive excess in both halves (split at the median listing date). Under 30 closed:
"collecting". At 30 or more and not all met: "failing", which can recover. About 25-30 such listings
a year at the recent pace, so the first judgement is about a year away.

    python scripts/ipo_forward.py
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import convergence_forward as cf
from tradedesk_lab import event_studies as es
from tradedesk_lab import ipo_listing as il
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT

FROM = date(2026, 11, 9)
BAND = (0.10, 0.30)
HOLD = 20
OUT_DIR = OUTPUT / "ipo_listing"
LEDGER = OUT_DIR / "forward.jsonl"
VERDICT = OUT_DIR / "forward.json"


def new_rows(
    ipos: Iterable[Mapping[str, Any]], known: Mapping[str, Mapping[str, Any]], p: nl.Panels,
    codes: Mapping[str, str], cost: Any, market: Any, now: datetime, *, start: date = FROM,
) -> list[dict[str, Any]]:  # fmt: skip
    """Ledger rows for new calls in the band and for held calls reaching their 20th close."""

    sessions = list(p.close.index)
    index = {d: i for i, d in enumerate(sessions)}
    stamp = now.isoformat()
    out: list[dict[str, Any]] = []
    held = {k: dict(v) for k, v in known.items()}
    for ipo in ipos:
        symbol, listed = ipo["symbol"], ipo["listed"]
        code = codes.get(symbol)
        if listed < start or symbol in held or code not in p.open.columns or listed not in index:
            continue
        opening = float(p.open[code].iat[index[listed]])
        if not (math.isfinite(opening) and opening > 0):
            continue
        gain = opening / ipo["issue"] - 1
        if BAND[0] <= gain < BAND[1]:
            call = {
                "event": "call", "key": symbol, "code": code, "entry_on": str(listed),
                "issue": ipo["issue"], "gain": gain, "entry": opening * (1 + il.SLIPPAGE),
                "recorded_at": stamp,
            }  # fmt: skip
            out.append(call)
            held[symbol] = dict(call)
    for key, call in held.items():
        entry_on = date.fromisoformat(call["entry_on"])
        if "exit_on" in call or entry_on not in index:
            continue
        e = index[entry_on]
        x = e + HOLD - 1
        if x >= len(sessions):
            continue
        close = float(p.close[call["code"]].iat[x])
        if not math.isfinite(close):
            continue
        entry = float(call["entry"])
        sold = close * (1 - il.SLIPPAGE)
        net = sold / entry - 1 - cost(entry, sold)
        out.append({
            "event": "closed", "key": key, "exit_on": str(sessions[x]), "exit": sold, "net": net,
            "excess": net - market(e, x), "recorded_at": stamp,
        })  # fmt: skip
    return out


def read_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run(*, now: datetime | None = None) -> dict[str, Any]:
    """Nightly: read the issue list, record new calls and closes, judge the record."""

    now = now or datetime.now(IST)
    il.fetch()
    p = nl.load()
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)

    def market(e: int, x: int) -> float:
        move = p.close.iloc[x] / p.open.iloc[e] - 1
        return float(move[(turnover.iloc[e] >= nl.MIN_TURNOVER) & move.notna()].mean())

    raw = json.loads(il.STORE.read_text(encoding="utf-8"))
    ipos, _ = il.clean(raw, {})  # a future split cannot be known; a forward record is unadjusted
    ledger = read_ledger()
    new = new_rows(ipos, cf.fold(ledger), p, es.symbols(), nl.cost_function(), market, now)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if new:
        with LEDGER.open("a", encoding="utf-8") as fh:
            for row in new:
                fh.write(json.dumps(row) + "\n")
    calls = cf.fold([*ledger, *new])
    verdict = {
        "generated_at": now.isoformat(), "from": str(FROM), "new_rows": len(new), **cf.judge(calls),
        "by_amount": cf.by_amount(calls, _cost_model()),
        "open_calls": [k for k, c in calls.items() if "exit_on" not in c],
    }  # fmt: skip
    VERDICT.write_text(json.dumps(verdict, indent=2, default=str), encoding="utf-8")
    print(json.dumps(verdict, default=str))
    return verdict


def _cost_model() -> Any:
    from tradedesk_lab import amount_test as am

    return am.cost_model()
