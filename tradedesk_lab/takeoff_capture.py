"""Catching 5-7% swings and 8-10% intraday days on results news (owner, 2026-10-19).

Research only; writes data/m14_m18/takeoff_capture/. Nothing here alerts, sizes or trades.
Results news is the one thing in this project's data that has shown a direction after costs
(earnings drift), so both tests build on it. Rules and bars fixed before any result was seen.

A. swing_targets - the live earnings-drift entries (liquid NSE stocks, results jump >= 5% vs the
   market on >= 2x volume, buy the next open) with a profit target: sell the first session whose
   HIGH reaches +5% / +7% / +10% over the entry (at the target, or at the open if it gaps past),
   otherwise at the close of the 20th session as now. Costs: the real Rs 10,000 delivery round
   trip (0.564%). Capital: Rs 1 lakh in ten Rs 10,000 slots, calls taken in the live order while
   a slot is free - a target frees a slot sooner, so more calls fit. Improvement bar: rupees a
   year on Rs 1 lakh >= 1.2x the 20-session rule's AND mean net per call >= +1.0%.

B. results_morning - 15-minute bars (lab history, ~1,600 NSE stocks). On the first session after
   a results date, if the 09:15 bar closes >= 3% above the previous close on >= 3x that stock's
   average 09:15-bar volume (previous 20 sessions), buy at 09:30 (the next bar's open), stop at
   the 09:15 bar's low, out by 15:15. Two variants: hold (no target) and target (+5% over the
   entry, about +8% on the day). Intraday costs (production model, Rs 500 risk). Null: the same
   stock on a randomly drawn OTHER session, entering at 09:30 with the same stop distance (as a
   share of price) and the same exit rule. Pass: mean >= +0.05R net, beats the null with
   p < 0.05/2, positive in >= 75% of calendar quarters and in the last.

Result of the first run (2026-10-19). A: of 1,605 calls 67% reached +5%, 55% +7% and 40% +10%
within 20 sessions, but capping gives up the big winners: hold20 +1.79% net a call (Rs 5,970 a year
on Rs 1 lakh), target5 +0.67% (Rs 6,200), target7 +0.89% (Rs 7,220), target10 +1.19% (Rs 7,880,
+32%, the only one past both bars; +0.83% over the market, 58% win) -> made the live rule.
B: 339 results mornings qualified and 195 of them did reach +8% on the day, but mostly inside the
first 15 minutes; buying at 09:30 lost -0.06R net a trade (39% win, p 0.54-0.60 vs random) - FAIL.

Usage: python -m tradedesk_lab.takeoff_capture --daily-db <copy> --intraday-db <m15 copy>
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import intraday_plan_test as ipt
from tradedesk_lab import intraday_playbook as ip
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "takeoff_capture"
RT = 0.00564
SLOTS, SLOT_INR = 10, 10_000
TARGETS = {"hold20": None, "target5": 0.05, "target7": 0.07, "target10": 0.10}
COHORTS = 200
SEED = 20261019


# --------------------------------------------------------------------------- A. swing targets


def swing_targets(db: Path) -> dict[str, Any]:
    p = eh.load_panels(db)
    ev = edv.events(p)
    highs = mr.load(db)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    opens, closes = p["open"].to_numpy(), p["close"].to_numpy()
    hi = highs.to_numpy()
    col = {c: j for j, c in enumerate(p["close"].columns)}
    dates = p["close"].index
    years = len({d.year for d in dates[ev["i1"].to_numpy() + 1]}) if len(ev) else 1
    out: dict[str, Any] = {}
    for name, target in TARGETS.items():
        trades = []
        for code, i1 in zip(ev["code"], ev["i1"], strict=True):
            j, e = col[code], i1 + 1
            last = i1 + edv.HOLD
            entry = opens[e, j]
            if not (np.isfinite(entry) and entry > 0) or last >= len(dates):
                continue
            exit_px, exit_i, hit = closes[last, j], last, False
            if target is not None:
                level = entry * (1 + target)
                for k in range(e, last + 1):
                    if np.isfinite(hi[k, j]) and hi[k, j] >= level:
                        gap_open = opens[k, j] if k > e and np.isfinite(opens[k, j]) else 0.0
                        exit_px, exit_i, hit = max(level, gap_open), k, True
                        break
            if not np.isfinite(exit_px):
                continue
            trades.append({"e": e, "x": exit_i, "net": exit_px / entry - 1 - RT, "hit": hit,
                           "days": exit_i - e + 1})  # fmt: skip
        t = pd.DataFrame(trades)
        busy: list[int] = []
        taken: list[float] = []
        for e, group in t.groupby("e", sort=True):
            busy = [x for x in busy if x >= e]
            for _, row in group.iterrows():
                if len(busy) < SLOTS:
                    busy.append(int(row["x"]))
                    taken.append(float(row["net"]))
        out[name] = {
            "calls": int(len(t)),
            "mean_net": float(t["net"].mean()),
            "win_rate": float((t["net"] > 0).mean()),
            "hit_target_share": float(t["hit"].mean()) if target else None,
            "median_days_held": float(t["days"].median()),
            "calls_taken_on_1_lakh": len(taken),
            "mean_net_taken": float(np.mean(taken)),
            "rupees_per_year_on_1_lakh": float(np.sum(taken) * SLOT_INR / years),
        }
    base = out["hold20"]
    for name in TARGETS:
        if name != "hold20":
            r = out[name]
            r["improves"] = bool(
                r["rupees_per_year_on_1_lakh"] >= 1.2 * base["rupees_per_year_on_1_lakh"]
                and r["mean_net"] >= 0.01
            )
    return out


# --------------------------------------------------------------------------- B. results morning


def _reaction_days(db: Path) -> dict[str, set[date]]:
    """scrip code -> results dates (the first session after each is a reaction day)."""

    con = duckdb.connect(str(db), read_only=True)
    try:
        symbols = dict(con.execute(
            "SELECT trading_symbol, scrip_code FROM instruments WHERE exch='NSE' AND series='EQ'"
        ).fetchall())  # fmt: skip
        events = con.execute("SELECT DISTINCT symbol, event_date FROM results_events").fetchall()
    finally:
        con.close()
    out: dict[str, set[date]] = defaultdict(set)
    for sym, d in events:
        code = symbols.get(sym)
        if code:
            out[code].add(d if isinstance(d, date) else date.fromisoformat(str(d)))
    return out


def _trade(s: ip.Session, stop_pct: float, target: float | None) -> tuple[float, float] | None:
    entry = float(s.o[1])
    stop = entry * (1 - stop_pct)
    if not (np.isfinite(entry) and entry > 0):
        return None
    tgt = entry * (1 + target) if target else None
    gross = ipt.resolve(s.o, s.h, s.l, s.c, 1, ip.EXIT_BAR, entry, stop, 1, tgt)
    return gross, entry + gross * (entry - stop)


def results_morning(daily_db: Path, cm: Any) -> dict[str, Any]:
    results = _reaction_days(daily_db)
    rng = np.random.default_rng(SEED)
    codes = [c for c in ip.all_codes() if c in results]
    trades: dict[str, list[dict[str, Any]]] = {"hold": [], "target": []}
    signals = big_days = 0
    for start in range(0, len(codes), 150):
        frame = ip.load_codes(codes[start : start + 150])
        by_code: dict[str, list[ip.Session]] = defaultdict(list)
        for s in ip.sessions_for(frame):
            by_code[s.code].append(s)
        for code, sessions in by_code.items():
            days = [s.day for s in sessions]
            reaction = set()
            for d in results[code]:
                nxt = next((x for x in days if x > d), None)
                if nxt is not None:
                    reaction.add(nxt)
            for n, s in enumerate(sessions):
                if s.day not in reaction or n < 10:
                    continue
                first_bar_avg = np.mean([x.v[0] for x in sessions[max(0, n - 20) : n]])
                if not (s.c[0] >= 1.03 * s.prev_close and first_bar_avg > 0
                        and s.v[0] >= 3 * first_bar_avg):  # fmt: skip
                    continue
                entry, stop = float(s.o[1]), float(s.l[0])
                if not (stop < entry):
                    continue
                signals += 1
                big_days += int(s.h.max() >= 1.08 * s.prev_close)
                stop_pct = (entry - stop) / entry
                others = [x for m, x in enumerate(sessions) if m != n]
                for variant, target in (("hold", None), ("target", 0.05)):
                    res = _trade(s, stop_pct, target)
                    if res is None:
                        continue
                    gross, exit_px = res
                    rand = []
                    for m in rng.integers(0, len(others), size=COHORTS):
                        r = _trade(others[int(m)], stop_pct, target)
                        if r is not None:
                            rand.append(r[0])
                    trades[variant].append({
                        "day": s.day, "gross": gross, "random": rand,
                        "cost": ipt.cost_r(entry, exit_px, stop, 1, cm),
                        "hit_target": bool(target and exit_px >= entry * (1 + target) - 1e-9),
                    })  # fmt: skip
    out: dict[str, Any] = {"signals": signals,
                           "days_reaching_plus8_from_prev_close": big_days}  # fmt: skip
    for variant, rows in trades.items():
        if len(rows) < 30:
            out[variant] = {"trades": len(rows), "passes": False, "note": "too few trades"}
            continue
        gross = np.array([r["gross"] for r in rows])
        net = gross - np.array([r["cost"] for r in rows])
        depth = min(len(r["random"]) for r in rows)
        cohort = np.array([r["random"][:depth] for r in rows]).mean(axis=0)
        p = float((1 + (cohort >= gross.mean()).sum()) / (1 + depth))
        q = pd.Series(net, index=pd.to_datetime([r["day"] for r in rows])).groupby(
            lambda d: f"{d.year}Q{(d.month - 1) // 3 + 1}"
        ).mean()  # fmt: skip
        res = {
            "trades": len(rows), "win_rate": float((net > 0).mean()),
            "mean_gross_r": float(gross.mean()), "mean_net_r": float(net.mean()),
            "random_mean_gross_r": float(cohort.mean()), "p_vs_random": p,
            "hit_target_share": float(np.mean([r["hit_target"] for r in rows])),
            "positive_quarters": f"{int((q > 0).sum())}/{len(q)}",
            "by_quarter": {k: round(float(v), 3) for k, v in q.items()},
        }  # fmt: skip
        res["passes"] = bool(
            res["mean_net_r"] >= 0.05 and p < 0.025 and (q > 0).mean() >= 0.75 and q.iloc[-1] > 0
        )
        out[variant] = res
    return out


def main(argv: list[str] | None = None) -> None:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--daily-db", type=Path, required=True)
    parser.add_argument("--intraday-db", type=Path, required=True)
    args = parser.parse_args(argv)
    ip.HISTORY = args.intraday_db
    cm = EquityCostModel(load_config(Path(".")).risk.costs)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "authority": "research_only",
        "swing_targets": swing_targets(args.daily_db),
        "results_morning": results_morning(args.daily_db, cm),
        "doc": __doc__,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "doc"}, indent=1, default=str))


if __name__ == "__main__":
    main()
