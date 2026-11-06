"""Trying to break the IPO finding: a listing open 10-30% over the issue price keeps rising.

ipo_listing.py's bar gave that bucket a "hold" at the close, 20 and 60 sessions, while under 10% and
over 30% gave nothing. Its holder's question has a second reading, a trade anyone could take: buy at
the listing open when it opens in that band. That reading was not pre-registered and a hump with no
obvious reason is a warning, so this tries to sink it. Written 2026-11-07, after that result and
before any of these ran; reported whatever they show. Judged on the 20-session hold, because a
same-day sale may not be allowed in a new listing and 60 sessions overlap most. Passing earns a
forward paper trial, nothing more: the same history cannot prove what it suggested.

  costs     A Rs 10,000 buyer's real delivery charges and 1% worse than the open on entry, since
            listing opens are hard to get. FAILS unless mean net is still > 0.
  outliers  Mean excess without the 5 best. FAILS if it is not positive.
  median    Median excess. FAILS if it is not positive.
  years     Mean excess by listing year with 3+ IPOs. FAILS if under 60% are positive.
  quarter_t One calendar quarter as one unit. FAILS under t 2.0.
  bands     5-25% and 15-35%. FAILS if either has a mean excess <= 0 or t under 1.5: a real effect
            should not depend on the exact edges.
  recent    2023 on alone. FAILS if its mean excess is not positive.
Reported only: the gain band by band in 5-point steps and the IPOs themselves.

    python -m tradedesk_lab.ipo_checks
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import duckdb
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import event_studies as es
from tradedesk_lab import ipo_listing as il
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "ipo_listing"
BAND = (0.10, 0.30)
HOLD = "s20"
EXTRA_ENTRY = 0.01
M15 = OUTPUT / "intraday_history" / "nse.duckdb"
MIN_YEAR_IPOS = 3
NEARBY = ((0.05, 0.25), (0.15, 0.35))
FINE_STEPS = tuple(round(-0.10 + 0.05 * i, 2) for i in range(14))


def in_band(frame: pd.DataFrame, low: float, high: float) -> pd.DataFrame:
    return frame[(frame["gain"] >= low) & (frame["gain"] < high)]


def buyer_net(frame: pd.DataFrame, cost: Any, hold: str = HOLD) -> pd.Series:
    """Net return a Rs 10,000 buyer gets: `hold` is measured from the open, so it is worsened by the
    extra entry cost and that amount's delivery charges (on a price of 100, as a share of value)."""

    gross = frame[hold].dropna()
    return gross.map(lambda r: (1 + r) / (1 + EXTRA_ENTRY) - 1 - cost(100.0, 100.0 * (1 + r)))


def quarter_t(frame: pd.DataFrame, column: str) -> tuple[float, float, int]:
    means = frame.groupby(pd.to_datetime(frame["day"]).dt.to_period("Q"))[column].mean()
    n = len(means)
    if n < 2 or float(means.std(ddof=1)) == 0:
        return (float(means.mean()) if n else math.nan, math.nan, n)
    return float(means.mean()), float(means.mean() / (means.std(ddof=1) / math.sqrt(n))), n


