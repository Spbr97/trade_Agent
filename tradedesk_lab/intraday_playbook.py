"""Intraday playbook lab: do the strategist plan's intraday setups make money after costs?

Research only (owner-approved stage B, 2026-10-18). Reads the lab's own 15-minute history
(data/m14_m18/intraday_history/nse.duckdb, about a year on ~1,600 NSE stocks) and writes
data/m14_m18/intraday_playbook/. Nothing here alerts, sizes or trades, and no production
file is read for decisions or written.

Rules are fixed BEFORE any result is seen (no parameter search), each tested long and short:

  orb15       First 15-min bar = opening range. Signal: the first bar from 09:30 to 11:15 that
              CLOSES beyond it. Entry next bar's open, stop at the range's other side.
              Range must be 0.3%-2.0% of price; prior close on the trade's side of its EMA50.
  vwap_pb     In an up (down) day - prior close above (below) EMA50 and price above (below)
              the session open - a bar from 10:00 to 13:30 touches VWAP and closes back on the
              trend side with a trend-coloured body. Entry next open, stop beyond that bar.
  pdh_retest  A bar from 09:30 to 11:45 closes beyond the previous day's high (low); a later
              bar up to 13:30 comes back within 0.2% of that level and closes beyond it.
              Entry next open, stop beyond the retest bar and the level.
  first_hour  At 10:15, the five stocks with the strongest (weakest) first-hour move among
              those trading at 1.5x their usual first-hour volume. Entry at the 10:15 bar's
              open, stop at the first-hour low (high).

Every trade: target 2R, exit at the 15:00 bar's close (15:15) if neither is hit, gap-aware,
stop wins a bar that touches both. Costs: the production EquityCostModel at INTRADAY rates
(brokerage, STT, exchange, GST, stamp, slippage) for a Rs 500-risk position.

Market-aligned variants (pre-registered 2026-10-18 after the first run showed every short
making money only on falling days, before any aligned result was seen): the same rules,
taking a long only when the equal-weight average of the liquid stocks is UP from its open at
the signal bar's close, a short only when it is DOWN - information available at entry time.
They count as extra hypotheses in the multiple-testing correction.

Pass bar, also fixed in advance: mean >= +0.05R per trade after costs, beats matched random
entries (same stock, same session, same entry window, stop distance and target) with
p < 0.05 / number of hypotheses, net positive in at least 3 of 4 quarters, and in the last.
Liquidity: 20-session average turnover >= Rs 20 crore, price >= Rs 50.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT

HISTORY = OUTPUT / "intraday_history" / "nse.duckdb"
OUT = OUTPUT / "intraday_playbook"
BARS = 25  # 09:15 ... 15:15 fifteen-minute bars
EXIT_BAR = 23  # the 15:00 bar: exit at its close, 15:15, before square-off
TARGET_R = 2.0
MIN_TURNOVER = 2e8  # Rs 20 crore
MIN_PRICE = 50.0
RISK_RUPEES = 500.0
COHORTS = 200
SEED = 20261018
PASS_MEAN_R = 0.05
RULES = ("orb15", "vwap_pb", "pdh_retest", "first_hour")
VARIANTS = ("all", "market_aligned")
DIRECTIONS = ("long", "short")


def bar(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return (h * 60 + m - (9 * 60 + 15)) // 15


@dataclass
class Session:
    code: str
    day: Any
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    v: np.ndarray
    prev_close: float
    prev_high: float
    prev_low: float
    ema50: float
    turnover20: float
    first_hour_volume_avg: float
    market_return: float = 0.0


def load_codes(codes: Iterable[str]) -> pd.DataFrame:
    con = duckdb.connect(str(HISTORY), read_only=True)
    try:
        frame = con.execute(
            "SELECT code, ts, open, high, low, close, volume FROM m15 "
            "WHERE code IN (SELECT unnest(?)) ORDER BY code, ts",
            [list(codes)],
        ).df()
    finally:
        con.close()
    stamps = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    frame["day"] = stamps.dt.date
    frame["bar"] = (stamps.dt.hour * 60 + stamps.dt.minute - (9 * 60 + 15)) // 15
    return frame[(frame["bar"] >= 0) & (frame["bar"] < BARS)]


def all_codes() -> list[str]:
    con = duckdb.connect(str(HISTORY), read_only=True)
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT code FROM m15 ORDER BY code").fetchall()]
    finally:
        con.close()


def sessions_for(frame: pd.DataFrame) -> list[Session]:
    """Complete sessions only (all 25 bars), with the prior-day context each rule may use -
    every context value is computed from sessions strictly BEFORE the trade's session."""

    out: list[Session] = []
    for code, rows in frame.groupby("code", sort=False):
        full = rows.groupby("day").filter(lambda g: len(g) == BARS and g["bar"].nunique() == BARS)
        if full.empty:
            continue
        days = sorted(full["day"].unique())
        mats = {
            k: full.pivot(index="day", columns="bar", values=k).loc[days].to_numpy()
            for k in ("open", "high", "low", "close", "volume")
        }
        close_d = mats["close"][:, -1]
        high_d = mats["high"].max(axis=1)
        low_d = mats["low"].min(axis=1)
        turnover = (mats["close"] * mats["volume"]).sum(axis=1)
        fh_vol = mats["volume"][:, :4].sum(axis=1)
        ema = pd.Series(close_d).ewm(span=50, adjust=False).mean().to_numpy()
        turn20 = pd.Series(turnover).rolling(20).mean().to_numpy()
        fh20 = pd.Series(fh_vol).rolling(20).mean().to_numpy()
        for i in range(50, len(days)):
            if not (turn20[i - 1] >= MIN_TURNOVER and close_d[i - 1] >= MIN_PRICE):
                continue
            out.append(
                Session(
                    code=str(code),
                    day=days[i],
                    o=mats["open"][i],
                    h=mats["high"][i],
                    l=mats["low"][i],
                    c=mats["close"][i],
                    v=mats["volume"][i],
                    prev_close=float(close_d[i - 1]),
                    prev_high=float(high_d[i - 1]),
                    prev_low=float(low_d[i - 1]),
                    ema50=float(ema[i - 1]),
                    turnover20=float(turn20[i - 1]),
                    first_hour_volume_avg=float(fh20[i - 1]),
                )  # fmt: skip
            )
    return out


