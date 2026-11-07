"""The owner's one number (2026-11-07): is the agent earning? Rupees after charges against simply
holding the Nifty over the same days, for the two candidates judged in the next few months plus
the Friday condor, written nightly to data/reports/money_board.json for the Scoreboard tab.

It only reads the paper records; it never alerts, never changes a rule and never places an order.

Drift is scored as the book the owner could actually run: Rs 1 lakh in ten Rs 10,000 slots, the
calls in order of entry, skipping calls marked "skip with real money" and calls that arrive when
every slot is full. Open positions count at the latest close, after the round-trip charges they
would pay. A slot still counts as held on the day it exits, because a new entry buys at the open
and the exit comes later that session.

Option figures are per lot. A straddle's Rs 1 lakh figure uses Rs 3.6 lakh of margin, the ceiling
from the broker's single-leg quotes, because the true combined margin is not confirmed yet; broker
sites suggest Rs 1.5-2.3 lakh, so this may understate it. The condor's margin has no such ceiling,
so it gets no Rs 1 lakh figure.

    python scripts/money_board.py
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk import earnings_drift as ed
from tradedesk.broker.indstocks.models import IST

BOARD = Path("data/reports/money_board.json")
OPTION_LEDGER = Path("data/m14_m18/option_income/ledger.jsonl")
CAPITAL = 100_000.0
SLOTS = 10
STRADDLE_MARGIN = 360_000.0
DRIFT_CALLS_NEEDED = 92  # earnings_drift.scorecard's closed_calls_for_proof at the live rule
STRADDLE_SESSIONS_NEEDED = 60
CONDOR_WEEKS_NEEDED = 12
STRADDLES = {"straddle_30": "Straddle, 30% stop", "straddle_40": "Straddle, 40% stop"}


def nifty_move(nifty: pd.Series | None, start: date, end: date) -> float | None:
    """The Nifty from the last close before `start` to the last close on or before `end`."""

    if nifty is None or nifty.empty:
        return None
    before = nifty[[d < start for d in nifty.index]]
    until = nifty[[d <= end for d in nifty.index]]
    if before.empty or until.empty:
        return None
    return float(until.iloc[-1]) / float(before.iloc[-1]) - 1


def drift_book(
    calls: Iterable[Mapping[str, Any]], latest: Mapping[str, float]
) -> dict[str, Any]:
    """The ten-slot book the calls would have made, in entry order (see the module docstring)."""

    entered = [c for c in calls if "entry_price" in c and "unfillable_at" not in c]
    entered.sort(key=lambda c: str(c["entry_on"]))
    taken: list[Mapping[str, Any]] = []
    marked = full = 0
    for call in entered:
        if ed.skip_real_money(call):
            marked += 1
            continue
        day = str(call["entry_on"])
        busy = sum(1 for t in taken if "exit_price" not in t or str(t["exit_on"]) >= day)
        if busy >= SLOTS:
            full += 1
            continue
        taken.append(call)
    closed = [c for c in taken if "exit_price" in c]
    results = [float(c["net_inr"]) for c in closed]
    open_inr, unpriced = 0.0, 0
    for call in taken:
        if "exit_price" in call:
            continue
        price = latest.get(call["scrip_code"])
        if price is None or not math.isfinite(price) or price <= 0:
            unpriced += 1
            continue
        entry = float(call["entry_price"])
        net = price / entry - 1 - ed.HISTORY["round_trip"]
        inr = net * entry * float(call["qty"])
        open_inr += inr
        results.append(inr)
    return {
        "taken": len(taken),
        "skipped_marked": marked,
        "skipped_full": full,
        "closed": len(closed),
        "won": sum(1 for c in closed if float(c["net_inr"]) > 0),
        "open": len(taken) - len(closed),
        "unpriced": unpriced,
        "closed_inr": sum(float(c["net_inr"]) for c in closed),
        "open_inr": open_inr,
        "results": results,
        "start": str(taken[0]["entry_on"]) if taken else None,
    }


def _verdict(done: int, needed: int, ahead: bool | None, unit: str = "closed") -> str:
    if done < needed:
        return f"Too early: {done} of {needed} {unit}"
    if ahead is None:
        return f"{done} {unit}: no Nifty comparison yet"
    return f"{done} {unit}: {'ahead of' if ahead else 'behind'} the Nifty"


def _row(
    name: str,
    closed: int,
    won: int,
    open_count: int,
    rupees: float,
    capital: float | None,
    nifty: float | None,
    needed: int,
    results: Sequence[float],
    note: str,
) -> dict[str, Any]:
    per_lakh = rupees / capital * CAPITAL if capital else None
    nifty_per_lakh = nifty * CAPITAL if nifty is not None else None
    ahead = (
        per_lakh > nifty_per_lakh if per_lakh is not None and nifty_per_lakh is not None else None
    )
    return {
        "name": name,
        "closed": closed,
        "won": won,
        "open": open_count,
        "rupees": rupees,
        "per_lakh": per_lakh,
        "nifty_per_lakh": nifty_per_lakh,
        "ahead": ahead,
        "profit_inr": sum(x for x in results if x > 0),  # the owner's bar: this beats the loss
        "loss_inr": sum(x for x in results if x < 0),
        "worst_inr": min(results) if results else None,
        "verdict": _verdict(closed, needed, ahead),
        "note": note,
    }


def option_row(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    capital: float | None,
    nifty: pd.Series | None,
    needed: int,
    note: str,
) -> dict[str, Any]:
    nets = [float(r["net_inr"]) for r in rows]
    move = None
    if rows:
        start = date.fromisoformat(str(rows[0]["opened_at"])[:10])
        move = nifty_move(nifty, start, date.fromisoformat(str(rows[-1]["closed_on"])[:10]))
    return _row(
        name, len(nets), sum(1 for n in nets if n > 0), 0, sum(nets), capital, move, needed, nets,
        note,
    )  # fmt: skip


def build(
    ledger: Sequence[Mapping[str, Any]],
    option_rows: Sequence[Mapping[str, Any]],
    latest: Mapping[str, float],
    nifty: pd.Series | None,
    as_of: date,
    now: datetime,
) -> dict[str, Any]:
    calls = ed.positions(ledger).values()
    book = drift_book(calls, latest)
    start =date.fromisoformat(book["start"]) if book["start"] else None
    note = (
        f"Rs 1 lakh in {SLOTS} slots: {book['taken']} taken, {book['skipped_full']} skipped with "
        f"every slot full, {book['skipped_marked']} marked skip. Includes "
        f"Rs {book['open_inr']:+,.0f} on open trades at the latest close."
    )
    if book["unpriced"]:
        note += f" {book['unpriced']} open trades had no price and count as zero."
    out = [
        _row(
            "Drift", book["closed"], book["won"], book["open"],
            book["closed_inr"] + book["open_inr"], CAPITAL,
            nifty_move(nifty, start, as_of) if start else None, DRIFT_CALLS_NEEDED,
            book["results"], note,
        )  # fmt: skip
    ]
    # The proof counts every paper call; the slots only limit what Rs 1 lakh could hold.
    paper_closed = sum(1 for c in calls if "exit_price" in c)
    out[0]["verdict"] = _verdict(
        paper_closed, DRIFT_CALLS_NEEDED, out[0]["ahead"], "paper calls closed"
    )
    margin_note = "Per lot. Rs 1 lakh figure assumes Rs 3.6 lakh margin, the unconfirmed ceiling."
    for rule, name in STRADDLES.items():
        mine = [r for r in option_rows if r.get("rule") == rule]
        out.append(
            option_row(name, mine, STRADDLE_MARGIN, nifty, STRADDLE_SESSIONS_NEEDED, margin_note)
        )
    condors = [r for r in option_rows if r.get("rule") == "condor"]
    out.append(
        option_row(
            "Friday condor", condors, None, nifty, CONDOR_WEEKS_NEEDED,
            "Per lot. Margin not known, so no Rs 1 lakh figure.",
        )  # fmt: skip
    )
    return {"written_at": now.isoformat(), "as_of": as_of.isoformat(), "rows": out}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def run(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(IST)
    panels = ed.load(now=now)
    if not panels.sessions:
        raise RuntimeError("no price sessions loaded, the money board was not updated")
    latest = {str(k): float(v) for k, v in panels.close.ffill().iloc[-1].items()}
    board = build(
        ed.read_ledger(), _read_rows(OPTION_LEDGER), latest, panels.nifty, panels.sessions[-1], now
    )
    BOARD.parent.mkdir(parents=True, exist_ok=True)
    scratch = BOARD.with_suffix(".tmp")
    scratch.write_text(json.dumps(board, indent=1), encoding="utf-8")
    scratch.replace(BOARD)
    return board
