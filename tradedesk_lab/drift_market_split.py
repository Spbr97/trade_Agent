"""Earnings drift under a weak market: NIFTY below its 200-day average AND a down day.

Owner's question (2026-10-28): every open drift call is from a reaction made while NIFTY was
below its 200-day average, most of them on a market down day (equal-weight liquid market -1%
or worse that session). Each condition alone was weaker in history (+0.8% vs +2.2% a call;
down days +0.23% vs +0.94% over the market). Is the combination worth real money at all?

Same events, live entry (next open) and live exit (+10% target, else the 20th close) as the
live rule, real 0.564% round trip; excess = net minus the equal-weight liquid market over the
same days. The four cells of (NIFTY above/below 200-day) x (down day or not) are reported,
with per-year means. This splits the live rule's own history (exploratory, like the down-day
mark), so it can only advise what to SKIP with real money - never add a trade.

DECISION RULE (written 2026-10-28 before the run): advise skipping real money on the
below-200-day + down-day group if its mean excess over the market is <= 0.

    python -m tradedesk_lab.drift_market_split
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "drift_market_split"
DOWN_DAY = -0.01


def run() -> dict[str, Any]:
    p = eh.load_panels(dt.DB)
    highs = mr.load(dt.DB)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    opens, hi, closes = p["open"].to_numpy(), highs.to_numpy(), p["close"].to_numpy()
    c = p["close"]
    nifty = c[eh.NIFTY]
    above = (nifty > nifty.rolling(200).mean()).to_numpy()
    turn = (c * p["volume"]).rolling(20).mean().shift(1)
    liquid = (turn >= eh.MIN_TURNOVER) & (c.shift(1) >= eh.MIN_PRICE)
    market = c.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0)
    index = (1 + market).cumprod().to_numpy()
    mkt_day = market.to_numpy()
    col = {code: j for j, code in enumerate(c.columns)}
    ev = edv.events(p)
    rows = []
    for code, i1 in zip(ev["code"], ev["i1"], strict=True):
        j, i1 = col[code], int(i1)
        net = dt.trade(opens, hi, closes, j, opens[i1 + 1, j], i1 + 1, i1 + edv.HOLD)
        if net is None:
            continue
        mkt = index[i1 + edv.HOLD] / index[i1] - 1
        rows.append({
            "year": c.index[i1].year, "net": net, "excess": net - mkt,
            "trend": "above_200" if above[i1] else "below_200",
            "day": "down_day" if mkt_day[i1] <= DOWN_DAY else "other_day",
        })  # fmt: skip
    t = pd.DataFrame(rows)

    def cell(sub: pd.DataFrame) -> dict[str, Any]:
        years = sub.groupby("year")["excess"].mean()
        se = sub["excess"].std(ddof=1) / np.sqrt(len(sub)) if len(sub) > 1 else float("nan")
        return {
            "calls": len(sub), "mean_net": float(sub["net"].mean()),
            "mean_excess": float(sub["excess"].mean()),
            "t_excess": float(sub["excess"].mean() / se) if se and se > 0 else None,
            "win_rate": float((sub["net"] > 0).mean()),
            "net_inr_per_call": round(float(sub["net"].mean()) * 10_000),
            "positive_years": f"{int((years > 0).sum())}/{len(years)}",
        }  # fmt: skip

    cells = {"all": cell(t)}
    for (trend, day), sub in t.groupby(["trend", "day"]):
        cells[f"{trend}+{day}"] = cell(sub)
    weak = cells.get("below_200+down_day", {"mean_excess": None})
    rest = t[~((t["trend"] == "below_200") & (t["day"] == "down_day"))]
    cells["everything_else"] = cell(rest)
    report = {
        "generated_at": datetime.now(IST).isoformat(), "cells": cells,
        "advise_skip_weak_combination": bool(
            weak["mean_excess"] is not None and weak["mean_excess"] <= 0
        ),
    }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
