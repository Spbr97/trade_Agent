"""Do a protective stop or a Nifty hedge improve the earnings-drift calls?

Research only (owner-approved 2026-10-18). Same events and entries as the live rule in
src/tradedesk/earnings_drift.py (liquid NSE stocks, 20-session turnover >= Rs 20 crore, results
reaction >= +5% vs the equal-weight market on >= 2x volume, buy the next open, sell the close of
the 20th session, delivery round trip deducted). Variants, fixed before any result was seen:

  base      the live rule as it is
  stop8     if a daily CLOSE is 8% or more below the entry, sell at the NEXT open (gap-aware)
  stop12    the same at 12%
  hedged    the base trade plus a short NIFTY 50 future of equal value over the same days
            (earns the ~5.2%/yr carry, pays 0.04% to open and close)

A variant counts as an improvement only if its return per unit of risk (mean / standard deviation
of per-trade returns) is at least 10% better than base AND its mean return is no more than 20%
lower - a stop that only trims winners is not an improvement.

Usage: python -m tradedesk_lab.earnings_drift_variants --db data/tradedesk.duckdb
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab import edge_hunt as eh
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "earnings_drift_variants"
HOLD = 20
MIN_TURNOVER = 2e8


def events(p: dict[str, Any]) -> pd.DataFrame:
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
        d = pd.Timestamp(d)
        i1 = dates.searchsorted(d, side="right")
        i0 = dates.searchsorted(d, side="left") - 1
        if i0 < 20 or i1 + HOLD + 1 >= len(dates):
            continue
        j = col[code]
        if not (turn20.iat[i1, j] >= MIN_TURNOVER and closes.iat[i1 - 1, j] >= eh.MIN_PRICE):
            continue
        c0, c1 = closes.iat[i0, j], closes.iat[i1, j]
        wv, v20 = vol.iloc[i0 + 1 : i1 + 1, j].max(), vol20.iat[i1, j]
        if not (np.isfinite(c0) and np.isfinite(c1) and c0 > 0 and np.isfinite(wv) and v20 > 0):
            continue
        mkt = float((1 + daily_mkt.iloc[i0 + 1 : i1 + 1]).prod() - 1)
        if c1 / c0 - 1 - mkt >= 0.05 and wv / v20 >= 2.0:
            rows.append((code, i1))
    return pd.DataFrame(rows, columns=["code", "i1"]).drop_duplicates()


def trade(p: dict[str, Any], code: str, i1: int, variant: str) -> tuple[Any, float] | None:
    opens, closes = p["open"], p["close"]
    j = closes.columns.get_loc(code)
    e, x = i1 + 1, i1 + HOLD
    entry = opens.iat[e, j]
    if not (np.isfinite(entry) and entry > 0):
        return None
    exit_price = closes.iat[x, j]
    if variant in ("stop8", "stop12"):
        limit = entry * (1 - (0.08 if variant == "stop8" else 0.12))
        for k in range(e, x):
            if closes.iat[k, j] <= limit:
                nxt = opens.iat[k + 1, j]
                exit_price = nxt if np.isfinite(nxt) else closes.iat[k, j]
                break
    if not np.isfinite(exit_price):
        return None
    ret = exit_price / entry - 1 - eh.DELIVERY_RT
    if variant == "hedged":
        n_open = opens.iat[e, closes.columns.get_loc(eh.NIFTY)]
        n_close = closes.iat[x, closes.columns.get_loc(eh.NIFTY)]
        if not (np.isfinite(n_open) and n_open > 0):
            n_open = closes.iat[e - 1, closes.columns.get_loc(eh.NIFTY)]
        ret += -(n_close / n_open - 1) + eh.CARRY * HOLD / 252 - 2 * eh.FUT_COST
    return closes.index[e], float(ret)


def run(db: Path) -> dict[str, Any]:
    p = eh.load_panels(db)
    ev = events(p)
    results = {}
    for variant in ("base", "stop8", "stop12", "hedged"):
        rows = [
            r
            for r in (trade(p, c, i, variant) for c, i in zip(ev["code"], ev["i1"], strict=True))
            if r
        ]
        t = pd.DataFrame(rows, columns=["day", "ret"])
        years = t.groupby(pd.to_datetime(t["day"]).dt.year)["ret"].mean()
        months = t.groupby(pd.to_datetime(t["day"]).dt.to_period("M"))["ret"].mean()
        results[variant] = {
            "trades": int(len(t)),
            "mean_return": float(t["ret"].mean()),
            "sd": float(t["ret"].std(ddof=1)),
            "return_per_risk": float(t["ret"].mean() / t["ret"].std(ddof=1)),
            "win_rate": float((t["ret"] > 0).mean()),
            "worst_trade": float(t["ret"].min()),
            "worst_month_avg": float(months.min()),
            "positive_years": f"{int((years > 0).sum())}/{len(years)}",
            "by_year": {str(k): float(v) for k, v in years.items()},
        }
    base = results["base"]
    for name, r in results.items():
        r["improves_on_base"] = bool(
            name != "base"
            and r["return_per_risk"] >= 1.10 * base["return_per_risk"]
            and r["mean_return"] >= 0.80 * base["mean_return"]
        )
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "authority": "research_only",
        "results": results,
        "doc": __doc__,
    }  # noqa: E501
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    for name in (f"{stamp}.json", "latest.json"):
        (OUT / name).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    for name, r in run(args.db)["results"].items():
        yrs = " ".join(f"{y[2:]}:{v:+.1%}" for y, v in r["by_year"].items())
        print(
            f"{name:7s} n={r['trades']} mean={r['mean_return']:+.2%} sd={r['sd']:.1%} per-risk={r['return_per_risk']:.3f} "  # noqa: E501
            f"win={r['win_rate']:.0%} worst={r['worst_trade']:+.0%} worst-month={r['worst_month_avg']:+.1%} "  # noqa: E501
            f"years+={r['positive_years']} {'IMPROVES' if r['improves_on_base'] else ''}\n         [{yrs}]"
        )  # noqa: E501


if __name__ == "__main__":
    main()
