"""Down-day leaders (owner, 2026-10-24): on 23 Oct nearly every call lost while some stocks
rose. Do stocks that rise while the market falls go on beating it?

Research only. Reads a COPY of the daily store; writes data/m14_m18/down_day_leaders/. Nothing
here alerts, sizes or trades. Rules and pass bars were fixed before any result was seen.

Liquid: 20-session turnover >= Rs 20 crore and price >= Rs 50 at the previous close (as
multiday_rotation). Down day: the equal-weight liquid market fell 1% or more, close to close.
Leader: a liquid stock that closed UP 1% or more on a down day, on >= 1.5x its previous
20-session average volume.
Variants: all_leaders, and top5 (each down day's 5 biggest gains among its leaders).
Buy at the next open, sell at the open `hold` sessions later (10 or 20), one trade per stock at
a time. Net of the real Rs 10,000 delivery round trip (0.564%, production cost model); excess =
net minus the equal-weight liquid market from the same open to the same open (what a random
liquid pick earns). Also reported at Rs 25,000 a trade (0.358%), not judged.
Pass (4 tests, from 2017-07): month-clustered t of the excess > 2.50 (p < 0.05/4 one-sided),
mean net > 0 and mean excess > 0, positive excess in >= 60% of years and in both halves.
Caveat: today's stock list (delisted names are missing) flatters long-only results.

Usage: python -m tradedesk_lab.down_day_leaders --daily-db <copy of data/tradedesk.duckdb>
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

OUT = OUTPUT / "down_day_leaders"
RT_10K, RT_25K = 0.005641, 0.0035828  # production delivery round trip at Rs 10,000 / 25,000
MARKET_DOWN, LEADER_UP, VOLUME_X, TOP = -0.01, 0.01, 1.5, 5
HOLDS = (10, 20)
T_BAR = 2.50
START = pd.Timestamp("2017-07-01")


def signals(p: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Each variant's signal frame (known at that day's close), the liquid mask and the
    equal-weight market's daily return."""
    close, vol = p["close"], p["volume"]
    turn20 = (close * vol).rolling(20).mean()
    liquid = (turn20.shift(1) >= mr.MIN_TURNOVER) & (close.shift(1) >= mr.MIN_PRICE)
    day_ret = close.pct_change(fill_method=None)
    market = day_ret.where(liquid).mean(axis=1)
    vol_x = vol / vol.rolling(20).mean().shift(1)
    down = np.broadcast_to((market <= MARKET_DOWN).to_numpy()[:, None], close.shape)
    lead = liquid & (day_ret >= LEADER_UP) & (vol_x >= VOLUME_X) & down
    rank = day_ret.where(lead).rank(axis=1, ascending=False, method="first")
    return {
        "all_leaders": lead,
        "top5": lead & (rank <= TOP),
        "liquid": liquid,
        "market": market,
        "day_ret": day_ret,
    }


def trades(
    signal: pd.DataFrame, p: dict[str, pd.DataFrame], liquid: pd.DataFrame, hold: int
) -> pd.DataFrame:
    """One row per trade: entry day, the stock's gross open-to-open return and the liquid
    market's over the same opens."""
    S = signal.fillna(False).to_numpy(bool)
    OP, C, L = p["open"].to_numpy(), p["close"].to_numpy(), liquid.to_numpy(bool)
    days, n = signal.index, len(signal.index)
    market: dict[tuple[int, int], float] = {}
    rows = []
    for j in range(S.shape[1]):
        free_from = 0
        for i in np.flatnonzero(S[:, j]):
            e, x = i + 1, i + 1 + hold
            if i < free_from:
                continue
            if x >= n:
                break
            entry, exit_px = OP[e, j], OP[x, j]
            if not np.isfinite(exit_px):
                exit_px = C[x - 1, j]
            if not (np.isfinite(entry) and entry > 0 and np.isfinite(exit_px)):
                continue
            if (e, x) not in market:
                moved = OP[x] / OP[e] - 1
                ok = L[e] & np.isfinite(moved)
                market[(e, x)] = float(moved[ok].mean()) if ok.any() else 0.0
            rows.append((days[e], signal.columns[j], exit_px / entry - 1, market[(e, x)]))
            free_from = x
    return pd.DataFrame(rows, columns=["day", "code", "gross", "market"])