def resolve(s: Session, entry_bar: int, entry: float, stop: float, direction: str) -> float | None:
    """Gross R from `entry_bar` (fills at its open) to EXIT_BAR's close. Gap-aware: a bar that
    OPENS beyond the stop fills at its open; a bar touching stop and target counts as the stop."""

    if entry_bar > EXIT_BAR:
        return None
    sign = 1.0 if direction == "long" else -1.0
    risk = (entry - stop) * sign
    if risk <= 0:
        return None
    target = entry + sign * TARGET_R * risk
    for j in range(entry_bar, EXIT_BAR + 1):
        o, h, low = s.o[j], s.h[j], s.l[j]
        if j > entry_bar and (o - stop) * sign <= 0:
            return (o - entry) * sign / risk
        if (low if sign > 0 else h) * sign <= stop * sign:
            return -1.0
        if (h if sign > 0 else low) * sign >= target * sign:
            return TARGET_R
    return (s.c[EXIT_BAR] - entry) * sign / risk


@dataclass
class Trade:
    rule: str
    direction: str
    code: str
    day: Any
    window: tuple[int, int]  # bars the rule could have entered in (for the random benchmark)
    entry_bar: int
    entry: float
    stop: float
    gross_r: float
    net_r: float = 0.0
    random_r: list[float] = field(default_factory=list)
    market_move: float = 0.0  # equal-weight move from the open at the signal bar's close


def _vwap(s: Session) -> np.ndarray:
    typical = (s.h + s.l + s.c) / 3
    vol = np.maximum(s.v, 1e-9)
    return np.cumsum(typical * vol) / np.cumsum(vol)


