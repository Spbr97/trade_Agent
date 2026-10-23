"""A Rs 25,000-a-trade dip-buy book (owner, 2026-10-24: "even if I don't put said money you can
assume something"). mr_dip passed its bar in sweep_and_reversion.py but the real 0.56% round
trip of a Rs 10,000 call erases it; this asks whether a book a person could actually run, with
an assumed Rs 2,50,000, makes money after every charge cut at the trade.

Research only. Reads a COPY of the daily store; writes data/m14_m18/dip_book/. Nothing here
alerts, sizes or trades. Rules and pass bars were fixed before any result was seen.

Book: 10 slots of Rs 25,000 (paper capital Rs 2,50,000). Each evening the day's mr_dip signals
(unchanged: 20-session turnover >= Rs 20 crore, price >= Rs 50, close above its 200-day EMA,
RSI(2) < 5) are ranked deepest RSI(2) first and fill the free slots, one position per stock;
buy at the next open, sell at the next open after a close above the 5-day average, at most 10
sessions, no stop. Charges: the real Rs 25,000 delivery round trip (0.358%, production cost
model). Slots stay Rs 25,000 (no compounding), so results are rupees; idle cash earns nothing.
Control: 200 random books holding the SAME slots over the SAME days - each trade swapped for a
random stock that met everything but the RSI(2) dip that evening (liquid, above its 200-day
EMA), bought and sold at the same opens - so only the choice of stock differs.
Pass (from 2017-07): total rupees > 0, beats >= 95% of the random books, positive rupees in
>= 60% of years and in both halves, worst peak-to-trough fall < 25%.
Reported beside it, not judged: the same trades at Rs 10,000 a slot (0.564%).
Caveat: today's stock list (delisted names are missing) flatters long-only results.

Usage: python -m tradedesk_lab.dip_book --daily-db <copy of data/tradedesk.duckdb>
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "dip_book"
SLOTS, MAX_HOLD, RANDOM_BOOKS, SEED = 10, 10, 200, 20261024
SIZES = {"rs_25000": (25_000, 0.0035828), "rs_10000": (10_000, 0.005641)}
JUDGED = "rs_25000"
START = pd.Timestamp("2017-07-01")


def indicators(p: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """sweep_and_reversion.run_multiday's mr_dip, verbatim, plus the random control's pool."""
    close, vol = p["close"], p["volume"]
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False, min_periods=2).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 2, adjust=False, min_periods=2).mean()
    rsi2 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    turn20 = (close * vol).rolling(20).mean()
    pool = (close > ema200) & (turn20 >= mr.MIN_TURNOVER) & (close >= mr.MIN_PRICE)
    sma5 = close.rolling(5).mean()
    return {"rsi2": rsi2, "signal": pool & (rsi2 < 5), "pool": pool, "sma5": sma5}


def exit_index(C: np.ndarray, M: np.ndarray, e: int, j: int) -> int:
    """The open the position sells at: the one after the first close above the 5-day average,
    else MAX_HOLD sessions after entry."""
    last = C.shape[0] - 1
    for k in range(e, min(e + MAX_HOLD, last)):
        if np.isfinite(C[k, j]) and np.isfinite(M[k, j]) and C[k, j] > M[k, j]:
            return k + 1
    return min(e + MAX_HOLD, last)


def _return(OP: np.ndarray, C: np.ndarray, e: int, x: int, j: int) -> float | None:
    entry = OP[e, j]
    exit_px = OP[x, j] if np.isfinite(OP[x, j]) else C[x - 1, j]
    if not (np.isfinite(entry) and entry > 0 and np.isfinite(exit_px)):
        return None
    return float(exit_px / entry - 1)


def run_book(p: dict[str, pd.DataFrame], ind: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """Every trade the 10-slot book takes: signal day i, entry open e, exit open x, stock j."""
    C, OP, M = p["close"].to_numpy(), p["open"].to_numpy(), ind["sma5"].to_numpy()
    S, R = ind["signal"].fillna(False).to_numpy(bool), ind["rsi2"].to_numpy()
    days = p["close"].index
    first = int(days.searchsorted(START))
    held: list[dict[str, Any]] = []
    taken: list[dict[str, Any]] = []
    for i in range(max(first - 1, 0), len(days) - 2):
        e = i + 1
        held = [t for t in held if t["x"] > e]  # sold at this open, slot free again
        free = SLOTS - len(held)
        if free <= 0:
            continue
        cands = np.flatnonzero(S[i])
        if not len(cands):
            continue
        busy = {t["j"] for t in held}
        for j in cands[np.argsort(R[i, cands], kind="stable")]:
            if free <= 0:
                break
            if j in busy:
                continue
            x = exit_index(C, M, e, int(j))
            ret = _return(OP, C, e, x, int(j))
            if ret is None:
                continue
            trade = {"i": i, "e": e, "x": x, "j": int(j), "ret": ret}
            held.append(trade)
            taken.append(trade)
            busy.add(int(j))
            free -= 1
    return taken


def random_totals(
    p: dict[str, pd.DataFrame], ind: dict[str, pd.DataFrame], taken: list[dict[str, Any]],
    slot: float, rt: float, books: int = RANDOM_BOOKS, seed: int = SEED,
) -> list[float]:  # fmt: skip
    """Total rupees of each random book: every trade swapped for a random pool stock that
    evening, bought and sold at the same opens."""
    C, OP = p["close"].to_numpy(), p["open"].to_numpy()
    pool = ind["pool"].fillna(False).to_numpy(bool)
    rng = np.random.default_rng(seed)
    options = {t["i"]: np.flatnonzero(pool[t["i"]]) for t in taken}
    totals = []
    for _ in range(books):
        total = 0.0
        for t in taken:
            choices = options[t["i"]]
            for _try in range(20):
                ret = _return(OP, C, t["e"], t["x"], int(rng.choice(choices)))
                if ret is not None:
                    total += slot * (ret - rt)
                    break
        totals.append(total)
    return totals


def equity_curve(
    p: dict[str, pd.DataFrame], taken: list[dict[str, Any]], slot: float, rt: float
) -> pd.Series:
    """Rupees of profit by day, open positions marked at each close, charges taken at the sale."""
    C, OP = p["close"].to_numpy(), p["open"].to_numpy()
    n = C.shape[0]
    realised, marked = np.zeros(n), np.zeros(n)
    for t in taken:
        e, x, j = t["e"], t["x"], t["j"]
        realised[x] += slot * (t["ret"] - rt)
        for d in range(e, x):
            if np.isfinite(C[d, j]):
                marked[d] += slot * (C[d, j] / OP[e, j] - 1)
    curve = pd.Series(np.cumsum(realised) + marked, index=p["close"].index)
    return curve.loc[START:]


def judge(
    p: dict[str, pd.DataFrame], taken: list[dict[str, Any]], slot: float, rt: float,
    random_book_totals: list[float] | None,
) -> dict[str, Any]:  # fmt: skip
    days = p["close"].index
    pnl = pd.Series(
        [slot * (t["ret"] - rt) for t in taken], index=[days[t["e"]] for t in taken], dtype=float
    )
    capital = SLOTS * slot
    curve = equity_curve(p, taken, slot, rt)
    equity = capital + curve
    by_year = pnl.groupby(pnl.index.year).sum()
    months = pnl.groupby(pnl.index.to_period("M")).sum()
    half = len(months) // 2
    years = max((days[-1] - START).days / 365.25, 1e-9)
    res: dict[str, Any] = {
        "capital_inr": capital,
        "trades": int(len(pnl)),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "avg_inr_per_trade": float(pnl.mean()) if len(pnl) else 0.0,
        "total_inr": float(pnl.sum()),
        "inr_per_year": float(pnl.sum() / years),
        "return_per_year_on_capital": float(pnl.sum() / years / capital),
        "max_drawdown": float((1 - equity / equity.cummax()).max()),
        "positive_year_share": float((by_year > 0).mean()) if len(by_year) else 0.0,
        "first_half_inr": float(months.iloc[:half].sum()),
        "second_half_inr": float(months.iloc[half:].sum()),
        "by_year_inr": {str(k): round(float(v)) for k, v in by_year.items()},
    }
    if random_book_totals is not None:
        beaten = float(np.mean([res["total_inr"] > r for r in random_book_totals]))
        res.update(
            random_books=len(random_book_totals),
            random_median_inr=float(np.median(random_book_totals)),
            beats_random_share=beaten,
        )
        res["passes"] = bool(
            res["total_inr"] > 0 and beaten >= 0.95 and res["positive_year_share"] >= 0.6
            and res["first_half_inr"] > 0 and res["second_half_inr"] > 0
            and res["max_drawdown"] < 0.25
        )  # fmt: skip
    return res


def run(db: Path) -> dict[str, Any]:
    p = mr.load(db)
    ind = indicators(p)
    taken = run_book(p, ind)
    held_days = sum(t["x"] - t["e"] for t in taken)
    sessions = len(p["close"].loc[START:])
    out: dict[str, Any] = {
        "slots_in_use_avg": round(held_days / max(sessions, 1), 2),
        "avg_sessions_held": round(held_days / max(len(taken), 1), 2),
    }
    for name, (slot, rt) in SIZES.items():
        totals = random_totals(p, ind, taken, slot, rt) if name == JUDGED else None
        out[name] = judge(p, taken, slot, rt, totals)
    close = p["close"]
    liquid = (close * p["volume"]).rolling(20).mean().shift(1) >= mr.MIN_TURNOVER
    market = close.pct_change(fill_method=None).where(liquid).mean(axis=1).loc[START:]
    out["equal_weight_market_per_year"] = float(
        (1 + market.fillna(0)).prod() ** (252 / max(len(market), 1)) - 1
    )
    out["passed"] = bool(out[JUDGED].get("passes"))
    out["last_session"] = close.index[-1].date().isoformat()
    return out


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
