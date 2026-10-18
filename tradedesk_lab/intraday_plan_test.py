"""The owner's intraday algo plan (2026-10-19), tested exactly as written on 1-minute history.

Research only. Reads data/m14_m18/intraday_1m/nse_1m.duckdb (two years of 1-minute candles for
the 50 most-traded seasoned NSE stocks, fetched 2026-10-19) and writes
data/m14_m18/intraday_plan_test/. Nothing here alerts, sizes or trades. Rules fixed before any
result was seen; signals act at the NEXT bar's open, never the signal bar's own close.

  orb       5-minute bars. Opening range = 09:15-09:30 high/low. Signal: a 5-minute bar that
            CLOSES beyond the range with volume > 1.5x the previous 20 five-minute bars. First
            signal of the session only. Stop: the range midpoint. No profit target.
              orb_1130  square off at 11:30 (the plan's text; signals 09:30-11:25)
              orb_1515  square off at 15:15 (the plan's code; signals 09:30-12:55)
  vwap_mr   3-minute bars. Session VWAP from 09:15 with volume-weighted standard-deviation
            bands. Long: close below VWAP - 1.5 SD and RSI14 < 30; short: above VWAP + 1.5 SD
            and RSI14 > 70 (signals 09:30-14:30). Stop 0.3% beyond the signal bar's low/high.
            Target: price back at VWAP. Square off at 15:15. A new signal only after an exit.

Every trade is gap-aware (an open beyond a stop or target fills at the open; a bar touching
both counts as the stop), costed with the production EquityCostModel at INTRADAY rates for a
Rs 500-risk position. Pass bar, the same as tradedesk_lab/intraday_playbook.py: mean >= +0.05R
per trade after costs, beats matched random entries (same stock, session, direction, stop
distance and exit rule; entry bar drawn from the rule's window) with p < 0.05 / 6, net positive
in >= 75% of quarters and in the last.

Result of the first run (2026-10-19, 50 stocks, Oct 2024 - Oct 2026): no rule passes. Net per
trade: orb_1130 long -0.04R / short -0.04R, orb_1515 long -0.02R / short +0.02R (7/9 quarters
positive, under the +0.05R bar), vwap_mr long -0.18R / short -0.14R (tight 0.3% stops make the
round trip ~0.13R). Caveat found while reading the result: for ORB the matched-random entries
are drawn from the whole window, including bars BEFORE the breakout on a day already known to
break out, so the random baseline is flattered (+0.2R) - the ORB verdict rests on the net-mean
bar alone, which fails without it. About 1% of stock-weeks were missing from the API (one empty
7-day window each); those sessions are simply absent.

Usage: python -m tradedesk_lab.intraday_plan_test --db <copy of nse_1m.duckdb>
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "intraday_plan_test"
RISK_RUPEES = 500.0
COHORTS = 200
SEED = 20261019
PASS_MEAN_R = 0.05
HYPOTHESES = 6
RULES = ("orb_1130_long", "orb_1130_short", "orb_1515_long", "orb_1515_short",
         "vwap_mr_long", "vwap_mr_short")  # fmt: skip


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def load(db: Path, code: str) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    try:
        f = con.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE scrip_code = ? AND interval = '1minute' ORDER BY ts",
            [code],
        ).df()
    finally:
        con.close()
    t = pd.to_datetime(f["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    f["day"] = t.dt.date
    f["minute"] = t.dt.hour * 60 + t.dt.minute
    return f[(f["minute"] >= minutes("09:15")) & (f["minute"] < minutes("15:30"))]


def bars(f: pd.DataFrame, size: int) -> pd.DataFrame:
    """1-minute -> `size`-minute bars anchored at 09:15 (the exchange convention)."""

    g = f.assign(bucket=(f["minute"] - minutes("09:15")) // size)
    out = g.groupby(["day", "bucket"]).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).reset_index()  # fmt: skip
    out["start"] = minutes("09:15") + out["bucket"] * size
    return out


def resolve(o, h, low, c, e: int, last: int, entry: float, stop: float, side: int,
            target: np.ndarray | float | None = None) -> float:  # fmt: skip
    """Gross R from entry at bar e's open to the first stop/target, else bar `last`'s close.
    A bar touching both counts as the stop; an open beyond either fills at the open."""

    risk = abs(entry - stop)
    hs, ls, os_ = h[e : last + 1], low[e : last + 1], o[e : last + 1]
    if isinstance(target, np.ndarray):
        tg = target[e : last + 1]
    else:
        tg = np.full(len(hs), np.nan if target is None else float(target))
    if side > 0:
        stop_hit, tgt_hit = ls <= stop, np.isfinite(tg) & (hs >= tg)
    else:
        stop_hit, tgt_hit = hs >= stop, np.isfinite(tg) & (ls <= tg)
    ks = int(np.argmax(stop_hit)) if stop_hit.any() else None
    kt = int(np.argmax(tgt_hit)) if tgt_hit.any() else None
    if ks is not None and (kt is None or ks <= kt):
        px = stop if ks == 0 else (min(stop, os_[ks]) if side > 0 else max(stop, os_[ks]))
        return side * (px - entry) / risk
    if kt is not None:
        level = tg[kt]
        if kt == 0:
            px = max(level, entry) if side > 0 else min(level, entry)
        else:
            px = max(level, os_[kt]) if side > 0 else min(level, os_[kt])
        return side * (px - entry) / risk
    return side * (c[last] - entry) / risk


def cost_r(entry: float, exit_price: float, stop: float, side: int, cm: Any) -> float:
    from tradedesk.models import TradeType

    risk = abs(entry - stop)
    qty = max(1.0, round(RISK_RUPEES / risk))
    buy, sell = (entry, exit_price) if side > 0 else (exit_price, entry)
    rt = cm.round_trip_cost(
        trade_type=TradeType.INTRADAY, qty=qty,
        entry_price=Decimal(str(round(buy, 2))), exit_price=Decimal(str(round(max(sell, 0.05), 2))),
    )  # fmt: skip
    return float(rt.total) / (qty * risk)


def orb_trades(b5: pd.DataFrame, rng: np.random.Generator, cm: Any) -> list[dict[str, Any]]:
    out = []
    vol20 = b5["volume"].rolling(20).mean().shift(1).to_numpy()
    for day, s in b5.groupby("day", sort=True):
        idx = s.index.to_numpy()
        st = s["start"].to_numpy()
        o, h, low, c, v = (s[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
        rng_bars = st < minutes("09:30")
        if rng_bars.sum() != 3:
            continue
        orh, orl = h[rng_bars].max(), low[rng_bars].min()
        mid = (orh + orl) / 2
        for name, last_signal, exit_start in (("orb_1130", "11:25", "11:25"),
                                              ("orb_1515", "12:55", "15:10")):  # fmt: skip
            window = [k for k in range(len(st))
                      if minutes("09:30") <= st[k] <= minutes(last_signal)]  # fmt: skip
            exits = np.flatnonzero(st == minutes(exit_start))
            if not window or not len(exits):
                continue
            last = int(exits[0])
            for k in window:
                vk = vol20[idx[k]]
                if not (np.isfinite(vk) and v[k] > 1.5 * vk) or k + 1 > last:
                    continue
                side = 1 if c[k] > orh else -1 if c[k] < orl else 0
                if side == 0:
                    continue
                e, entry = k + 1, o[k + 1]
                if side * (entry - mid) <= 0:
                    break  # already past the stop at the open: no trade this session
                gross = resolve(o, h, low, c, e, last, entry, mid, side)
                exit_px = entry + side * gross * abs(entry - mid)
                dist = abs(entry - mid)
                lo_w, hi_w = window[0] + 1, min(window[-1] + 1, last)
                rand = []
                for rb in rng.integers(lo_w, hi_w + 1, size=COHORTS):
                    re = o[rb]
                    rand.append(resolve(o, h, low, c, int(rb), last, re, re - side * dist, side))
                out.append({"rule": f"{name}_{'long' if side > 0 else 'short'}", "day": day,
                            "gross": gross, "cost": cost_r(entry, exit_px, mid, side, cm),
                            "random": rand})  # fmt: skip
                break
    return out


def _rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=np.nan)
    gain = pd.Series(np.where(delta > 0, delta, 0.0))
    loss = pd.Series(np.where(delta < 0, -delta, 0.0))
    ag = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return (100 - 100 / (1 + ag / al.replace(0, np.nan))).to_numpy()


def vwap_trades(b3: pd.DataFrame, rng: np.random.Generator, cm: Any) -> list[dict[str, Any]]:
    out = []
    rsi_all = _rsi(b3["close"].to_numpy())
    for day, s in b3.groupby("day", sort=True):
        idx = s.index.to_numpy()
        st = s["start"].to_numpy()
        o, h, low, c, v = (s[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
        tp = (h + low + c) / 3
        cv = np.cumsum(v)
        if cv[-1] <= 0:
            continue
        vwap = np.cumsum(tp * v) / np.where(cv > 0, cv, np.nan)
        sd = np.sqrt(np.maximum(np.cumsum(v * tp**2) / np.where(cv > 0, cv, np.nan) - vwap**2, 0))
        # the target for bar k is the VWAP known at bar k-1's close (no look-ahead inside a bar)
        target = np.concatenate([[np.nan], vwap[:-1]])
        exits = np.flatnonzero(st == minutes("15:12"))
        if not len(exits):
            continue
        last = int(exits[0])
        window = [k for k in range(len(st)) if minutes("09:30") <= st[k] <= minutes("14:30")]
        if not window:
            continue
        k = window[0]
        while k <= window[-1] and k + 1 <= last:
            r = rsi_all[idx[k]]
            side = 0
            if np.isfinite(sd[k]) and sd[k] > 0 and np.isfinite(r):
                if c[k] < vwap[k] - 1.5 * sd[k] and r < 30:
                    side = 1
                elif c[k] > vwap[k] + 1.5 * sd[k] and r > 70:
                    side = -1
            if side == 0:
                k += 1
                continue
            e, entry = k + 1, o[k + 1]
            stop = low[k] * (1 - 0.003) if side > 0 else h[k] * (1 + 0.003)
            if side * (entry - stop) <= 0 or side * (target[e] - entry) <= 0:
                k += 1
                continue
            gross = resolve(o, h, low, c, e, last, entry, stop, side, target)
            dist = abs(entry - stop)
            exit_px = entry + side * gross * dist
            rand = []
            for rb in rng.integers(window[0] + 1, min(window[-1] + 1, last) + 1, size=COHORTS):
                re = o[rb]
                rand.append(resolve(o, h, low, c, int(rb), last, re, re - side * dist, side,
                                    target))  # fmt: skip
            out.append({"rule": f"vwap_mr_{'long' if side > 0 else 'short'}", "day": day,
                        "gross": gross, "cost": cost_r(entry, exit_px, stop, side, cm),
                        "random": rand})  # fmt: skip
            # next signal only after this trade is over: skip to the bar after its exit
            k = e + 1
            for j in range(e, last + 1):
                hit_stop = low[j] <= stop if side > 0 else h[j] >= stop
                hit_tgt = (h[j] >= target[j]) if side > 0 else (low[j] <= target[j])
                if hit_stop or (np.isfinite(target[j]) and hit_tgt):
                    k = j + 1
                    break
            else:
                k = last + 1
    return out


def evaluate(trades: list[dict[str, Any]]) -> dict[str, Any]:
    results = {}
    for rule in RULES:
        rows = [t for t in trades if t["rule"] == rule]
        if len(rows) < 30:
            results[rule] = {"trades": len(rows), "passes": False, "note": "too few trades"}
            continue
        gross = np.array([t["gross"] for t in rows])
        net = gross - np.array([t["cost"] for t in rows])
        rand = np.array([t["random"] for t in rows])  # trades x cohorts
        cohort_means = rand.mean(axis=0)
        p = float((1 + (cohort_means >= gross.mean()).sum()) / (1 + rand.shape[1]))
        q = pd.Series(net, index=pd.to_datetime([t["day"] for t in rows])).groupby(
            lambda d: f"{d.year}Q{(d.month - 1) // 3 + 1}"
        ).mean()  # fmt: skip
        res = {
            "trades": len(rows),
            "win_rate": float((net > 0).mean()),
            "mean_gross_r": float(gross.mean()),
            "mean_cost_r": float((gross - net).mean()),
            "mean_net_r": float(net.mean()),
            "random_mean_gross_r": float(cohort_means.mean()),
            "beats_random_by_r": float(gross.mean() - cohort_means.mean()),
            "p_vs_random": p,
            "positive_quarters": f"{int((q > 0).sum())}/{len(q)}",
            "by_quarter": {k: round(float(v), 3) for k, v in q.items()},
        }
        res["passes"] = bool(
            res["mean_net_r"] >= PASS_MEAN_R
            and p < 0.05 / HYPOTHESES
            and (q > 0).mean() >= 0.75
            and q.iloc[-1] > 0
        )
        results[rule] = res
    return results


def run(db: Path) -> dict[str, Any]:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel

    cm = EquityCostModel(load_config(Path(".")).risk.costs)
    rng = np.random.default_rng(SEED)
    con = duckdb.connect(str(db), read_only=True)
    codes = [r[0] for r in con.execute("SELECT DISTINCT scrip_code FROM candles").fetchall()]
    con.close()
    trades: list[dict[str, Any]] = []
    for n, code in enumerate(sorted(codes), 1):
        f = load(db, code)
        trades += orb_trades(bars(f, 5), rng, cm)
        trades += vwap_trades(bars(f, 3), rng, cm)
        print(f"{n}/{len(codes)} {code}: {len(trades)} trades so far", flush=True)
    results = evaluate(trades)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "authority": "research_only",
        "stocks": len(codes),
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
    print(json.dumps(report["results"], indent=1, default=str))


if __name__ == "__main__":
    main()