def signals(s: Session) -> list[tuple[str, str, tuple[int, int], int, float]]:
    """(rule, direction, entry window, entry bar, stop) for every rule firing in session `s`."""

    found = []
    up = s.prev_close > s.ema50
    # orb15
    rng = (s.h[0] - s.l[0]) / s.o[0]
    if 0.003 <= rng <= 0.02:
        for t in range(bar("09:30"), bar("11:15") + 1):
            if up and s.c[t] > s.h[0]:
                found.append(("orb15", "long", (bar("09:45"), bar("11:30")), t + 1, s.l[0]))
                break
            if not up and s.c[t] < s.l[0]:
                found.append(("orb15", "short", (bar("09:45"), bar("11:30")), t + 1, s.h[0]))
                break
    # vwap_pb
    vw = _vwap(s)
    for t in range(bar("10:00"), bar("13:30") + 1):
        if (
            up
            and s.c[t] > s.o[0]
            and s.l[t] <= vw[t] < s.c[t]
            and s.c[t] > s.o[t]
            and s.c[t - 1] > vw[t - 1]
        ):
            found.append(("vwap_pb", "long", (bar("10:15"), bar("13:45")), t + 1, s.l[t] * 0.9995))
            break
        if (
            not up
            and s.c[t] < s.o[0]
            and s.h[t] >= vw[t] > s.c[t]
            and s.c[t] < s.o[t]
            and s.c[t - 1] < vw[t - 1]
        ):
            found.append(("vwap_pb", "short", (bar("10:15"), bar("13:45")), t + 1, s.h[t] * 1.0005))
            break
    # pdh_retest
    for direction, level in (("long", s.prev_high), ("short", s.prev_low)):
        sign = 1.0 if direction == "long" else -1.0
        broke = next(
            (t for t in range(bar("09:30"), bar("11:45") + 1) if (s.c[t] - level) * sign > 0), None
        )
        if broke is None:
            continue
        for t in range(broke + 1, bar("13:30") + 1):
            near = s.l[t] <= level * 1.002 if sign > 0 else s.h[t] >= level * 0.998
            if near and (s.c[t] - level) * sign > 0:
                stop = min(s.l[t], level) * 0.999 if sign > 0 else max(s.h[t], level) * 1.001
                found.append(("pdh_retest", direction, (broke + 2, bar("13:45")), t + 1, stop))
                break
    return found


def cost_r(entry: float, exit_price: float, stop: float, direction: str, cm: Any) -> float:
    from tradedesk.models import TradeType

    risk = abs(entry - stop)
    qty = max(1.0, round(RISK_RUPEES / risk))
    buy, sell = (entry, exit_price) if direction == "long" else (exit_price, entry)
    rt = cm.round_trip_cost(
        trade_type=TradeType.INTRADAY, qty=qty,
        entry_price=Decimal(str(round(buy, 2))), exit_price=Decimal(str(round(sell, 2))),
    )  # fmt: skip
    return float(rt.total) / (qty * risk)


def random_benchmark(t: Trade, s: Session, rng: np.random.Generator) -> list[float]:
    """COHORTS alternate entries for this trade: same stock, session, direction, stop
    distance and target, entry bar drawn uniformly from the rule's entry window."""

    lo, hi = t.window
    hi = min(hi, EXIT_BAR)
    if hi < lo:
        return [t.gross_r] * COHORTS
    dist = abs(t.entry - t.stop)
    out = []
    for b in rng.integers(lo, hi + 1, size=COHORTS):
        entry = float(s.o[b])
        stop = entry - dist if t.direction == "long" else entry + dist
        r = resolve(s, int(b), entry, stop, t.direction)
        out.append(t.gross_r if r is None else r)
    return out


