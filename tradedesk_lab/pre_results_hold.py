"""Buy before results and keep the stock only if the reaction qualifies: does it beat waiting?

Research only; nothing here places an order. Compares, on the same results events and at the
real Rs 10,000 delivery round trip (0.564%), all measured as return minus the equal-weight
liquid market over the same path:

  wait  - the live rule (src/tradedesk/earnings_drift.py): trade only a reaction that rose >= 5%
          more than the market on >= 2x volume (20-session turnover >= Rs 20 crore), buy the next
          open, sell at the 20th close.
  prebuy - buy the open ~9 sessions before the PREDICTED results day (same date + 365 rule as
          literature_checks, no look-ahead) in every liquid stock; at the reaction close keep the
          qualifiers to the same 20th-close exit and sell the rest at the next open.

Pre-registered bar: prebuy must beat wait per rupee of capital-days used, clustered t > 2,
positive in both halves, else the live rule stands.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab.artifacts import OUTPUT, ROOT
from tradedesk_lab.literature_checks import (
    MIN_PRICE,
    MIN_TURNOVER,
    _daily_panel,
    _turnover20,
    clustered_t,
    predicted_date,
)

COST = 0.00564
JUMP, VOLUME_X, QUALIFY_TURNOVER, HOLD = 0.05, 2.0, 2e8, 20
LEAD = 10
OUT_DIR = OUTPUT / "pre_results_hold"


def qualifies(jump_vs_market: float, volume_x: float, turnover: float, price: float) -> bool:
    return (
        jump_vs_market >= JUMP
        and volume_x >= VOLUME_X
        and turnover >= QUALIFY_TURNOVER
        and price >= MIN_PRICE
    )


def run(db: Path) -> dict[str, Any]:
    p = _daily_panel(db)
    code, dn = p["scrip_code"], p["dn"]
    o, c, v = p["open"].astype(float), p["close"].astype(float), p["volume"].astype(float)
    n = len(dn)
    starts = np.r_[0, np.nonzero(code[1:] != code[:-1])[0] + 1, n]
    sid = np.repeat(np.arange(len(starts) - 1), np.diff(starts))
    turn = _turnover20(c, v, starts)
    liq = (turn > MIN_TURNOVER) & (c > MIN_PRICE)
    dates = np.unique(dn)
    di = np.searchsorted(dates, dn)
    same = np.r_[False, sid[1:] == sid[:-1]]
    ret = np.full(n, np.nan)
    ret[same] = c[same] / c[np.nonzero(same)[0] - 1] - 1
    ret[np.abs(ret) > 0.5] = np.nan
    m = liq & np.isfinite(ret)
    k = np.bincount(di[m], minlength=len(dates))
    mkt = np.where(k > 50, np.bincount(di[m], weights=ret[m], minlength=len(dates))
                   / np.maximum(k, 1), np.nan)  # fmt: skip
    lcum = np.r_[0.0, np.cumsum(np.log1p(np.nan_to_num(mkt)))]
    year = dates.astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970

    def market(a: int, b: int) -> float:
        """Market return over the sessions a+1..b (indices into the date grid)."""
        return math.exp(lcum[b + 1] - lcum[a + 1]) - 1

    vol20 = np.full(n, np.nan)
    for s, e in zip(starts[:-1], starts[1:], strict=True):
        cs = np.cumsum(np.r_[0.0, v[s:e]])
        if e - s > 20:
            vol20[s + 20 : e] = (cs[20 : e - s] - cs[: e - s - 20]) / 20

    first = {str(code[s]): (s, e) for s, e in zip(starts[:-1], starts[1:], strict=True)}
    sym_to_code = {
        str(sy): str(cd)
        for cd, sy in zip(code[starts[:-1]], p["trading_symbol"][starts[:-1]], strict=True)
    }
    by_sym: dict[str, list[int]] = defaultdict(list)
    for sy, d in p["events"]:
        by_sym[str(sy)].append(int(np.datetime64(d, "D").astype(int)))

    rows: list[dict[str, float]] = []
    for sy, hist in by_sym.items():
        span = first.get(sym_to_code.get(sy, ""))
        if span is None:
            continue
        s, e = span
        days = dn[s:e]
        for ev in sorted(set(hist)):
            pred = predicted_date(hist, ev)
            if pred is None:
                continue
            i0 = int(np.searchsorted(days, ev)) - 1  # last session before results
            i1 = int(np.searchsorted(days, ev, side="right"))  # first session after
            sig = int(np.searchsorted(days, pred)) - LEAD
            if sig < 253 or i0 < sig + 2 or i1 + HOLD >= len(days) or not liq[s + sig]:
                continue
            t, a0, a1 = s + sig, s + i0, s + i1
            ok = all(np.isfinite([c[a0], c[a1], vol20[a1], turn[a1], o[t + 1], o[a1 + 1]]))
            if not ok or c[a0] <= 0 or vol20[a1] <= 0:
                continue
            win_vol = v[a0 + 1 : a1 + 1].max()
            jump = c[a1] / c[a0] - 1 - market(di[a0], di[a1])
            q = qualifies(jump, win_vol / vol20[a1], turn[a1], c[a0])
            wait_ret = c[a1 + HOLD - 1] / o[a1 + 1] - 1 - market(di[a1], di[a1 + HOLD - 1]) - COST
            runup = c[a0] / o[t + 1] - 1 - market(di[t], di[a0])
            end = a1 + HOLD - 1 if q else a1 + 1
            px = c[end] if q else o[end]
            pre_ret = px / o[t + 1] - 1 - market(di[t], di[end]) - COST
            rows.append({
                "key": int(dn[t]), "year": int(year[di[t]]), "q": float(q), "wait": wait_ret,
                "pre": pre_ret, "runup": runup, "gap": jump,
                "days_pre": float(end - (t + 1)), "days_wait": float(HOLD),
                "big": float(turn[t] >= QUALIFY_TURNOVER),
            })  # fmt: skip
    return summarise(rows)


def _stat(vals: np.ndarray, keys: np.ndarray, years: np.ndarray) -> dict[str, Any]:
    mean, t, days = clustered_t(vals, keys)
    return {
        "mean": mean, "t": t, "days": days, "n": len(vals),
        "halves": [float(vals[years <= 2021].mean()) if (years <= 2021).any() else None,
                   float(vals[years >= 2022].mean()) if (years >= 2022).any() else None],
    }  # fmt: skip


def summarise(rows: list[dict[str, float]]) -> dict[str, Any]:
    if not rows:
        return {"events": 0}
    g = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    q, big = g["q"] == 1, g["big"] == 1
    out: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(),
        "events": len(rows),
        "qualify_share": float(q.mean()),
        "qualify_share_big_universe": float(q[big].mean()) if big.any() else None,
    }
    # Pre-registered comparison on the Rs 20 crore universe the live rule trades.
    for name, sel in (("big_universe", big), ("all_liquid", np.ones(len(rows), bool))):
        wait = np.where(q[sel], g["wait"][sel], 0.0)  # no trade, no cost on non-qualifiers
        pre = g["pre"][sel]
        cap_wait = np.where(q[sel], g["days_wait"][sel], 0.0)
        out[name] = {
            "wait_per_event": _stat(wait, g["key"][sel], g["year"][sel]),
            "prebuy_per_event": _stat(pre, g["key"][sel], g["year"][sel]),
            "prebuy_minus_wait": _stat(pre - wait, g["key"][sel], g["year"][sel]),
            "capital_days_per_event": {
                "wait": float(cap_wait.mean()), "prebuy": float(g["days_pre"][sel].mean())
            },
        }
    out["on_qualifiers"] = {
        "runup_before_results": float(g["runup"][q].mean()),
        "runup_before_results_non_qualifiers": float(g["runup"][~q].mean()),
        "prebuy_minus_wait": _stat((g["pre"] - g["wait"])[q], g["key"][q], g["year"][q]),
    }
    nq = ~q
    out["on_non_qualifiers"] = {
        "prebuy_net": _stat(g["pre"][nq], g["key"][nq], g["year"][nq]),
        "reaction_p5": float(np.quantile(g["gap"][nq], 0.05)),
        "reaction_p50": float(np.quantile(g["gap"][nq], 0.5)),
    }
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "tradedesk.duckdb")
    a = ap.parse_args(argv)
    report = run(a.db)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "latest.json"
    out.write_text(json.dumps(report, indent=2, default=float))
    print(json.dumps(report, indent=2, default=lambda x: round(float(x), 4)))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
