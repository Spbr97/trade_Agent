"""Two extensions of the live earnings-drift strategy (owner asked for improvements, 2026-10-19).

Research only. Reads a COPY of the daily store (same events, liquidity rules and prices as
tradedesk_lab/edge_hunt.py); writes data/m14_m18/earnings_extensions/. Costs are the real delivery
round trip of a Rs 10,000 trade (0.564%). Fixed before any result was seen:

1. selection - the live drift calls split into thirds by the size of the results jump (stock vs
   the market) and by the volume surge. Question: when capital allows only some calls, which to
   take? A ranking counts as useful only if its top third beats the bottom third by >= 0.5% net
   per call with a two-sample t > 2. Also simulated: Rs 1 lakh in ten Rs 10,000 slots, calls taken
   first-come versus biggest-jump-first when more arrive than there are free slots.

2. pre_results - the documented run-up into results announcements: buy at the open five sessions
   before the results date, sell at the close of the last session before it (out before the
   announcement), liquid stocks only (20-session turnover >= Rs 20 crore, price >= Rs 50). Board
   meetings for results must be announced in advance (SEBI LODR), so the date is known at entry.
   Excess is over the equal-weight liquid market's open-to-close return over the same days,
   clustered by entry day. Pass: z > 2.24, mean net > 0, positive in >= 70% of years.

Result of the first run (2026-10-19, 1,605 calls). Selection: NOT useful - jump-size thirds
+1.82% / +1.59% / +1.96% net (t 0.19); volume-surge thirds +0.92% / +2.23% / +2.23% (t 1.80, a hint
only). Capital: Rs 1 lakh in ten Rs 10,000 slots takes only 536 of the 1,605 calls; first-come
+1.11% a call (~Rs 5,400 a year), biggest-jump-first +1.35% (~Rs 6,600 a year) - about a 5-7% return
on the Rs 1 lakh, money idle between results seasons. Pre-results run-up FAILS: +0.40% gross,
-0.17% net, +0.03% over the market, z 1.09.

Usage: python -m tradedesk_lab.earnings_extensions --db <copy of data/tradedesk.duckdb>
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab import edge_hunt as eh
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "earnings_extensions"
RT = 0.00564
HOLD = 20
MIN_TURNOVER = 2e8
PRE_SESSIONS = 5


def _liquid(p: dict[str, Any], turnover_floor: float) -> pd.DataFrame:
    closes = p["close"]
    turn = (closes * p["volume"]).rolling(20).mean().shift(1)
    return (turn >= turnover_floor) & (closes.shift(1) >= eh.MIN_PRICE)


def _clustered_z(day: pd.Series, value: pd.Series) -> float:
    daily = value.groupby(day).mean()
    if len(daily) < 3:
        return 0.0
    return float(daily.mean() / (daily.std(ddof=1) / math.sqrt(len(daily))))


def drift_calls(p: dict[str, Any]) -> pd.DataFrame:
    """The live rule's calls with their jump, volume surge and after-cost result."""

    closes, opens, vol = p["close"], p["open"], p["volume"]
    dates = closes.index
    vol20 = vol.rolling(20).mean().shift(1)
    market_ok = _liquid(p, eh.MIN_TURNOVER)
    daily_mkt = closes.pct_change(fill_method=None).where(market_ok).mean(axis=1)
    liquid = _liquid(p, MIN_TURNOVER)
    col = {c: i for i, c in enumerate(closes.columns)}
    rows = []
    for sym, d in p["events"].itertuples(index=False):
        code = p["symbols"].get(sym)
        if code not in col:
            continue
        d = pd.Timestamp(d)
        i1 = dates.searchsorted(d, side="right")
        i0 = dates.searchsorted(d, side="left") - 1
        if i0 < 20 or i1 + HOLD + 1 >= len(dates) or not liquid.iat[i1, col[code]]:
            continue
        j = col[code]
        c0, c1 = closes.iat[i0, j], closes.iat[i1, j]
        wv, v20 = vol.iloc[i0 + 1 : i1 + 1, j].max(), vol20.iat[i1, j]
        if not (np.isfinite(c0) and np.isfinite(c1) and c0 > 0 and np.isfinite(wv) and v20 > 0):
            continue
        mkt = float((1 + daily_mkt.iloc[i0 + 1 : i1 + 1]).prod() - 1)
        jump, volx = c1 / c0 - 1 - mkt, wv / v20
        if jump < 0.05 or volx < 2.0:
            continue
        e, x = i1 + 1, i1 + HOLD
        entry, exit_ = opens.iat[e, j], closes.iat[x, j]
        if not (np.isfinite(entry) and np.isfinite(exit_) and entry > 0):
            continue
        m_open = opens.iloc[e].where(market_ok.iloc[e])
        m_close = closes.iloc[x].where(market_ok.iloc[e])
        net = exit_ / entry - 1 - RT
        rows.append({"code": code, "entry_i": e, "exit_i": x, "day": dates[e], "jump": jump,
                     "volx": volx, "net": net,
                     "excess": net - float((m_close / m_open - 1).mean())})  # fmt: skip
    return pd.DataFrame(rows).drop_duplicates(["code", "entry_i"])