def run(codes: list[str] | None = None, batch: int = 150) -> dict[str, Any]:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel

    cm = EquityCostModel(load_config(Path(".")).risk.costs)
    rng = np.random.default_rng(SEED)
    codes = codes or all_codes()
    trades: list[Trade] = []
    first_hour_rows: list[tuple[Any, str, float, float, float, float]] = []
    session_index: dict[tuple[str, Any], Session] = {}
    day_returns: dict[Any, list[float]] = {}
    bar_moves: dict[Any, np.ndarray] = {}
    bar_counts: dict[Any, int] = {}
    for i in range(0, len(codes), batch):
        for s in sessions_for(load_codes(codes[i : i + batch])):
            day_returns.setdefault(s.day, []).append(s.c[-1] / s.prev_close - 1)
            bar_moves[s.day] = bar_moves.get(s.day, np.zeros(BARS)) + (s.c / s.o[0] - 1)
            bar_counts[s.day] = bar_counts.get(s.day, 0) + 1
            if s.first_hour_volume_avg > 0:
                fh_ret = s.c[bar("10:00")] / s.o[0] - 1
                rel_vol = s.v[:4].sum() / s.first_hour_volume_avg
                first_hour_rows.append(
                    (s.day, s.code, fh_ret, rel_vol, s.l[:4].min(), s.h[:4].max())
                )
            for rule, direction, window, entry_bar, stop in signals(s):
                if entry_bar > EXIT_BAR:
                    continue
                entry = float(s.o[entry_bar])
                r = resolve(s, entry_bar, entry, stop, direction)
                if r is None:
                    continue
                t = Trade(rule, direction, s.code, s.day, window, entry_bar, entry, stop, r)
                t.random_r = random_benchmark(t, s, rng)
                trades.append(t)
            session_index[(s.code, s.day)] = s
        print(
            json.dumps(
                {"codes_done": min(i + batch, len(codes)), "of": len(codes), "trades": len(trades)}
            ),
            flush=True,
        )

    # first_hour: cross-sectional, decided at 10:15 from completed first-hour bars only
    fh = pd.DataFrame(first_hour_rows, columns=["day", "code", "ret", "relvol", "low", "high"])
    fh = fh[fh["relvol"] >= 1.5]
    entry_bar = bar("10:15")
    for day, rows in fh.groupby("day"):
        for direction, picks in (
            ("long", rows.nlargest(5, "ret")),
            ("short", rows.nsmallest(5, "ret")),
        ):
            for _, p in picks.iterrows():
                if (direction == "long" and p["ret"] <= 0) or (
                    direction == "short" and p["ret"] >= 0
                ):
                    continue
                s = session_index[(p["code"], day)]
                stop = float(p["low"]) if direction == "long" else float(p["high"])
                entry = float(s.o[entry_bar])
                r = resolve(s, entry_bar, entry, stop, direction)
                if r is None:
                    continue
                t = Trade(
                    "first_hour",
                    direction,
                    s.code,
                    day,
                    (entry_bar, bar("11:30")),
                    entry_bar,
                    entry,
                    stop,
                    r,
                )
                t.random_r = random_benchmark(t, s, rng)
                trades.append(t)

    for t in trades:
        exit_price = t.entry + t.gross_r * (t.entry - t.stop)
        t.net_r = t.gross_r - cost_r(t.entry, exit_price, t.stop, t.direction, cm)
        t.market_move = float(bar_moves[t.day][t.entry_bar - 1] / bar_counts[t.day])
    market = {d: float(np.mean(v)) for d, v in day_returns.items()}
    return evaluate(trades, market)


def _stats(net: np.ndarray) -> dict[str, Any]:
    if len(net) == 0:
        return {"trades": 0}
    wins, losses = net[net > 0], net[net <= 0]
    equity = np.cumsum(net)
    drawdown = float((np.maximum.accumulate(equity) - equity).max()) if len(equity) else 0.0
    return {
        "trades": int(len(net)),
        "mean_net_r": float(net.mean()),
        "win_rate": float(len(wins) / len(net)),
        "avg_win_r": float(wins.mean()) if len(wins) else None,
        "avg_loss_r": float(losses.mean()) if len(losses) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "max_drawdown_r": drawdown,
        "t_stat": float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net))))
        if len(net) > 1 and net.std() > 0
        else None,
    }


