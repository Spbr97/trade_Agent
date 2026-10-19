"""Low-volatility smart beta on NSE (owner's "portfolio rebalancing" idea, 2026-10-19).

Research only. Reads a COPY of the daily store; writes data/m14_m18/low_volatility/. Fixed before
any result was seen: at each month end, take the 200 NSE stocks with the highest 126-session
average turnover (price >= Rs 50, >= 252 sessions of history, point-in-time) and hold the 30 with
the LOWEST 252-session daily-return volatility, equal weight, for the next month (the method of
NSE's Nifty100 Low Volatility 30). Trades at the next open; costs on every rupee that changes
hands at the real delivery rate for Rs 10,000 positions (0.564% round trip). Benchmark: the
equal-weight 200. Pass, as for dual momentum: monthly excess t > 2.24, positive annual excess,
positive in >= 60% of years and in both halves. Reported alongside: volatility, worst drawdown
and return per unit of risk, since the documented benefit is lower risk for similar return.
Caveat: today's stock list (delisted names missing).

Result of the first run (2026-10-19, 2017-10 to 2026-09): FAILS the excess-return bar (-1.6%/yr
vs the equal-weight 200, t -0.43) but shows the documented benefit - 10.9%/yr vs 11.2%, at
volatility 13.4% vs 20.8% and a worst drawdown of 16% vs 44%; turnover 1.2x a year. A portfolio
choice, not a source of trading calls.
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

from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "low_volatility"
HOLD = 30
HALF_COST = 0.00564 / 2


def run(db: Path) -> dict[str, Any]:
    p = mr.load(db)
    close, opens, vol = p["close"], p["open"], p["volume"]
    ret = close.pct_change(fill_method=None)
    sigma = ret.rolling(252, min_periods=200).std()
    turn126 = (close * vol).rolling(126, min_periods=100).mean()
    history = close.notna().cumsum()
    days = close.index
    month_ends = list(pd.Series(days, index=days).groupby(days.to_period("M")).max())
    strat, bench, turnover = {}, {}, 0.0
    weights: pd.Series | None = None
    for a, b in zip(month_ends[:-1], month_ends[1:], strict=True):
        i = days.get_loc(a)
        if i < 260 or i + 1 >= len(days):
            continue
        ok = (history.iloc[i] >= 252) & (close.iloc[i] >= mr.MIN_PRICE)
        universe = turn126.iloc[i][ok].dropna().nlargest(mr.UNIVERSE).index
        picks = sigma.iloc[i][universe].dropna().nsmallest(HOLD).index
        new = pd.Series(1.0 / len(picks), index=picks)
        traded = (new.reindex(new.index.union(weights.index if weights is not None else []))
                  .fillna(0) - (weights if weights is not None else 0)).abs().sum()  # fmt: skip
        if weights is None:
            traded = 1.0
        turnover += traded
        weights = new
        j0, j1 = i + 1, days.get_loc(b) + 1  # open after this month end -> open after the next
        if j1 >= len(days):
            break

        def leg(codes: pd.Index, w: pd.Series | None, j0: int = j0, j1: int = j1) -> float:
            entry, exit_ = opens.iloc[j0][codes], opens.iloc[j1][codes]
            r = (exit_ / entry - 1).replace([np.inf, -np.inf], np.nan)
            if w is None:
                return float(r.mean())
            return float((r.fillna(0) * w.reindex(codes)).sum())

        strat[days[j0]] = leg(picks, new) - traded * HALF_COST
        bench[days[j0]] = leg(universe, None)
    s, bm = pd.Series(strat), pd.Series(bench)
    excess = s - bm
    n, years = len(excess), len(excess) / 12
    t = float(excess.mean() / excess.std(ddof=1) * math.sqrt(n))
    by_year = excess.groupby(excess.index.year).apply(lambda x: float((1 + x).prod() - 1))
    half = n // 2

    def risk(r: pd.Series) -> dict[str, float]:
        eq = (1 + r).cumprod()
        return {"cagr": float(eq.iloc[-1] ** (1 / years) - 1),
                "volatility": float(r.std(ddof=1) * math.sqrt(12)),
                "max_drawdown": float((1 - eq / eq.cummax()).max()),
                "return_per_risk": float(r.mean() / r.std(ddof=1) * math.sqrt(12))}  # fmt: skip

    result = {
        "months": n, "from": str(s.index[0].date()), "to": str(s.index[-1].date()),
        "low_vol": risk(s), "benchmark": risk(bm),
        "annual_excess": float((1 + excess.mean()) ** 12 - 1), "t": t,
        "positive_year_share": float((by_year > 0).mean()),
        "first_half_excess": float(excess.iloc[:half].mean() * 12),
        "second_half_excess": float(excess.iloc[half:].mean() * 12),
        "annual_turnover": float(turnover / years),
        "by_year_excess": {str(k): round(v, 4) for k, v in by_year.items()},
    }  # fmt: skip
    result["passes"] = bool(
        t > mr.T_BAR and result["annual_excess"] > 0 and result["positive_year_share"] >= 0.6
        and result["first_half_excess"] > 0 and result["second_half_excess"] > 0
    )  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": datetime.now().astimezone().isoformat(),
              "authority": "research_only", "result": result, "doc": __doc__}  # fmt: skip
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    print(json.dumps(run(parser.parse_args(argv).db), indent=1))


if __name__ == "__main__":
    main()
