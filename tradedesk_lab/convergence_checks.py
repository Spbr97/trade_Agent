"""Trying to break convergence's one pass, two_in_60 (tradedesk_lab/convergence.py).

Written 2026-11-07, after that pass and before any of these ran, and reported whatever they show.
Its signals were chosen after seeing them do well, and 60-session holds overlap, so a
week-clustered t can flatter it. A check failing here sinks the idea, and passing them all still
earns only a forward paper trial, because they use the same history.

  placebo    Does agreement add anything, or do stocks with many events just do well? Keep each
             stock's events but move its both_up and loud_strong dates by a random 60-250
             sessions either way, so the kinds no longer agree except by chance, and rebuild the
             calls (liquid at entry, as real ones are). Repeat 500 times. FAILS if 5% or more of
             the shuffled runs reach the real mean excess.
  quarter_t  One calendar QUARTER as one unit, which removes most overlap between holds. FAILS
             under t 2.0.
  outliers   Mean excess without the 5 best calls. FAILS if it is not positive.
  median     Median excess. FAILS if it is not positive: a few big winners would be the result.
  years      Mean excess by entry year, 5+ calls. FAILS if under 60% of those years are positive.
  stocks     Share of the summed excess from the 5 best stocks. Reported.
  pairs      The result for each pair of kinds. Reported, as a pass carried by one pair is weaker.
  nearby     Other windows (30, 45, 90) and holds (20, 40). FAILS if a window is negative or more
             than one of these five settings has t under 1.5: a real effect is no knife edge.

ADDED 2026-11-07, after the placebo above FAILED (42.2% of runs reached the real result; every other
check passed). That failure stands on the record. But the design has a flaw: moving an event EARLIER
places a call before the good news it rests on, so it buys ahead of a later 3%+ gap or fund buying,
which is look-ahead in the placebo's favour. placebo_later moves events LATER only, so a call only
ever knows what had happened, and is the fair form of the question. Same 500 draws, same 5% line,
written before it ran. It does not erase the first result: both are reported.

    python -m tradedesk_lab.convergence_checks
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import bulk_block_deals as bb
from tradedesk_lab import convergence as cv
from tradedesk_lab import event_studies as es
from tradedesk_lab import institutional_stakes as st
from tradedesk_lab import new_listings as nl
from tradedesk_lab import results_reading as rr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "convergence"
NEED, WINDOW = 2, 60
DRAWS = 500
SEED = 20261107
SHIFT = (60, 250)
MOVED = ("both_up", "loud_strong")
Event = tuple[str, int, str]


def shuffled(
    events: Iterable[Event], rng: random.Random, last_row: int, *, later_only: bool = False
) -> list[Event]:
    """Each stock's MOVED kinds shifted by one random amount per stock and kind, inside the data.
    A kind's own spacing within a stock is kept; only its timing against the others is broken."""

    offsets: dict[tuple[str, str], int] = {}
    out = []
    for code, e, kind in events:
        if kind not in MOVED:
            out.append((code, e, kind))
            continue
        key = (code, kind)
        if key not in offsets:
            sign = 1 if later_only else rng.choice((-1, 1))
            offsets[key] = sign * rng.randint(*SHIFT)
        moved = e + offsets[key]
        if 1 <= moved <= last_row:
            out.append((code, moved, kind))
    return out


def quarter_t(calls: pd.DataFrame) -> tuple[float, float, int]:
    """(mean of quarter means, t, quarters): one calendar quarter as one unit."""

    means = calls.groupby(pd.to_datetime(calls["day"]).dt.to_period("Q"))["excess"].mean()
    n = len(means)
    if n < 2 or float(means.std(ddof=1)) == 0:
        return (float(means.mean()) if n else math.nan, math.nan, n)
    return float(means.mean()), float(means.mean() / (means.std(ddof=1) / math.sqrt(n))), n


def without_best(values: Sequence[float], n: int = 5) -> float:
    kept = sorted(values)[: max(0, len(values) - n)]
    return statistics.fmean(kept) if kept else math.nan


def kinds_at_call(
    picks: Iterable[tuple[str, int]], events: Iterable[Event], window: int = WINDOW
) -> dict[tuple[str, int], str]:
    """(code, entry) -> the kinds that made the call, joined in a fixed order."""

    by_code: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for code, e, kind in events:
        by_code[code].append((e, kind))
    return {
        (code, e): "+".join(sorted({k for when, k in by_code[code] if e - window < when <= e}))
        for code, e in picks
    }


def calls_held(measure: rr.Measure, picks: Iterable[tuple[str, int]], hold: int) -> pd.DataFrame:
    rows = [c for code, e in picks if (c := measure.call(code, e, str(hold)))]
    return pd.DataFrame(rows, columns=["code", "day", "net", "excess", "peer", "days"])


def _held(measure: rr.Measure, code: str, e: int, hold: int) -> float | None:
    """Excess of one call at a hold exit_row does not name (only 20 and 60 are built in)."""

    j = measure.column[code]
    x = e + hold - 1
    entry, close = float(measure.opens[e, j]), None
    if x < len(measure.closes):
        close = float(measure.closes[x, j])
    if close is None or not (math.isfinite(entry) and math.isfinite(close) and entry > 0):
        return None
    bought, sold = entry * (1 + rr.SLIPPAGE), close * (1 - rr.SLIPPAGE)
    return sold / bought - 1 - measure.delivery(bought, sold) - measure.market(e, x)