def evaluate(trades: list[Trade], market: dict[Any, float]) -> dict[str, Any]:
    hypotheses = [(r, d, v) for v in VARIANTS for r in RULES for d in DIRECTIONS]
    alpha = 0.05 / len(hypotheses)
    days = sorted({t.day for t in trades})
    quarters = sorted({(d.year, (d.month - 1) // 3 + 1) for d in days})
    results = []
    for rule, direction, variant in hypotheses:

        def aligned(t: Trade, variant: str = variant) -> bool:
            return variant == "all" or (t.market_move > 0) == (t.direction == "long")

        ts = sorted(
            (t for t in trades if t.rule == rule and t.direction == direction and aligned(t)),
            key=lambda t: (t.day, t.entry_bar),
        )
        if not ts:
            results.append(
                {
                    "rule": rule,
                    "direction": direction,
                    "variant": variant,
                    "trades": 0,
                    "passed": False,
                }
            )
            continue
        net = np.array([t.net_r for t in ts])
        gross = np.array([t.gross_r for t in ts])
        cohorts = np.array([t.random_r for t in ts]).mean(axis=0)  # cohort j's mean gross R
        p_random = float(np.mean(cohorts >= gross.mean()))
        by_q = {}
        for q in quarters:
            qnet = np.array([t.net_r for t in ts if (t.day.year, (t.day.month - 1) // 3 + 1) == q])
            by_q[f"{q[0]}Q{q[1]}"] = {
                "trades": int(len(qnet)),
                "mean_net_r": float(qnet.mean()) if len(qnet) else None,
            }
        positive_q = sum(
            1 for v in by_q.values() if v["mean_net_r"] is not None and v["mean_net_r"] > 0
        )
        last_q = by_q[f"{quarters[-1][0]}Q{quarters[-1][1]}"]["mean_net_r"]
        up = np.array([t.net_r for t in ts if market.get(t.day, 0) > 0])
        down = np.array([t.net_r for t in ts if market.get(t.day, 0) <= 0])
        stats = _stats(net)
        passed = bool(
            stats["mean_net_r"] >= PASS_MEAN_R
            and p_random < alpha
            and positive_q >= 3
            and last_q is not None
            and last_q > 0
        )
        results.append({
            "rule": rule, "direction": direction, "variant": variant, **stats,
            "mean_gross_r": float(gross.mean()),
            "random_mean_gross_r": float(cohorts.mean()),
            "beats_random_by_r": float(gross.mean() - cohorts.mean()),
            "p_random": p_random, "alpha": alpha,
            "trades_per_day": float(len(ts) / max(1, len(days))),
            "quarters": by_q, "positive_quarters": positive_q,
            "market_up_days": _stats(up), "market_down_days": _stats(down),
            "passed": passed,
        })  # fmt: skip
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "authority": "research_only",
        "data": {
            "sessions": len(days),
            "first": str(days[0]) if days else None,
            "last": str(days[-1]) if days else None,
        },
        "pass_bar": {
            "mean_net_r": PASS_MEAN_R,
            "p_random_below": alpha,
            "positive_quarters": 3,
            "last_quarter_positive": True,
        },
        "results": results,
        "rules_doc": __doc__,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    (OUT / f"{stamp}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--codes", type=int, default=0, help="first N codes only (smoke test)")
    args = parser.parse_args(argv)
    codes = all_codes()
    report = run(codes[: args.codes] if args.codes else codes)
    for r in report["results"]:
        if r.get("trades"):
            print(
                f"{r['variant']:14s} {r['rule']:11s} {r['direction']:5s} n={r['trades']:6d} net={r['mean_net_r']:+.3f}R "  # noqa: E501
                f"win={r['win_rate']:.0%} vs-random={r['beats_random_by_r']:+.3f}R p={r['p_random']:.3f} "  # noqa: E501
                f"q+={r['positive_quarters']} {'PASS' if r['passed'] else 'fail'}"
            )
        else:
            print(f"{r['variant']:14s} {r['rule']:11s} {r['direction']:5s} no trades")


if __name__ == "__main__":
    main()