def run_checks(frame: pd.DataFrame, cost: Any) -> dict[str, Any]:
    column = f"{HOLD}_x"
    band = in_band(frame, *BAND).dropna(subset=[column])
    values = sorted(band[column])
    net = buyer_net(band, cost)
    years = band.groupby(band["day"].map(lambda d: d.year))[column].agg(["count", "mean"])
    judged = years[years["count"] >= MIN_YEAR_IPOS]
    year_share = float((judged["mean"] > 0).mean()) if len(judged) else 0.0
    q_mean, q_t, quarters = quarter_t(band, column)
    without = sum(values[:-5]) / max(1, len(values) - 5)
    median = float(band[column].median())
    recent = band[band["day"].map(lambda d: d.year >= 2023)]
    nearby = {}
    for low, high in NEARBY:
        part = in_band(frame, low, high).dropna(subset=[column])
        mean, t, _ = nl.week_t(part.assign(excess=part[column]))
        nearby[f"{low:.0%}-{high:.0%}"] = {"ipos": len(part), "mean_excess": mean, "t": t}
    fails = []
    if not net.mean() > 0:
        fails.append("no money after charges and a worse entry")
    if not without > 0:
        fails.append("nothing left without the 5 best")
    if not median > 0:
        fails.append("median excess not positive")
    if year_share < 0.6:
        fails.append(f"positive in {year_share:.0%} of years")
    if not q_t >= 2.0:
        fails.append(f"quarter t {q_t:.2f}")
    if any(not (v["mean_excess"] > 0 and v["t"] >= 1.5) for v in nearby.values()):
        fails.append("depends on the exact band edges")
    if not float(recent[column].mean()) > 0:
        fails.append("not positive from 2023")
    return {
        "ipos": len(band), "mean_excess": float(band[column].mean()), "median_excess": median,
        "without_5_best": without, "buyer_mean_net": float(net.mean()),
        "buyer_median_net": float(net.median()), "buyer_win_rate": float((net > 0).mean()),
        "year_share": year_share,
        "by_year": {str(y): {"ipos": int(r["count"]), "mean": float(r["mean"])}
                    for y, r in years.iterrows()},
        "quarter": {"mean": q_mean, "t": q_t, "quarters": quarters}, "nearby": nearby,
        "recent": {"ipos": len(recent), "mean_excess": float(recent[column].mean())},
        "fails": fails, "survives": not fails,
    }  # fmt: skip


def fine_bands(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    column = f"{HOLD}_x"
    out = {}
    for low in FINE_STEPS:
        part = in_band(frame, low, low + 0.05 if low < 0.55 else math.inf).dropna(subset=[column])
        if len(part):
            out[f"{low:+.0%}"] = {"ipos": len(part), "mean": float(part[column].mean()),
                                  "median": float(part[column].median())}  # fmt: skip
    return out


def first_bar_gap(band: pd.DataFrame, opens: pd.DataFrame, codes: dict[str, str]) -> dict[str, Any]:
    """How far above the day's open the first 15-minute bar closed, where bars exist."""

    con = duckdb.connect(str(M15), read_only=True)
    gaps = []
    try:
        for row in band.to_dict("records"):
            code = codes.get(row["symbol"])
            if code is None or code not in opens.columns:
                continue
            bars = con.execute(
                "SELECT ts, close FROM m15 WHERE code = ? ORDER BY ts LIMIT 1", [code]
            ).fetchall()
            if not bars:
                continue
            first_day = pd.to_datetime(bars[0][0], unit="s", utc=True).tz_convert(IST).date()
            if first_day == row["day"]:
                gaps.append(float(bars[0][1]) / float(opens.at[row["day"], code]) - 1)
    finally:
        con.close()
    if not gaps:
        return {"ipos": 0}
    return {"ipos": len(gaps), "mean": sum(gaps) / len(gaps), "max": max(gaps), "min": min(gaps),
            "median": sorted(gaps)[len(gaps) // 2]}  # fmt: skip


def main() -> None:
    p = nl.load()
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)

    def span(e: int, x: int) -> float:
        move = p.close.iloc[x] / p.open.iloc[e] - 1
        return float(move[(turnover.iloc[e] >= nl.MIN_TURNOVER) & move.notna()].mean())

    codes = es.symbols()
    raw = json.loads(il.STORE.read_text(encoding="utf-8"))
    ipos, _ = il.clean(raw, il.corporate_actions())
    frame, _ = il.outcomes(ipos, p, span, codes)
    cost = nl.cost_function()
    band = in_band(frame, *BAND)
    checks = run_checks(frame, cost)
    report = {
        "generated_at": datetime.now(IST).isoformat(), "checks": checks,
        "fine_bands": fine_bands(frame), "first_bar_gap": first_bar_gap(band, p.open, codes),
        "band_ipos": [
            {"symbol": r["symbol"], "day": str(r["day"]), "gain": round(r["gain"], 3),
             "close": r.get("close"), "s20": r.get("s20")}
            for r in band.sort_values("day").to_dict("records")
        ],
    }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "checks.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: report[k] for k in ("checks", "fine_bands", "first_bar_gap")}, indent=1))


if __name__ == "__main__":
    main()