def nearby(measure: rr.Measure, events: list[Event]) -> dict[str, dict[str, Any]]:
    settings = {f"window_{w}": (w, 60) for w in (30, 45, 90)}
    settings |= {f"hold_{h}": (60, h) for h in (20, 40)}
    out = {}
    for name, (window, hold) in settings.items():
        picks = cv.convergent(events, NEED, window)
        rows = []
        for code, e in picks:
            excess = _held(measure, code, e, hold)
            if excess is not None:
                rows.append({"day": measure.p.close.index[e], "excess": excess})
        frame = pd.DataFrame(rows, columns=["day", "excess"])
        mean, t, weeks = nl.week_t(frame) if len(frame) else (math.nan, math.nan, 0)
        out[name] = {"calls": len(frame), "mean_excess_week": mean, "t": t, "weeks": weeks}
    return out


def main() -> None:
    p = nl.load()
    sessions = list(p.close.index)
    codes = es.symbols()
    measure = rr.Measure(p)
    events = [
        *cv.fund_bulk_events(bb.read_store(), sessions, codes),
        *cv.both_up_events(st.read_stakes(), sessions, codes),
        *cv.loud_strong_events(rr.one_per_quarter(rr.read_figures()), measure, sessions, codes),
    ]
    events = [x for x in events if cv.liquid_at(measure, x[0], x[1])]
    picks = cv.convergent(events, NEED, WINDOW)
    calls = calls_held(measure, picks, 60)
    real = float(calls["excess"].mean())

    cache: dict[tuple[str, int], float | None] = {}

    def excess_of(code: str, e: int) -> float | None:
        if (code, e) not in cache:
            cache[(code, e)] = _held(measure, code, e, 60)
        return cache[(code, e)]

    last_row = len(sessions) - 1

    def run_placebo(later_only: bool) -> list[float]:
        rng = random.Random(SEED)
        means = []
        for _ in range(DRAWS):
            moved = shuffled(events, rng, last_row, later_only=later_only)
            values = [
                v for code, e in cv.convergent(moved, NEED, WINDOW)
                if cv.liquid_at(measure, code, e) and (v := excess_of(code, e)) is not None
            ]  # fmt: skip
            if values:
                means.append(statistics.fmean(values))
        return means

    placebo = run_placebo(False)
    share = sum(v >= real for v in placebo) / len(placebo)
    later = run_placebo(True)
    later_share = sum(v >= real for v in later) / len(later)

    q_mean, q_t, quarters = quarter_t(calls)
    trimmed = without_best(list(calls["excess"]))
    median = float(calls["excess"].median())
    years = pd.to_datetime(calls["day"]).dt.year
    by_year = calls.groupby(years)["excess"].agg(["count", "mean"])
    judged = by_year[by_year["count"] >= 5]
    year_share = float((judged["mean"] > 0).mean())
    best = calls.groupby("code")["excess"].sum().sort_values(ascending=False)
    top_share = float(best.head(5).sum() / calls["excess"].sum())
    made = kinds_at_call(picks, events)
    calls = calls.assign(pair=[
        made[(c, sessions.index(d))] for c, d in zip(calls["code"], calls["day"], strict=True)
    ])  # fmt: skip
    pairs = {
        k: {"calls": len(g), "mean_net": float(g["net"].mean()),
            "mean_excess": float(g["excess"].mean()), "t": nl.week_t(g)[1]}
        for k, g in calls.groupby("pair")
    }  # fmt: skip
    around = nearby(measure, events)
    windows = [v for k, v in around.items() if k.startswith("window")]
    weak = sum(1 for v in around.values() if not v["t"] >= 1.5)
    fails = []
    if share >= 0.05:
        fails.append(f"placebo reaches it {share:.1%} of the time")
    if later_share >= 0.05:
        fails.append(f"later-only placebo reaches it {later_share:.1%} of the time")
    if not q_t >= 2.0:
        fails.append(f"quarter t {q_t:.2f}")
    if not trimmed > 0:
        fails.append("nothing left without the 5 best calls")
    if not median > 0:
        fails.append("median excess not positive")
    if year_share < 0.6:
        fails.append(f"positive in {year_share:.0%} of years")
    if any(not v["mean_excess_week"] > 0 for v in windows) or weak > 1:
        fails.append("knife edge among nearby settings")
    report = {
        "generated_at": datetime.now(IST).isoformat(), "calls": len(calls), "real_excess": real,
        "mean_net": float(calls["net"].mean()), "win_rate": float((calls["net"] > 0).mean()),
        "median_net": float(calls["net"].median()),
        "placebo": {"draws": len(placebo), "mean": statistics.fmean(placebo),
                    "p95": sorted(placebo)[int(0.95 * len(placebo))], "share_at_or_above": share},
        "placebo_later": {"draws": len(later), "mean": statistics.fmean(later),
                          "p95": sorted(later)[int(0.95 * len(later))],
                          "share_at_or_above": later_share},
        "quarter": {"mean": q_mean, "t": q_t, "quarters": quarters},
        "without_5_best": trimmed, "median_excess": median,
        "by_year": {str(y): {"calls": int(r["count"]), "mean": float(r["mean"])}
                    for y, r in by_year.iterrows()},
        "year_share": year_share, "top_5_stock_share": top_share,
        "top_stocks": list(best.head(5).index), "pairs": pairs, "nearby": around,
        "calls_a_year": len(calls) / max(1, len(by_year)),
        "kinds": dict(Counter(x[2] for x in events)), "fails": fails, "survives": not fails,
    }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "checks.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
