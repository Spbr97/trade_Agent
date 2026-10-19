"""Does a market-timing filter improve the live earnings-drift calls? (owner, 2026-10-19)

Research only. Same events, entries and exits as the live rule (src/tradedesk/earnings_drift.py,
via tradedesk_lab/earnings_drift_variants.py), re-costed at the real delivery round trip for a
Rs 10,000 call (0.564%: the fixed per-sale charges; 0.391% was assumed before). Variants fixed
before any result was seen:

  base        every call
  nifty_200   only when NIFTY 50 closed above its 200-day average on the reaction day
  nifty_50    only when NIFTY 50 closed above its 50-day average on the reaction day

The improvement bar is the one earnings_drift_variants.py used: return per unit of risk at
least 10% better than base AND mean excess over the market no more than 20% lower.

Usage: python -m tradedesk_lab.earnings_drift_timing --db <copy of data/tradedesk.duckdb>
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "earnings_drift_timing"
REAL_RT = 0.00564


def run(db: Path) -> dict[str, Any]:
    p = eh.load_panels(db)
    ev = edv.events(p)
    closes = p["close"]
    nifty = closes[eh.NIFTY]
    above = {"nifty_200": nifty > nifty.rolling(200).mean(),
             "nifty_50": nifty > nifty.rolling(50).mean()}  # fmt: skip
    turn = (closes * p["volume"]).rolling(20).mean().shift(1)
    liquid = (turn >= eh.MIN_TURNOVER) & (closes.shift(1) >= eh.MIN_PRICE)
    market = closes.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0)
    mkt_index = (1 + market).cumprod()
    rows = []
    for code, i1 in zip(ev["code"], ev["i1"], strict=True):
        trade = edv.trade(p, code, i1, "base")
        if trade is None:
            continue
        day, ret = trade
        ret += eh.DELIVERY_RT - REAL_RT  # re-cost at the real Rs 10,000 round trip
        e, x = i1 + 1, i1 + edv.HOLD
        mkt = float(mkt_index.iloc[x] / mkt_index.iloc[e - 1] - 1)
        rows.append({"day": day, "ret": ret, "excess": ret - mkt,
                     **{k: bool(v.iloc[i1]) for k, v in above.items()}})  # fmt: skip
    t = pd.DataFrame(rows)
    results = {}
    for name in ("base", *above):
        sub = t if name == "base" else t[t[name]]
        years = sub.groupby(pd.to_datetime(sub["day"]).dt.year)["excess"].mean()
        results[name] = {
            "calls": int(len(sub)),
            "mean_return": float(sub["ret"].mean()),
            "mean_excess": float(sub["excess"].mean()),
            "return_per_risk": float(sub["ret"].mean() / sub["ret"].std(ddof=1)),
            "win_rate": float((sub["ret"] > 0).mean()),
            "worst_call": float(sub["ret"].min()),
            "positive_excess_years": f"{int((years > 0).sum())}/{len(years)}",
            "by_year_excess": {str(k): round(float(v), 4) for k, v in years.items()},
        }
    base = results["base"]
    for name in above:
        r = results[name]
        r["improves_on_base"] = bool(
            r["return_per_risk"] >= 1.10 * base["return_per_risk"]
            and r["mean_excess"] >= 0.80 * base["mean_excess"]
        )
    skipped = t[~t["nifty_200"]]
    results["calls_skipped_by_nifty_200"] = {
        "calls": int(len(skipped)),
        "mean_excess": float(skipped["excess"].mean()) if len(skipped) else None,
    }
    report = {"generated_at": datetime.now().astimezone().isoformat(),
              "authority": "research_only", "results": results, "doc": __doc__}  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.db)["results"], indent=1, default=str))


if __name__ == "__main__":
    main()
