"""Forward paper trial of convergence: two kinds of evidence within 60 sessions, held 60 sessions.

tradedesk_lab/convergence.py's two_in_60 passed its bar on history, but its signals were picked
after seeing them do well, so only sessions no test has seen can settle it. Owner approved the
trial on 2026-11-07. Research only: it never alerts, sizes or trades. A pass is a proposal to the
owner.

Rule, unchanged and built from the same functions: a domestic fund's one-sided bulk buy, FPI and
mutual funds each up 0.25+ points in a filing, or strong results opening 3%+ up. A call at the
entry of the event that brings a liquid stock to two kinds within 60 sessions, once per stock per
60 sessions. Events from before FROM count as evidence; only calls entered from FROM on are the
trial's. Bought at that open, sold at the 60th close, 0.05% slippage each way, a Rs 10,000 trade's
charges.

PASS BAR (written 2026-11-07, before the first forward call, FROM 2026-11-09). Judged at 30 closed
calls and on every later run: mean net > 0 after charges; mean excess over the market > 0 with t
>= 2.0, one entry WEEK as one unit; positive excess in both halves (split at the median entry
date). Under 30 closed: "collecting". 30 or more and not all met: "failing", which can recover,
because the bar stays open as calls close. About 33 calls a year and a 60-session hold mean 30
closed calls takes over a year: a slow answer, accepted because no faster honest one exists. The
other amounts are reported beside it and never decide it.

    python scripts/convergence_forward.py
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import bulk_block_deals as bb
from tradedesk_lab import convergence as cv
from tradedesk_lab import drift_runner as dr
from tradedesk_lab import event_studies as es
from tradedesk_lab import institutional_stakes as st
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reading as rr
from tradedesk_lab import results_short_forward as short
from tradedesk_lab.artifacts import OUTPUT

FROM = date(2026, 11, 9)
OUT_DIR = OUTPUT / "convergence"
LEDGER = OUT_DIR / "forward.jsonl"
VERDICT = OUT_DIR / "forward.json"
NEED, WINDOW = cv.VARIANTS["two_in_60"]
HOLD = cv.HOLD
MIN_CLOSED = 30
MIN_T = 2.0
AMOUNTS = (10_000, 25_000, 50_000, 100_000)


def fold(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """key -> the call folded with its close, if it has closed."""

    calls: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["event"] == "call":
            calls[row["key"]] = dict(row)
        elif row["event"] == "closed" and row["key"] in calls:
            calls[row["key"]].update(row)
    return calls


def new_events(
    known: Mapping[str, Mapping[str, Any]], events: list[tuple[str, int, str]],
    measure: Any, now: datetime, *, start: date = FROM,
) -> list[dict[str, Any]]:  # fmt: skip
    """Ledger rows for calls entered from `start` on not yet recorded, and for held calls that have
    reached their 60th close. Rows already written are never changed."""

    sessions = list(measure.p.close.index)
    stamp = now.isoformat()
    out: list[dict[str, Any]] = []
    held = {key: dict(call) for key, call in known.items()}
    for code, e in cv.convergent(events, NEED, WINDOW):
        day = sessions[e]
        key = f"{code}:{day}"
        if day < start or key in held or not cv.liquid_at(measure, code, e):
            continue
        opening = float(measure.opens[e, measure.column[code]])
        if not opening > 0:  # also refuses NaN: that session has no price yet, so try again
            continue
        kinds = sorted({k for c, when, k in events if c == code and e - WINDOW < when <= e})
        call = {
            "event": "call", "key": key, "code": code, "entry_on": str(day),
            "entry": opening * (1 + rr.SLIPPAGE), "kinds": kinds, "recorded_at": stamp,
        }  # fmt: skip
        out.append(call)
        held[key] = dict(call)
    index = {str(d): i for i, d in enumerate(sessions)}
    for key, call in held.items():
        if "exit_on" in call or call["entry_on"] not in index:
            continue
        result = measure.call(call["code"], index[call["entry_on"]], str(HOLD))
        if result is None:
            continue
        x = index[call["entry_on"]] + int(result["days"]) - 1
        close = float(measure.closes[x, measure.column[call["code"]]])
        out.append({
            "event": "closed", "key": key, "exit_on": str(sessions[x]),
            "exit": close * (1 - rr.SLIPPAGE), "net": result["net"], "excess": result["excess"],
            "peer": result["peer"], "recorded_at": stamp,
        })  # fmt: skip
    return out


def judge(calls: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The forward bar, on closed calls."""

    closed = [c for c in calls.values() if "exit_on" in c]
    out: dict[str, Any] = {"calls": len(calls), "open": len(calls) - len(closed),
                           "closed": len(closed), "status": "collecting"}  # fmt: skip
    if not closed:
        return out
    frame = pd.DataFrame(
        {"day": [date.fromisoformat(c["entry_on"]) for c in closed],
         "net": [float(c["net"]) for c in closed], "excess": [float(c["excess"]) for c in closed]}
    )  # fmt: skip
    mean, t, weeks = nl.week_t(frame)
    days = sorted(frame["day"])
    cut = days[len(days) // 2]
    halves = [nl.week_t(frame[frame["day"] < cut])[0], nl.week_t(frame[frame["day"] >= cut])[0]]
    out |= {
        "mean_net": float(frame["net"].mean()), "win_rate": float((frame["net"] > 0).mean()),
        "mean_excess_week": mean, "t_excess": t, "weeks": weeks, "halves_excess": halves,
    }  # fmt: skip
    if len(closed) >= MIN_CLOSED:
        good = out["mean_net"] > 0 and mean > 0 and t >= MIN_T and all(h > 0 for h in halves)
        out["status"] = "passed" if good else "failing"
    return out


def by_amount(calls: Mapping[str, Mapping[str, Any]], cm: Any) -> dict[str, dict[str, float]]:
    """Closed calls' rupees at each amount, on that amount's own charges."""

    closed = [c for c in calls.values() if "exit_on" in c]
    table = {}
    for amount in AMOUNTS:
        rupees = []
        for c in closed:
            entry, exit_px = float(c["entry"]), float(c["exit"])
            net = exit_px / entry - 1 - dr._delivery(cm, entry, exit_px, float(amount))
            rupees.append(net * amount)
        table[str(amount)] = {
            "mean_rupees": sum(rupees) / len(rupees) if rupees else 0.0,
            "total_rupees": sum(rupees),
        }  # fmt: skip
    return table


def build_events(measure: rr.Measure, codes: Mapping[str, str]) -> list[tuple[str, int, str]]:
    sessions = list(measure.p.close.index)
    events = [
        *cv.fund_bulk_events(bb.read_store(), sessions, codes),
        *cv.both_up_events(st.read_stakes(), sessions, codes),
        *cv.loud_strong_events(rr.one_per_quarter(rr.read_figures()), measure, sessions, codes),
    ]
    return [x for x in events if cv.liquid_at(measure, x[0], x[1])]


def read_ledger(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run(*, now: datetime | None = None) -> dict[str, Any]:
    """Nightly: read the three sources, record new calls and closes, judge the record."""

    now = now or datetime.now(IST)
    today = now.date()
    panels = nl.load()
    codes = es.symbols()
    bb.fetch(today)
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    st.fetch_index(last_month, today, refresh=True)
    st.fetch_stakes(st.tradable_symbols(panels, codes))
    short.refresh_index(today)
    rr.fetch_figures(rr.liquid_symbols(panels, codes), since=FROM - timedelta(days=7))
    measure = rr.Measure(panels)
    ledger = read_ledger()
    new = new_events(fold(ledger), build_events(measure, codes), measure, now)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if new:
        with LEDGER.open("a", encoding="utf-8") as fh:
            for row in new:
                fh.write(json.dumps(row) + "\n")
    calls = fold([*ledger, *new])
    verdict = {
        "generated_at": now.isoformat(), "from": str(FROM), "new_rows": len(new),
        **judge(calls), "by_amount": by_amount(calls, measure.cm),
        "open_calls": [c["key"] for c in calls.values() if "exit_on" not in c],
    }  # fmt: skip
    VERDICT.write_text(json.dumps(verdict, indent=2, default=str), encoding="utf-8")
    print(json.dumps(verdict, default=str))
    return verdict