def judge(t: pd.DataFrame, rt: float) -> dict[str, Any]:
    t = t[t["day"] >= START]
    if len(t) < 2:
        return {"trades": int(len(t)), "passes": False}
    net = t["gross"] - rt
    excess = net - t["market"]
    month = t["day"].dt.to_period("M")
    monthly = excess.groupby(month).mean()
    by_year = excess.groupby(t["day"].dt.year).mean()
    half = len(monthly) // 2
    sd = monthly.std(ddof=1)
    t_stat = float(monthly.mean() / sd * math.sqrt(len(monthly))) if sd > 0 else 0.0
    res = {
        "trades": int(len(t)),
        "months": int(len(monthly)),
        "win_rate": float((net > 0).mean()),
        "mean_net": float(net.mean()),
        "mean_market": float(t["market"].mean()),
        "mean_excess": float(excess.mean()),
        "beat_market_rate": float((excess > 0).mean()),
        "t_month_clustered": t_stat,
        "positive_year_share": float((by_year > 0).mean()),
        "first_half_excess": float(monthly.iloc[:half].mean()),
        "second_half_excess": float(monthly.iloc[half:].mean()),
        "by_year_excess": {str(k): round(float(v), 4) for k, v in by_year.items()},
    }
    res["passes"] = bool(
        res["t_month_clustered"] > T_BAR and res["mean_net"] > 0 and res["mean_excess"] > 0
        and res["positive_year_share"] >= 0.6
        and res["first_half_excess"] > 0 and res["second_half_excess"] > 0
    )  # fmt: skip
    return res


def latest_day(sig: dict[str, Any], symbols: dict[str, str]) -> dict[str, Any]:
    """The newest down day's leaders, for the owner to see what the rule picks."""
    down = sig["market"][sig["market"] <= MARKET_DOWN]
    if down.empty:
        return {}
    day = down.index[-1]
    row = sig["all_leaders"].loc[day]
    names = row[row].index
    gains = sig["day_ret"].loc[day, names].sort_values(ascending=False)
    return {
        "day": day.date().isoformat(),
        "market": float(sig["market"].loc[day]),
        "leaders": [
            {"symbol": symbols.get(code, code), "day_gain": round(float(g), 4)}
            for code, g in gains.items()
        ],
    }


def run(db: Path) -> dict[str, Any]:
    p = mr.load(db)
    sig = signals(p)
    out: dict[str, Any] = {}
    for name in ("all_leaders", "top5"):
        for hold in HOLDS:
            t = trades(sig[name], p, sig["liquid"], hold)
            out[f"{name}_hold{hold}"] = {
                "rs_10000": judge(t, RT_10K),
                "rs_25000_not_judged": {
                    k: v for k, v in judge(t, RT_25K).items() if k in ("mean_net", "mean_excess")
                },
            }
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        symbols = {code: sym for sym, code in con.execute(
            "SELECT trading_symbol, scrip_code FROM instruments WHERE exch='NSE' AND series='EQ'"
        ).fetchall()}  # fmt: skip
    finally:
        con.close()
    down_days = int((sig["market"].loc[START:] <= MARKET_DOWN).sum())
    return {
        "tests": out,
        "any_passed": any(v["rs_10000"]["passes"] for v in out.values()),
        "down_days_since_start": down_days,
        "latest_down_day": latest_day(sig, symbols),
        "last_session": p["close"].index[-1].date().isoformat(),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--daily-db", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"generated_at": datetime.now().astimezone().isoformat(), **run(args.daily_db)}
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, default=str)
    (OUT / f"{datetime.now():%Y%m%dT%H%M%S}.json").write_text(text, encoding="utf-8")
    (OUT / "latest.json").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
