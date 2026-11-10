"""Drift on Rs 1 lakh: would fewer, larger positions or a rule for which calls to take earn more?

Drift's ten Rs 10,000 slots are full for most of a results season, so the Rs 1 lakh book the owner
could run takes about four calls in ten and since October held 2 of the 9 paper winners
(money_board.py). Two things could lift what that Rs 1 lakh earns without touching the signal or
the exit, both of which stay exactly the live rule's (drift_swing.py showed the exit is not the
problem): a larger position pays less in charges per rupee, and when several calls arrive on one
session the book could take the more promising ones first instead of whichever comes first. Owner
approved this test on 2026-11-11. Research only: no call, alert or live rule changes; a pass is a
proposal for how the money board counts the book and which calls are labelled "take".

CALLS. Drift's history (drift_months.build_calls), marked calls never taken, a stock held once.
A call's rupees at a position size = size x (its gross return - the real delivery round trip at
that size, whole shares, from risk.yaml). The live record's flat 0.564% is the Rs 10,000 case.

SIZES, each run 200 times with same-session calls in a random order, because the order they
arrive in is an accident (drift_months: it moves ten-year rupees from Rs 69,000 to Rs 1,09,000):
  10 x Rs 10,000 (live), 5 x Rs 20,000, 4 x Rs 25,000.
RANKINGS, each known at the signal close, the highest first among same-session calls:
  reaction         the jump over the market that made the call (5% is the floor);
  volume_multiple  the results volume over the 20-session average (2x is the floor);
  turnover         the stock's 20-session traded value (the larger company first).

PASS BAR (written 2026-11-11, before any result was seen).
A SIZE replaces 10 x Rs 10,000 only if, comparing the medians of the 200 runs:
  S1. it makes at least 10% more rupees over the whole history;
  S2. its worst fall from a peak is no more than 1.25 times the live size's;
  S3. it makes more rupees in both halves of the history (split at the median call).
A RANKING is adopted only if both:
  R1. per call, at the live size: calls in the top half of the measure (within their calendar
      year) return more than the bottom half, by entry week, t >= 2.5, and the difference has the
      same sign in both halves of the history. t >= 2.5 is about Bonferroni for three.
  R2. its ranked ten-slot book makes more rupees than 95% of the 200 random-order books, and its
      worst fall is no more than 1.25 times their median.
Rankings at the other sizes are reported, not judged. Nothing is changed after the run, and no
fourth size or ranking is tried.

RESULT (2026-11-11; 1,605 calls, 1,517 unmarked, halves split at 2024-02-15). NOTHING PASSES: the
live ten Rs 10,000 slots stay, and same-session calls stay in the order they arrive.
Sizes, medians of 200 random-order runs over the ten years:
  10 x Rs 10,000  +Rs 88,024 (Rs 68,022 to Rs 1,09,323), worst fall Rs 17,436, worst trade
                  -Rs 6,053, 630 trades, charges 0.58%, 7 of 11 years ahead
   5 x Rs 20,000  +Rs 65,241 (Rs 31,618 to Rs 96,765), worst fall Rs 30,266, worst trade
                  -Rs 12,077, 362 trades, charges 0.40%. Fails S1, S2 and S3.
   4 x Rs 25,000  +Rs 85,663 (Rs 36,896 to Rs 1,43,579), worst fall Rs 28,667, worst trade
                  -Rs 9,669, 298 trades, charges 0.36%. Fails S1, S2 and S3.
Larger positions do pay less in charges, but they take half as many calls, so luck in which calls
they hold swamps the saving: the range of outcomes doubles and the falls are 1.6 to 1.7 times as
deep, for no more money.
Rankings, per call (top half against bottom half, by week) and the ranked ten-slot book:
  reaction         +1.47% against +1.21%, t 0.38; book +Rs 93,647, ahead of 76% of random orders
  volume_multiple  +1.66% against +1.02%, t 0.09, reversed in the second half; book +Rs 93,340,
                   ahead of 76%
  turnover         +1.13% against +1.56%, t -1.48; book +Rs 82,337, ahead of 18%
None of the three says which call will do better, so there is nothing to rank by.
THE FINDING THAT MATTERS, which no bar here asked for: the live book's two halves are +Rs 84,829
and +Rs 3,298. Since February 2024, half of all the calls, a Rs 1 lakh drift book has made about
nothing. A read-only check per call the same day (unmarked calls, live exit, by entry year):
2023 +3.88%, 2024 +2.68%, 2025 -0.46%, 2026 +0.49% a call; over the market +1.03%, +0.95%,
+0.58%, +0.18%. First half +1.82% a call, +0.84% over the market, t 2.67, 63% win; second half
+0.86%, +0.59%, t 1.19, 56% win. Calls beat the market in every year 2016-2026, but by less
lately, and a long-only call earns little when the market itself goes nowhere.

    python -m tradedesk_lab.drift_capital
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab import drift_months as dm
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "drift_capital"
SIZES: dict[str, tuple[int, float]] = {
    "10 x Rs 10,000": (10, 10_000.0),
    "5 x Rs 20,000": (5, 20_000.0),
    "4 x Rs 25,000": (4, 25_000.0),
}
LIVE = "10 x Rs 10,000"
RANKINGS = ("reaction", "volume_multiple", "turnover")
SHUFFLES = 200
SEED = 20261111
PASS = {"more_rupees": 1.10, "fall_ratio": 1.25, "min_t": 2.5, "beats_share": 0.95}


def call_measures(p: Mapping[str, Any]) -> pd.DataFrame:
    """Each qualifying results reaction with the three measures, by earnings_drift_variants'
    own arithmetic (its `events` keeps only the stock and the session)."""

    closes, vol = p["close"], p["volume"]
    dates = closes.index
    turn20 = (closes * vol).rolling(20).mean().shift(1)
    vol20 = vol.rolling(20).mean().shift(1)
    market_ok = (turn20 >= eh.MIN_TURNOVER) & (closes.shift(1) >= eh.MIN_PRICE)
    daily_mkt = closes.pct_change(fill_method=None).where(market_ok).mean(axis=1)
    col = {c: i for i, c in enumerate(closes.columns)}
    rows = []
    for sym, d in p["events"].itertuples(index=False):
        code = p["symbols"].get(sym)
        if code not in col:
            continue
        day = pd.Timestamp(d)
        i1 = dates.searchsorted(day, side="right")
        i0 = dates.searchsorted(day, side="left") - 1
        if i0 < 20 or i1 + edv.HOLD + 1 >= len(dates):
            continue
        j = col[code]
        if not (turn20.iat[i1, j] >= edv.MIN_TURNOVER and closes.iat[i1 - 1, j] >= eh.MIN_PRICE):
            continue
        c0, c1 = closes.iat[i0, j], closes.iat[i1, j]
        wv, v20 = vol.iloc[i0 + 1 : i1 + 1, j].max(), vol20.iat[i1, j]
        if not (np.isfinite(c0) and np.isfinite(c1) and c0 > 0 and np.isfinite(wv) and v20 > 0):
            continue
        mkt = float((1 + daily_mkt.iloc[i0 + 1 : i1 + 1]).prod() - 1)
        reaction, multiple = c1 / c0 - 1 - mkt, wv / v20
        if reaction >= 0.05 and multiple >= 2.0:
            rows.append((code, int(i1) + 1, reaction, multiple, float(turn20.iat[i1, j])))
    frame = pd.DataFrame(rows, columns=["code", "entry_i", *RANKINGS])
    return frame.drop_duplicates(["code", "entry_i"])


def charge(size: float) -> Callable[[float, float], float]:
    """The delivery round trip of a position of `size` rupees, as a share of the money in it."""

    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    model = EquityCostModel(load_config(nl.ROOT).risk.costs)

    def cost(entry: float, exit_px: float) -> float:
        qty = max(1, int(size // entry))
        charged = model.round_trip_cost(
            trade_type=TradeType.DELIVERY, qty=qty, entry_price=Decimal(str(round(entry, 2))),
            exit_price=Decimal(str(round(exit_px, 2))),
        )  # fmt: skip
        return float(charged.total) / (qty * entry)

    return cost


def rupees_at(calls: pd.DataFrame, size: float, cost: Callable[[float, float], float]) -> (
    np.ndarray
):  # fmt: skip
    """Each call's result in rupees at this position size, after that size's own charges."""

    gross = calls["net"].to_numpy(float) + dt.RT
    entry = calls["entry_price"].to_numpy(float)
    paid = np.array([cost(e, e * (1 + g)) for e, g in zip(entry, gross, strict=True)])
    return size * (gross - paid)


def worst_fall(results: np.ndarray) -> float:
    curve = np.concatenate(([0.0], np.cumsum(results)))
    return float(np.max(np.maximum.accumulate(curve) - curve))


def book_result(calls: pd.DataFrame, slots: int, split: int) -> dict[str, Any]:
    """One book over the calls in the order given: rupees, the two halves, fall, good years."""

    held = dm.book(calls, slots).sort_values("exit_i", kind="stable")
    inr = held["inr"].to_numpy(float)
    early = held["entry_i"].to_numpy() < split
    years = held.groupby(held["exit_day"].dt.year)["inr"].sum()
    return {
        "trades": len(held), "rupees": float(inr.sum()),
        "profit": float(inr[inr > 0].sum()), "loss": float(inr[inr < 0].sum()),
        "first_half": float(inr[early].sum()), "second_half": float(inr[~early].sum()),
        "worst_fall": worst_fall(inr), "good_years": int((years > 0).sum()), "years": len(years),
        "worst_trade": float(inr.min()) if len(inr) else 0.0,
    }  # fmt: skip


def random_order(calls: pd.DataFrame, slots: int, split: int, shuffles: int = SHUFFLES,
                 seed: int = SEED) -> list[dict[str, Any]]:  # fmt: skip
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(shuffles):
        order = calls.assign(_r=rng.random(len(calls))).sort_values(["entry_i", "_r"])
        out.append(book_result(order.drop(columns="_r"), slots, split))
    return out


def middle(runs: list[dict[str, Any]]) -> dict[str, float]:
    """The median of each figure over the random-order runs, with the range of the rupees."""

    out = {k: float(np.median([r[k] for r in runs])) for k in runs[0]}
    rupees = [r["rupees"] for r in runs]
    return {**out, "rupees_low": float(min(rupees)), "rupees_high": float(max(rupees))}


def judge_size(mine: Mapping[str, float], live: Mapping[str, float]) -> dict[str, Any]:
    fails = []
    if not mine["rupees"] >= live["rupees"] * PASS["more_rupees"]:
        fails.append("under 10% more rupees")
    if not mine["worst_fall"] <= live["worst_fall"] * PASS["fall_ratio"]:
        fails.append("falls more than 1.25 times as far")
    if not (mine["first_half"] > live["first_half"] and mine["second_half"] > live["second_half"]):
        fails.append("not ahead in both halves")
    return {"adopted": not fails, "fails": fails}


def top_against_bottom(calls: pd.DataFrame, measure: str, split: int) -> dict[str, Any]:
    """Per call: the top half of the measure within each calendar year minus the bottom half,
    one entry week as one unit (only weeks holding both)."""

    mine = calls[~calls["marked"]].copy()
    year = mine["entry_day"].dt.year
    mine["top"] = mine[measure] >= mine.groupby(year)[measure].transform("median")
    mine["week"] = mine["entry_day"].dt.strftime("%G-%V")

    def gap(part: pd.DataFrame) -> pd.Series:
        by = part.groupby(["week", "top"])["net"].mean().unstack()
        by = by.rename(columns={True: "top", False: "bottom"})
        if "top" not in by or "bottom" not in by:
            return pd.Series(dtype=float)
        return (by["top"] - by["bottom"]).dropna()

    diff = gap(mine)
    n = len(diff)
    sd = float(diff.std(ddof=1)) if n > 1 else 0.0
    halves = [gap(mine[mine["entry_i"] < split]), gap(mine[mine["entry_i"] >= split])]
    return {
        "calls": len(mine), "weeks": n, "top_mean": float(mine.loc[mine["top"], "net"].mean()),
        "bottom_mean": float(mine.loc[~mine["top"], "net"].mean()),
        "difference": float(diff.mean()) if n else None,
        "t": float(diff.mean()) / (sd / math.sqrt(n)) if sd > 0 else None,
        "halves": [float(h.mean()) if len(h) else None for h in halves],
    }  # fmt: skip


def judge_ranking(per_call: Mapping[str, Any], ranked: Mapping[str, Any],
                  runs: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    beaten = float(np.mean([ranked["rupees"] > r["rupees"] for r in runs]))
    fall_limit = float(np.median([r["worst_fall"] for r in runs])) * PASS["fall_ratio"]
    fails = []
    if not ((per_call["difference"] or 0) > 0 and (per_call["t"] or 0) >= PASS["min_t"]):
        fails.append(f"per call t {per_call['t'] or 0:.2f}")
    if not all((h or 0) > 0 for h in per_call["halves"]):
        fails.append("top half not ahead in both halves of history")
    if beaten < PASS["beats_share"]:
        fails.append(f"beats only {beaten:.0%} of random-order books")
    if ranked["worst_fall"] > fall_limit:
        fails.append("falls more than 1.25 times as far")
    return {"adopted": not fails, "fails": fails, "beats_share": beaten}


def run() -> dict[str, Any]:
    p = eh.load_panels(dt.DB)
    highs = mr.load(dt.DB)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    calls = dm.build_calls(p, highs).merge(call_measures(p), on=["code", "entry_i"], how="left")
    column = {code: j for j, code in enumerate(p["close"].columns)}
    opens = p["open"].to_numpy(float)
    calls["entry_price"] = opens[calls["entry_i"].to_numpy(int), [column[c] for c in calls["code"]]]
    days = pd.Series(list(p["close"].index))
    calls["entry_day"] = pd.to_datetime(days.iloc[calls["entry_i"].to_numpy(int)].to_numpy())
    unmarked = calls[~calls["marked"]]
    split = int(unmarked["entry_i"].median())
    report: dict[str, Any] = {
        "calls": len(calls), "unmarked": len(unmarked),
        "measures_missing": int(calls[list(RANKINGS)].isna().any(axis=1).sum()),
        "split_day": str(days.iloc[split].date()), "sizes": {}, "rankings": {},
    }  # fmt: skip
    runs: dict[str, list[dict[str, Any]]] = {}
    for name, (slots, size) in SIZES.items():
        priced = calls.assign(inr=rupees_at(calls, size, charge(size)))
        runs[name] = random_order(priced, slots, split)
        report["sizes"][name] = {
            **middle(runs[name]),
            "mean_charge": float(np.mean(calls["net"] + dt.RT - priced["inr"] / size)),
        }
        for measure in RANKINGS:
            order = priced.sort_values(["entry_i", measure], ascending=[True, False])
            report["rankings"].setdefault(measure, {})[name] = book_result(order, slots, split)
    live = report["sizes"][LIVE]
    for name in SIZES:
        if name != LIVE:
            report["sizes"][name]["verdict"] = judge_size(report["sizes"][name], live)
    for measure in RANKINGS:
        per_call = top_against_bottom(calls, measure, split)
        report["rankings"][measure]["per_call"] = per_call
        report["rankings"][measure]["verdict"] = judge_ranking(
            per_call, report["rankings"][measure][LIVE], runs[LIVE]
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    return report


def main() -> None:
    r = run()
    print(f"{r['calls']} calls, {r['unmarked']} unmarked, halves split at {r['split_day']}, "
          f"{r['measures_missing']} calls without measures")  # fmt: skip
    for name, s in r["sizes"].items():
        print(f"{name}: median Rs {s['rupees']:+,.0f} (Rs {s['rupees_low']:,.0f} to "
              f"Rs {s['rupees_high']:,.0f}), halves Rs {s['first_half']:+,.0f} / "
              f"Rs {s['second_half']:+,.0f}, worst fall Rs {s['worst_fall']:,.0f}, worst trade "
              f"Rs {s['worst_trade']:,.0f}, {s['trades']:.0f} trades, good years "
              f"{s['good_years']:.0f} of {s['years']:.0f}, charges {s['mean_charge']:.3%} "
              f"{s.get('verdict', '')}")  # fmt: skip
    for measure, m in r["rankings"].items():
        pc, book = m["per_call"], m[LIVE]
        print(f"{measure}: top half {pc['top_mean']:+.2%} vs bottom {pc['bottom_mean']:+.2%}, "
              f"by week {pc['difference'] or 0:+.2%} t {pc['t'] or 0:.2f}, halves {pc['halves']}; "
              f"ranked ten-slot book Rs {book['rupees']:+,.0f}, fall Rs {book['worst_fall']:,.0f} "
              f"-> {m['verdict']}")  # fmt: skip
        print("   other sizes:", {n: round(m[n]["rupees"]) for n in SIZES if n != LIVE})


if __name__ == "__main__":
    main()
