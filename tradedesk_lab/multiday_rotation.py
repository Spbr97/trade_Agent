"""Multi-day ideas from the owner's algo plan (2026-10-19), tested on ten years of NSE history.

Research only. Reads a COPY of the production daily store; writes data/m14_m18/multiday_rotation/.
Nothing here alerts, sizes or trades. Rules and pass bars were fixed before any result was seen.

1. dual_momentum - the plan's cross-sectional rotation. Universe at each month end: the 200 NSE
   stocks with the highest 126-session average turnover, price >= Rs 50, >= 252 sessions of
   history (point-in-time, from stored candles). Score: cross-sectional z of (63-session return
   / 63-session volatility) plus z of the same over 126 sessions (the Nifty200 Momentum 30
   method). Ten equal slots, long only. Signals on a close, trades at the next open.
     spec     month end: fill empty slots with the best-ranked names above their 200-day EMA
              and equalise slot sizes; any day: sell a holding that falls out of the top 25 or
              closes below its 200-day EMA, and refill that slot with the best-ranked name
              (top 25, above its EMA).
     monthly  month end only: hold the top 10, nothing in between (the index-fund version).
   Costs: half the production delivery round trip on every rupee bought and on every rupee
   sold. Benchmark: the equal-weight universe. Pass: monthly excess t > 2.24 (p < 0.05/2),
   positive annual excess, positive in >= 60% of years and in both halves of the period.

2. donchian55 - a close above the highest high of the previous 55 sessions, liquid stocks
   (20-session turnover >= Rs 20 crore, price >= Rs 50). Buy the next open; exit at the next
   open after a close below (highest high since entry - 3 x ATR14), at most 250 sessions.
     base     the breakout alone
     atr      plus fresh volatility: ATR14 above its own 50-session average
   Per trade: net return after the delivery round trip minus the equal-weight liquid market
   over the same days, clustered by entry month. Pass: month-clustered t > 2.24, mean excess
   > 0, positive in >= 60% of years; "atr" counts as an improvement only if it beats "base".

Caveat: the stock list is today's (delisted names are missing), which flatters long-only
results; the excess over a same-universe benchmark partly controls for it. A pass still needs
live confirmation before it alerts.

Usage: python -m tradedesk_lab.multiday_rotation --db <copy of data/tradedesk.duckdb>
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "multiday_rotation"
RT = 0.0039098  # production delivery round trip
HALF = RT / 2
SLOTS, KEEP_RANK, UNIVERSE = 10, 25, 200
T_BAR = 2.24
MIN_TURNOVER, MIN_PRICE = 2e8, 50.0


def load(db: Path) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        frame = con.execute(
            "SELECT c.scrip_code, c.ts, c.open, c.high, c.low, c.close, c.volume FROM candles c "
            "JOIN instruments i ON i.scrip_code = c.scrip_code "
            "WHERE c.interval = '1day' AND i.series = 'EQ' AND c.scrip_code LIKE 'NSE_%'"
        ).df()
    finally:
        con.close()
    frame["day"] = (
        pd.to_datetime(frame["ts"], unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.normalize()
        .dt.tz_localize(None)
    )
    return {
        k: frame.pivot_table(index="day", columns="scrip_code", values=k, aggfunc="last")
        .sort_index()
        .astype(float)
        for k in ("open", "high", "low", "close", "volume")
    }


def _monthly_stats(strategy: pd.Series, bench: pd.Series) -> dict[str, Any]:
    s = (1 + strategy).groupby(strategy.index.to_period("M")).prod() - 1
    b = (1 + bench).groupby(bench.index.to_period("M")).prod() - 1
    excess = (s - b).dropna()
    n, years = len(excess), len(excess) / 12
    t = float(excess.mean() / excess.std(ddof=1) * math.sqrt(n)) if n > 2 else 0.0
    equity = (1 + strategy).cumprod()
    by_year = excess.groupby(excess.index.year).apply(lambda x: float((1 + x).prod() - 1))
    half = n // 2
    result = {
        "months": n,
        "from": str(excess.index[0]),
        "to": str(excess.index[-1]),
        "strategy_cagr": float(equity.iloc[-1] ** (1 / years) - 1),
        "benchmark_cagr": float((1 + bench).prod() ** (1 / years) - 1),
        "annual_excess": float((1 + excess.mean()) ** 12 - 1),
        "t": t,
        "max_drawdown": float((1 - equity / equity.cummax()).max()),
        "positive_year_share": float((by_year > 0).mean()),
        "first_half_excess": float(excess.iloc[:half].mean() * 12),
        "second_half_excess": float(excess.iloc[half:].mean() * 12),
        "by_year": {str(k): round(v, 4) for k, v in by_year.items()},
    }
    result["passes"] = bool(
        t > T_BAR
        and result["annual_excess"] > 0
        and result["positive_year_share"] >= 0.6
        and result["first_half_excess"] > 0
        and result["second_half_excess"] > 0
    )
    return result


def dual_momentum(p: dict[str, pd.DataFrame], variant: str) -> dict[str, Any]:
    close, opens, vol = p["close"], p["open"], p["volume"]
    ret = close.pct_change(fill_method=None)
    parts = [
        (close / close.shift(n) - 1) / (ret.rolling(n).std() * math.sqrt(252)) for n in (63, 126)
    ]
    turn126 = (close * vol).rolling(126, min_periods=100).mean()
    history = close.notna().cumsum()
    ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    days = close.index
    month_ends = set(pd.Series(days, index=days).groupby(days.to_period("M")).max())
    C, OP = close.to_numpy(), opens.to_numpy()
    col = {c: j for j, c in enumerate(close.columns)}

    # slot = [column or -1, rupee value, price the value was last marked at]
    slots: list[list[float]] = [[-1, 1.0 / SLOTS, 0.0] for _ in range(SLOTS)]
    universe: list[str] = []
    equity: dict[Any, float] = {}
    bench: dict[Any, float] = {}
    turnover = 0.0
    for i in range(260, len(days) - 1):
        # 1) mark to today's close. The equity curve is the total value at each close, so
        # yesterday's trades (costs, the overnight gap on a sale) land in today's return.
        for s in slots:
            if s[0] >= 0 and np.isfinite(C[i, int(s[0])]) and s[2] > 0:
                s[1] *= C[i, int(s[0])] / s[2]
                s[2] = C[i, int(s[0])]
        if universe:
            equity[days[i]] = sum(s[1] for s in slots)
            u = [col[c] for c in universe]
            r = C[i, u] / C[i - 1, u] - 1
            bench[days[i]] = float(np.nanmean(r)) if np.isfinite(r).any() else 0.0

        # 2) decide on this close
        rebalance = days[i] in month_ends
        if rebalance:
            ok = (history.iloc[i] >= 252) & (close.iloc[i] >= MIN_PRICE)
            universe = list(turn126.iloc[i][ok].dropna().nlargest(UNIVERSE).index)
        if not universe:
            continue
        z = sum((x.iloc[i][universe] - x.iloc[i][universe].mean()) / x.iloc[i][universe].std()
                for x in parts)  # fmt: skip
        order = list(z.dropna().sort_values(ascending=False).index)
        rank = {c: k for k, c in enumerate(order)}
        above = (close.iloc[i] > ema200.iloc[i]).to_dict()
        inv = {j: c for c, j in col.items()}
        held = {inv[int(s[0])] for s in slots if s[0] >= 0}
        if variant == "monthly":
            if not rebalance:
                continue
            target = order[:SLOTS]
            sells = [k for k, s in enumerate(slots) if s[0] >= 0 and inv[int(s[0])] not in target]
            buys_from = [c for c in target if c not in held]
        else:
            sells = [
                k for k, s in enumerate(slots)
                if s[0] >= 0 and (rank.get(inv[int(s[0])], 999) >= KEEP_RANK
                                  or not above.get(inv[int(s[0])], False))
            ]  # fmt: skip
            limit = SLOTS if rebalance else KEEP_RANK
            buys_from = [c for c in order[:limit] if c not in held and above.get(c, False)]
            if not rebalance:
                buys_from = buys_from if sells else []

        # 3) execute at the next open
        j1 = i + 1
        for k in sells:
            s = slots[k]
            px = OP[j1, int(s[0])]
            if np.isfinite(px) and s[2] > 0:
                s[1] *= px / s[2]
            turnover += s[1]
            s[1] -= s[1] * HALF
            s[0], s[2] = -1, 0.0
        if rebalance:
            total = sum(s[1] for s in slots)
            for s in slots:  # equalise, paying on the rupees moved
                if s[0] >= 0 and np.isfinite(OP[j1, int(s[0])]) and s[2] > 0:
                    s[1] *= OP[j1, int(s[0])] / s[2]
                    s[2] = OP[j1, int(s[0])]
            total = sum(s[1] for s in slots)
            for s in slots:
                moved = abs(total / SLOTS - s[1])
                turnover += moved if s[0] >= 0 else 0.0
                s[1] = total / SLOTS - (moved * HALF if s[0] >= 0 else 0.0)
        empty = [s for s in slots if s[0] < 0]
        for s, c in zip(empty, [c for c in buys_from if np.isfinite(OP[j1, col[c]])], strict=False):
            turnover += s[1]
            s[1] -= s[1] * HALF
            s[0], s[2] = col[c], OP[j1, col[c]]

    strategy = pd.Series(equity).pct_change().dropna()
    benchmark = pd.Series(bench).reindex(strategy.index).fillna(0.0)
    out = _monthly_stats(strategy, benchmark)
    out["annual_turnover"] = float(turnover / (len(strategy) / 252))
    return out


def donchian55(p: dict[str, pd.DataFrame], variant: str) -> dict[str, Any]:
    close, opens, high, low, vol = (p[k] for k in ("close", "open", "high", "low", "volume"))
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()]
    ).groupby(level=0).max()  # fmt: skip
    tr = tr.reindex(close.index)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    channel = high.rolling(55).max().shift(1)
    turn20 = (close * vol).rolling(20).mean()
    signal = (close > channel) & (turn20 >= MIN_TURNOVER) & (close >= MIN_PRICE)
    if variant == "atr":
        signal &= atr > atr.rolling(50).mean()
    liquid = (turn20.shift(1) >= MIN_TURNOVER) & (close.shift(1) >= MIN_PRICE)
    market = close.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0)
    market_index = (1 + market).cumprod().to_numpy()
    C, OP, H, A, S = (x.to_numpy() for x in (close, opens, high, atr, signal.fillna(False)))
    days = close.index
    trades = []
    for j in range(C.shape[1]):
        i = 60
        while i < len(days) - 2:
            if not S[i, j]:
                i += 1
                continue
            e = i + 1
            entry = OP[e, j]
            if not np.isfinite(entry) or entry <= 0:
                i += 1
                continue
            hh, x = H[e, j], None
            for k in range(e, min(e + 250, len(days) - 1)):
                if np.isfinite(H[k, j]):
                    hh = max(hh, H[k, j])
                if np.isfinite(C[k, j]) and np.isfinite(A[k, j]) and C[k, j] < hh - 3 * A[k, j]:
                    x = k + 1
                    break
            if x is None:
                x = min(e + 250, len(days) - 1)
            exit_px = OP[x, j] if np.isfinite(OP[x, j]) else C[x - 1, j]
            if np.isfinite(exit_px):
                net = exit_px / entry - 1 - RT
                mkt = market_index[x - 1] / market_index[e - 1] - 1
                trades.append((days[e], net, net - mkt, x - e))
            i = x  # no new entry while this one is open
    t = pd.DataFrame(trades, columns=["day", "net", "excess", "held"])
    monthly = t.groupby(t["day"].dt.to_period("M"))["excess"].mean()
    by_year = t.groupby(t["day"].dt.year)["excess"].mean()
    tstat = float(monthly.mean() / monthly.std(ddof=1) * math.sqrt(len(monthly)))
    result = {
        "trades": int(len(t)),
        "mean_net": float(t["net"].mean()),
        "mean_excess": float(t["excess"].mean()),
        "median_hold_sessions": float(t["held"].median()),
        "win_rate": float((t["net"] > 0).mean()),
        "t_month_clustered": tstat,
        "positive_year_share": float((by_year > 0).mean()),
        "by_year": {str(k): round(float(v), 4) for k, v in by_year.items()},
    }
    result["passes"] = bool(
        tstat > T_BAR and result["mean_excess"] > 0 and result["positive_year_share"] >= 0.6
    )
    return result


def run(db: Path) -> dict[str, Any]:
    p = load(db)
    results = {
        "dual_momentum": {v: dual_momentum(p, v) for v in ("spec", "monthly")},
        "donchian55": {v: donchian55(p, v) for v in ("base", "atr")},
    }
    d = results["donchian55"]
    d["atr"]["improves_on_base"] = bool(d["atr"]["mean_excess"] > d["base"]["mean_excess"])
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "authority": "research_only",
        "data_to": str(p["close"].index[-1].date()),
        "results": results,
        "doc": __doc__,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    for name in (f"{stamp}.json", "latest.json"):
        (OUT / name).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.db)
    print(json.dumps({k: v for k, v in report.items() if k != "doc"}, indent=1, default=str))


if __name__ == "__main__":
    main()
