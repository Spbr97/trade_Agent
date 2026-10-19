"""Liquidity sweeps, a volatility-to-cost filter and multi-day mean reversion (owner, 2026-10-19).

Research only. Reads the lab's 1-minute history (data/m14_m18/intraday_1m/nse_1m.duckdb, the 50
most-traded seasoned NSE stocks, Oct 2024 - Oct 2026) and a COPY of the daily store; writes
data/m14_m18/sweep_and_reversion/. Nothing here alerts, sizes or trades. Rules and pass bars were
fixed before any result was seen.

Intraday, 5-minute bars, signals act at the NEXT bar's open:
  sweep_short  a bar from 09:30 to 13:30 trades above yesterday's high or the 09:15-09:30 high,
               on volume > 1.5x the previous 20 bars, but CLOSES back below that level, which no
               earlier bar today closed above (the first poke). Short at the next open, stop
               0.05% above the poke bar's high, target 2R, out at 15:15. First signal only.
  sweep_long   the mirror below yesterday's low or the opening range low.
  *_filtered   only when the estimated round-trip cost (production EquityCostModel, INTRADAY,
               Rs 500 risk, priced at entry) is <= 3% of the risk - the volatility-to-cost filter.
  Matched random entries, fixed after the first test's flaw: same stock and direction, the SAME
  clock time and the same stop distance as a share of price, on a randomly drawn OTHER session -
  so the null never knows how the signal's day turned out.
  Pass: mean >= +0.05R net, beats random with p < 0.05 / 4, positive in >= 75% of quarters and in
  the last. The same cost filter is also applied to the first test's ORB and VWAP trades
  (tradedesk_lab/intraday_plan_test.py), net mean only, to see whether it rescues them.

Multi-day, daily bars 2017-2026:
  mr_dip       liquid stock (20-session turnover >= Rs 20 crore, price >= Rs 50) above its
               200-day EMA with RSI(2) < 5. Buy the next open; sell at the next open after a
               close above its 5-day average, at most 10 sessions. No stop.
  mr_dip_volcost  the same, only when ATR14 / price >= 5x the delivery round trip (~1.95%).
  Per trade: net of the delivery round trip, minus the equal-weight liquid market over the
  same days, clustered by entry month. Pass: month-clustered t > 2.24 (p < 0.05/2), mean excess
  > 0, positive in >= 60% of years and in both halves.

Result of the first run (2026-10-19). Sweeps FAIL: long -0.37R, short -0.17R net (stops just
beyond the poke bar make the round trip 0.19-0.32R; random timing does as well, p 0.57/0.91);
with the cost filter only 2-4 trades qualify. The filter leaves ORB-short-to-15:15 at +0.09R on
just 36 trades in two years - too few to mean anything. mr_dip PASSES the bar (12,888 trades,
+0.48% net, +0.18% over the market, t 2.41; volcost t 2.66) but is too thin to trade at
Rs 10,000 a call: that size's real delivery round trip is 0.56% (the fixed per-sale charges),
not the 0.39% assumed, and +0.17% more cost erases the excess; doubled costs give t -3.3, and
taking only the 5 deepest dips a day (median 6 signals a day, 29 at the 90th percentile) gives
t 2.03. It would need >= Rs 25,000 a trade (0.36% round trip) - not put live.

Usage: python -m tradedesk_lab.sweep_and_reversion --intraday-db <nse_1m copy> --daily-db <copy>
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

from tradedesk_lab import intraday_plan_test as ip
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "sweep_and_reversion"
COHORTS = 200
SEED = 20261019
MAX_COST_R = 0.03
TARGET_R = 2.0
PASS_MEAN_R = 0.05
INTRADAY_HYPOTHESES = 4
VOLCOST_MULTIPLE = 5.0


def _sessions(b5: pd.DataFrame) -> list[dict[str, Any]]:
    vol20 = b5["volume"].rolling(20).mean().shift(1).to_numpy()
    out = []
    for day, s in b5.groupby("day", sort=True):
        idx = s.index.to_numpy()
        arrays = {k: s[k].to_numpy() for k in ("open", "high", "low", "close", "volume", "start")}
        arrays["vol20"] = vol20[idx]
        arrays["day"] = day
        out.append(arrays)
    return out


def _exit_bar(st: np.ndarray) -> int | None:
    hits = np.flatnonzero(st == ip.minutes("15:10"))
    return int(hits[0]) if len(hits) else None


def _trade_r(s: dict[str, Any], e: int, side: int, stop_pct: float) -> float | None:
    last = _exit_bar(s["start"])
    if last is None or e > last:
        return None
    entry = s["open"][e]
    stop = entry * (1 + stop_pct) if side < 0 else entry * (1 - stop_pct)
    target = entry + side * TARGET_R * abs(entry - stop)
    return ip.resolve(s["open"], s["high"], s["low"], s["close"], e, last, entry, stop, side,
                      target)  # fmt: skip


def _swept(side: int, level: float, h, lo, c, k: int) -> bool:
    """Bar k poked through `level` and closed back on the near side, the first poke today."""

    if side < 0:
        return bool(h[k] > level and c[k] < level and not (c[:k] > level).any())
    return bool(lo[k] < level and c[k] > level and not (c[:k] < level).any())


def sweep_trades(sessions: list[dict[str, Any]], rng: np.random.Generator, cm: Any) -> list[dict]:
    out = []
    for n in range(1, len(sessions)):
        s, prev = sessions[n], sessions[n - 1]
        st, o, h, lo, c, v = (s[k] for k in ("start", "open", "high", "low", "close", "volume"))
        opening = st < ip.minutes("09:30")
        last = _exit_bar(st)
        if opening.sum() != 3 or last is None:
            continue
        ups = [prev["high"].max(), h[opening].max()]
        downs = [prev["low"].min(), lo[opening].min()]
        done = {1: False, -1: False}
        for k in range(len(st)):
            if not (ip.minutes("09:30") <= st[k] <= ip.minutes("13:30")) or k + 1 > last:
                continue
            if not (np.isfinite(s["vol20"][k]) and v[k] > 1.5 * s["vol20"][k]):
                continue
            for side, levels in ((-1, ups), (1, downs)):
                if done[side]:
                    continue
                if not any(_swept(side, lvl, h, lo, c, k) for lvl in levels):
                    continue
                e, entry = k + 1, o[k + 1]
                stop = h[k] * 1.0005 if side < 0 else lo[k] * 0.9995
                if side * (entry - stop) <= 0:
                    continue
                done[side] = True
                stop_pct = abs(entry - stop) / entry
                gross = _trade_r(s, e, side, stop_pct)
                if gross is None:
                    continue
                exit_px = entry + side * gross * abs(entry - stop)
                rand = []
                for m in rng.integers(0, len(sessions), size=COHORTS * 2):
                    if m == n or len(rand) == COHORTS:
                        continue
                    other = sessions[int(m)]
                    same_time = np.flatnonzero(other["start"] == st[e])
                    if not len(same_time):
                        continue
                    r = _trade_r(other, int(same_time[0]), side, stop_pct)
                    if r is not None:
                        rand.append(r)
                out.append({
                    "rule": f"sweep_{'short' if side < 0 else 'long'}", "day": s["day"],
                    "gross": gross, "cost": ip.cost_r(entry, exit_px, stop, side, cm),
                    "cost_at_entry": ip.cost_r(entry, entry, stop, side, cm), "random": rand,
                })  # fmt: skip
    return out


def _evaluate_intraday(rows: list[dict[str, Any]], with_random: bool) -> dict[str, Any]:
    if len(rows) < 30:
        return {"trades": len(rows), "passes": False, "note": "too few trades"}
    gross = np.array([t["gross"] for t in rows])
    net = gross - np.array([t["cost"] for t in rows])
    q = pd.Series(net, index=pd.to_datetime([t["day"] for t in rows])).groupby(
        lambda d: f"{d.year}Q{(d.month - 1) // 3 + 1}"
    ).mean()  # fmt: skip
    res: dict[str, Any] = {
        "trades": len(rows),
        "win_rate": float((net > 0).mean()),
        "mean_gross_r": float(gross.mean()),
        "mean_cost_r": float((gross - net).mean()),
        "mean_net_r": float(net.mean()),
        "positive_quarters": f"{int((q > 0).sum())}/{len(q)}",
        "by_quarter": {k: round(float(v), 3) for k, v in q.items()},
    }
    ok = res["mean_net_r"] >= PASS_MEAN_R and (q > 0).mean() >= 0.75 and q.iloc[-1] > 0
    if with_random:
        depth = min(len(t["random"]) for t in rows)
        rand = np.array([t["random"][:depth] for t in rows])
        cohort_means = rand.mean(axis=0)
        p = float((1 + (cohort_means >= gross.mean()).sum()) / (1 + depth))
        res |= {"random_mean_gross_r": float(cohort_means.mean()),
                "beats_random_by_r": float(gross.mean() - cohort_means.mean()),
                "p_vs_random": p}  # fmt: skip
        ok = ok and p < 0.05 / INTRADAY_HYPOTHESES
    res["passes"] = bool(ok)
    return res


def run_intraday(db: Path) -> dict[str, Any]:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel

    cm = EquityCostModel(load_config(Path(".")).risk.costs)
    rng = np.random.default_rng(SEED)
    con = duckdb.connect(str(db), read_only=True)
    codes = sorted(r[0] for r in con.execute("SELECT DISTINCT scrip_code FROM candles").fetchall())
    con.close()
    sweeps: list[dict[str, Any]] = []
    old: list[dict[str, Any]] = []
    ip.COHORTS = 1  # the first test's own (flawed) random baseline is not used here
    for n, code in enumerate(codes, 1):
        f = ip.load(db, code)
        b5 = ip.bars(f, 5)
        sweeps += sweep_trades(_sessions(b5), rng, cm)
        old += ip.orb_trades(b5, rng, cm) + ip.vwap_trades(ip.bars(f, 3), rng, cm)
        print(f"{n}/{len(codes)} {code}: {len(sweeps)} sweep trades", flush=True)
    results: dict[str, Any] = {}
    for side in ("long", "short"):
        rows = [t for t in sweeps if t["rule"] == f"sweep_{side}"]
        results[f"sweep_{side}"] = _evaluate_intraday(rows, True)
        results[f"sweep_{side}_filtered"] = _evaluate_intraday(
            [t for t in rows if t["cost_at_entry"] <= MAX_COST_R], True
        )
    for rule in ip.RULES:
        rows = [t for t in old if t["rule"] == rule]
        for t in rows:
            t["cost_at_entry"] = t["cost"]  # ORB/VWAP: the round trip priced on the trade
        results[f"{rule}_filtered_net_only"] = _evaluate_intraday(
            [t for t in rows if t["cost_at_entry"] <= MAX_COST_R], False
        )
    return results


def run_multiday(db: Path) -> dict[str, Any]:
    p = mr.load(db)
    close, opens, high, low, vol = (p[k] for k in ("close", "open", "high", "low", "volume"))
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False, min_periods=2).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 2, adjust=False, min_periods=2).mean()
    rsi2 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    sma5 = close.rolling(5).mean()
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()])
    atr = tr.groupby(level=0).max().reindex(close.index).ewm(alpha=1 / 14, adjust=False,
                                                             min_periods=14).mean()  # fmt: skip
    turn20 = (close * vol).rolling(20).mean()
    base = (rsi2 < 5) & (close > ema200) & (turn20 >= mr.MIN_TURNOVER) & (close >= mr.MIN_PRICE)
    liquid = (turn20.shift(1) >= mr.MIN_TURNOVER) & (close.shift(1) >= mr.MIN_PRICE)
    market = close.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0)
    mkt_index = (1 + market).cumprod().to_numpy()
    out = {}
    for name, signal in (
        ("mr_dip", base),
        ("mr_dip_volcost", base & (atr / close >= VOLCOST_MULTIPLE * mr.RT)),
    ):
        S = signal.fillna(False).to_numpy()
        C, OP, M = close.to_numpy(), opens.to_numpy(), sma5.to_numpy()
        days = close.index
        trades = []
        for j in range(C.shape[1]):
            i = 210
            while i < len(days) - 2:
                if not S[i, j]:
                    i += 1
                    continue
                e = i + 1
                entry = OP[e, j]
                if not (np.isfinite(entry) and entry > 0):
                    i += 1
                    continue
                x = min(e + 10, len(days) - 1)
                for k in range(e, min(e + 10, len(days) - 1)):
                    if np.isfinite(C[k, j]) and np.isfinite(M[k, j]) and C[k, j] > M[k, j]:
                        x = k + 1
                        break
                exit_px = OP[x, j] if np.isfinite(OP[x, j]) else C[x - 1, j]
                if np.isfinite(exit_px):
                    net = exit_px / entry - 1 - mr.RT
                    trades.append((days[e], net, net - (mkt_index[x - 1] / mkt_index[e - 1] - 1)))
                i = x
        t = pd.DataFrame(trades, columns=["day", "net", "excess"])
        t = t[t["day"] >= pd.Timestamp("2017-07-01")]
        monthly = t.groupby(t["day"].dt.to_period("M"))["excess"].mean()
        by_year = t.groupby(t["day"].dt.year)["excess"].mean()
        half = len(monthly) // 2
        tstat = float(monthly.mean() / monthly.std(ddof=1) * math.sqrt(len(monthly)))
        res = {
            "trades": int(len(t)),
            "win_rate": float((t["net"] > 0).mean()),
            "mean_net": float(t["net"].mean()),
            "mean_excess": float(t["excess"].mean()),
            "t_month_clustered": tstat,
            "positive_year_share": float((by_year > 0).mean()),
            "first_half_excess": float(monthly.iloc[:half].mean()),
            "second_half_excess": float(monthly.iloc[half:].mean()),
            "by_year": {str(k): round(float(v), 4) for k, v in by_year.items()},
        }
        res["passes"] = bool(
            tstat > mr.T_BAR and res["mean_excess"] > 0 and res["positive_year_share"] >= 0.6
            and res["first_half_excess"] > 0 and res["second_half_excess"] > 0
        )  # fmt: skip
        out[name] = res
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--intraday-db", type=Path, required=True)
    parser.add_argument("--daily-db", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "authority": "research_only",
        "multiday": run_multiday(args.daily_db),
        "intraday": run_intraday(args.intraday_db),
        "doc": __doc__,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    for name in (f"{stamp}.json", "latest.json"):
        (OUT / name).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("multiday", "intraday")}, indent=1, default=str))


if __name__ == "__main__":
    main()