def selection(t: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("jump", "volx"):
        thirds = pd.qcut(t[key], 3, labels=["low", "mid", "high"])
        means = t.groupby(thirds, observed=True)["net"].agg(["mean", "count", "std"])
        hi, lo = t[thirds == "high"]["net"], t[thirds == "low"]["net"]
        diff = hi.mean() - lo.mean()
        se = math.sqrt(hi.var(ddof=1) / len(hi) + lo.var(ddof=1) / len(lo))
        out[key] = {
            "net_by_third": {k: round(float(v), 4) for k, v in means["mean"].items()},
            "calls_by_third": {k: int(v) for k, v in means["count"].items()},
            "top_minus_bottom": float(diff), "t": float(diff / se),
            "useful": bool(diff >= 0.005 and diff / se > 2),
        }  # fmt: skip
    # Rs 1 lakh, ten Rs 10,000 slots: first-come vs biggest-jump-first when slots are short
    for order in ("first_come", "biggest_jump"):
        taken, busy_until, missed = [], [], 0
        for day, group in t.sort_values("entry_i").groupby("entry_i"):
            busy_until = [x for x in busy_until if x >= day]
            group = group.sort_values("jump", ascending=False) if order == "biggest_jump" else group
            for _, call in group.iterrows():
                if len(busy_until) < 10:
                    busy_until.append(call["exit_i"])
                    taken.append(call["net"])
                else:
                    missed += 1
        years = t["day"].dt.year.nunique()
        out[f"capital_{order}"] = {
            "calls_taken": len(taken),
            "calls_missed": missed,
            "mean_net_taken": float(np.mean(taken)),
            "rupees_per_year_on_1_lakh": float(np.sum(taken) * 10_000 / years),
        }
    return out


def pre_results(p: dict[str, Any]) -> dict[str, Any]:
    closes, opens = p["close"], p["open"]
    dates = closes.index
    liquid = _liquid(p, MIN_TURNOVER)
    market_ok = _liquid(p, eh.MIN_TURNOVER)
    col = {c: i for i, c in enumerate(closes.columns)}
    rows = []
    for sym, d in p["events"].drop_duplicates().itertuples(index=False):
        code = p["symbols"].get(sym)
        if code not in col:
            continue
        x = dates.searchsorted(pd.Timestamp(d), side="left") - 1  # last session before the date
        e = x - (PRE_SESSIONS - 1)
        if e < 21 or x >= len(dates):
            continue
        j = col[code]
        if not liquid.iat[e, j]:
            continue
        entry, exit_ = opens.iat[e, j], closes.iat[x, j]
        if not (np.isfinite(entry) and np.isfinite(exit_) and entry > 0):
            continue
        m = float((closes.iloc[x].where(market_ok.iloc[e]) / opens.iloc[e].where(market_ok.iloc[e])
                   - 1).mean())  # fmt: skip
        gross = exit_ / entry - 1
        rows.append({"day": dates[e], "gross": gross, "net": gross - RT, "excess": gross - RT - m})
    t = pd.DataFrame(rows)
    years = t.groupby(t["day"].dt.year)["excess"].mean()
    z = _clustered_z(t["day"], t["excess"])
    res = {
        "trades": int(len(t)), "mean_gross": float(t["gross"].mean()),
        "mean_net": float(t["net"].mean()), "mean_excess": float(t["excess"].mean()),
        "win_rate": float((t["net"] > 0).mean()), "z_day_clustered": z,
        "positive_year_share": float((years > 0).mean()),
        "by_year_excess": {str(k): round(float(v), 4) for k, v in years.items()},
    }  # fmt: skip
    res["passes"] = bool(z > 2.24 and res["mean_net"] > 0 and res["positive_year_share"] >= 0.7)
    return res


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    p = eh.load_panels(args.db)
    calls = drift_calls(p)
    report = {"generated_at": datetime.now().astimezone().isoformat(), "authority": "research_only",
              "drift_calls": int(len(calls)), "selection": selection(calls),
              "pre_results": pre_results(p), "doc": __doc__}  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "doc"}, indent=1, default=str))


if __name__ == "__main__":
    main()
