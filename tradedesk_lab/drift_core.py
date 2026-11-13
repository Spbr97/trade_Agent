"""Drift with the idle cash in the Nifty: does a fully invested Rs 1 lakh beat holding the Nifty?

WHY (2026-11-13). Drift's calls beat the market in every year (drift_capital.py), yet its Rs 1
lakh book trails the Nifty in most years (drift_months.py) and on the money board. A read-only
count the same day showed why: on average only 3.94 of the ten slots are in use, so 61% of the
money sits idle and earns nothing (50-73% by year). The board then compares a mostly-idle book
with money fully in the market. The obvious remedy is old and standard - a core holding of the
index with the active calls carved out of it - but its charges are real: every call means selling
Rs 10,000 of index fund at the open and buying it back when the call exits. Research only; it
places no order and changes no live rule.

BOOK. The live ten-slot drift book exactly as drift_months.book builds it (same calls, marked
calls skipped, a stock already held skipped, a full book skipped, +10% or 20th-close exit, real
0.564% round trip). Each slot not held by a call holds Rs 10,000 of the Nifty instead. A slot
counts as held from its entry session to its exit session inclusive (the book's own convention),
so the index money earns nothing on those two sessions: a small understatement. Each call costs one
extra index round trip, 0.35% of Rs 10,000 (brokerage, the DP charge on the sale, stamp duty,
exchange charges and a bid-ask allowance for a Nifty ETF). No compounding, like drift_months: a
fixed Rs 1 lakh, results summed. The Nifty is the price index, on both sides of the comparison.
Reported beside it, not judged: the same idle cash in a liquid fund at 5% and 6.5% a year.

BAR (written before any figure of this book was computed). The core book passes only if all of:
  1. over the whole period it makes more rupees than Rs 1 lakh held in the Nifty;
  2. it is ahead of the Nifty in at least 7 of 10 full calendar years;
  3. it is ahead of the Nifty in both halves of the period (split at the middle session);
  4. its worst fall from a peak is no more than 1.25 times the Nifty's over the same period.
A pass is a proposal to the owner for how the Rs 1 lakh is held, never an order. Nothing is tuned
after the run: no other switch charge, slot count or index is tried.

RESULT (2026-11-13, 639 book calls, entries 2016-10-21 to 2026-11-13, 3.95 of 10 slots held on
average). PASSES all four parts. The core book made Rs 1,57,215 after Rs 22,365 of switch charges,
against Rs 1,19,245 for Rs 1 lakh held in the Nifty and Rs 1,03,115 for the live book with its
idle cash earning nothing; ahead in 7 of 10 full years (behind in 2018, 2022, 2025), ahead in both
halves; worst fall Rs 46,123 against the Nifty's Rs 46,089 - it carries the market's risk, which
the plain book (worst fall about Rs 16,000) does not. Reported only: the idle cash in a liquid fund
instead gives Rs 1,33,583 at 5% and Rs 1,42,723 at 6.5%, also ahead of the Nifty with the plain
book's small falls. History holds only stocks listed today, which flatters drift; the margin over
the Nifty is about Rs 3,800 a year on Rs 1 lakh, and the live calls have been weaker since 2024.
From 2026-11-13 the money board scores this book forward on the live calls (paper).

    python -m tradedesk_lab.drift_core
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import drift_months as dm
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "drift_core"
SWITCH = 0.0035  # one Nifty ETF round trip on Rs 10,000
LIQUID_RATES = (0.05, 0.065)
YEARS_AHEAD = 0.7
FALL_RATIO = 1.25
SESSIONS_A_YEAR = 248


def held_slots(book: pd.DataFrame, sessions: int) -> np.ndarray:
    """Slots held by calls on each session, entry and exit sessions included."""

    held = np.zeros(sessions)
    for entry, exit_i in zip(book["entry_i"], book["exit_i"], strict=True):
        held[int(entry) : int(exit_i) + 1] += 1
    return held


def daily_rupees(
    book: pd.DataFrame, held: np.ndarray, nifty: np.ndarray, amount: float = dm.AMOUNT,
    slots: int = dm.SLOTS, switch: float = SWITCH,
) -> dict[str, np.ndarray]:  # fmt: skip
    """Rupees made each session by the live book, by the core book and by the Nifty alone.
    A call's result is booked on its exit session; the index earns the session's return on the
    slots that were free on that session."""

    sessions = len(nifty)
    move = np.zeros(sessions)
    move[1:] = nifty[1:] / nifty[:-1] - 1
    calls = np.zeros(sessions)
    np.add.at(calls, book["exit_i"].to_numpy().astype(int), book["net"].to_numpy() * amount)
    charges = np.zeros(sessions)
    np.add.at(charges, book["entry_i"].to_numpy().astype(int), switch * amount)
    idle = slots - held
    index = idle * amount * move
    return {
        "live": calls,
        "core": calls + index - charges,
        "nifty": slots * amount * move,
        "idle": idle,
        "charges": charges,
    }


def worst_fall(rupees: np.ndarray) -> float:
    path = np.cumsum(rupees)
    return float((path - np.maximum.accumulate(np.concatenate([[0.0], path]))[1:]).min())


def judge(frame: pd.DataFrame) -> dict[str, Any]:
    """The four-part bar on a frame with columns year, core, nifty (one row a session)."""

    years = frame.groupby("year").agg(core=("core", "sum"), nifty=("nifty", "sum"),
                                      n=("core", "size"))  # fmt: skip
    full = years[years["n"] >= 200]
    ahead = int((full["core"] > full["nifty"]).sum())
    half = len(frame) // 2
    halves = [frame.iloc[:half], frame.iloc[half:]]
    halves_ahead = [bool(h["core"].sum() > h["nifty"].sum()) for h in halves]
    fall_core = worst_fall(frame["core"].to_numpy())
    fall_nifty = worst_fall(frame["nifty"].to_numpy())
    fails = []
    if frame["core"].sum() <= frame["nifty"].sum():
        fails.append("behind the Nifty over the whole period")
    if ahead < YEARS_AHEAD * len(full):
        fails.append(f"ahead in only {ahead} of {len(full)} full years")
    if not all(halves_ahead):
        fails.append(f"halves ahead {halves_ahead}")
    if fall_core < FALL_RATIO * fall_nifty:
        fails.append(
            f"worst fall Rs {fall_core:,.0f} beyond 1.25 x the Nifty's Rs {fall_nifty:,.0f}"
        )
    return {
        "core_inr": float(frame["core"].sum()), "nifty_inr": float(frame["nifty"].sum()),
        "full_years": len(full), "years_ahead": ahead, "halves_ahead": halves_ahead,
        "worst_fall_core": fall_core, "worst_fall_nifty": fall_nifty,
        "by_year": {int(y): {"core": round(float(r.core)), "nifty": round(float(r.nifty))}
                    for y, r in years.iterrows()},
        "passed": not fails, "fails": fails,
    }  # fmt: skip


def run() -> dict[str, Any]:
    p = eh.load_panels(dt.DB)
    highs = mr.load(dt.DB)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    calls = dm.build_calls(p, highs)
    book = dm.book(calls)
    nifty = p["close"][eh.NIFTY].ffill().to_numpy(dtype=float)
    sessions = len(nifty)
    held = held_slots(book, sessions)
    got = daily_rupees(book, held, nifty)
    days = pd.to_datetime(pd.Series(list(p["close"].index)))
    first = int(book["entry_i"].min())
    frame = pd.DataFrame({
        "year": days.dt.year.to_numpy(), "live": got["live"], "core": got["core"],
        "nifty": got["nifty"], "idle": got["idle"],
    }).iloc[first:]  # fmt: skip
    verdict = judge(frame)
    liquid = {
        f"{rate:.1%}": round(float((frame["idle"] * dm.AMOUNT * rate / SESSIONS_A_YEAR).sum()
                                   + frame["live"].sum()))
        for rate in LIQUID_RATES
    }  # fmt: skip
    report = {
        "generated_at": datetime.now(IST).isoformat(),
        "from": str(days.iloc[first].date()), "to": str(days.iloc[-1].date()),
        "book_trades": len(book), "mean_slots_held": round(float(held[first:].mean()), 2),
        "live_book_inr": round(float(frame["live"].sum())),
        "switch_charges_inr": round(float(got["charges"][first:].sum())),
        "live_book_plus_liquid_fund": liquid,
        **verdict,
    }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    return report


def main() -> None:
    r = run()
    print(f"{r['from']} to {r['to']}: {r['book_trades']} calls, {r['mean_slots_held']} of 10 "
          f"slots held on average")  # fmt: skip
    print(f"live book Rs {r['live_book_inr']:+,}; with idle cash in a liquid fund "
          f"{r['live_book_plus_liquid_fund']}")  # fmt: skip
    print(f"core book Rs {r['core_inr']:+,.0f} (switch charges Rs {r['switch_charges_inr']:,}) "
          f"against the Nifty Rs {r['nifty_inr']:+,.0f}")  # fmt: skip
    print(f"years ahead {r['years_ahead']} of {r['full_years']}, halves {r['halves_ahead']}, "
          f"worst fall Rs {r['worst_fall_core']:,.0f} vs "
          f"Rs {r['worst_fall_nifty']:,.0f}")  # fmt: skip
    for year, row in r["by_year"].items():
        print(f"  {year}: core Rs {row['core']:+,} Nifty Rs {row['nifty']:+,}")
    print("PASS" if r["passed"] else f"FAIL {r['fails']}")


if __name__ == "__main__":
    main()
