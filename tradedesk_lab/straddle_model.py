"""Modelled history of the 9:20 straddle: were 13 good days luck, and what do bad months look like?

Expired option candles do not exist here, so option_income.py can only paper-trade forward. This
replays the same rule on real 15-minute Nifty and India VIX bars from 2024, pricing options with
Black-Scholes at an implied volatility of k x VIX, where k comes from our own real trades' prices.
Why it might pay: Indian studies find implied volatility above realised on average, a premium for
bearing volatility risk. Why it might not: practitioners report crowding since 2023 and long flat or
losing stretches, and a model has no real order book.

Rule, as live: sell the ATM call and put of the nearest expiry; each leg's own stop at +30% or +40%
of its premium; the rest bought back at 15:15; nothing overnight. Modelled entry is the 09:15 bar's
close, 10 minutes later than live. A leg's worst price in a bar uses that bar's high (call) or low
(put) and that bar's closing VIX; a bar opening past the stop fills at that open. 1 point of
slippage per order, the live cost table, one lot of 65. Expiry: Thursday to 2025-08-31, then
Tuesday, with holiday shifts ignored; a day that is an expiry trades that expiry, as live does.

VERDICT LINES (written 2026-11-07, before any of this was run). The model SUPPORTS the rule only
if, at the calibrated k: 1. mean net a day > 0; 2. each calendar year with 100+ sessions is
positive; 3. the worst fall from a peak in rupees is at most Rs 62,500, 25% of about Rs 2.5 lakh
of margin; 4. the worst day is at most Rs 25,000 lost; and 5. mean net a day is still > 0 at k x
0.9, because the premium is the assumption the result leans on hardest. Each stop is judged apart.
Support is not proof: the forward paper trial's 60 sessions still decide, and a model can only
count against it.

    python -m tradedesk_lab.straddle_model
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import index_bars as ib
from tradedesk_lab import option_income as oi
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "straddle_model"
RATE = 0.06
LOT = oi.LOT
STRIKE_STEP = 50.0
SLIP_POINTS = 1.0
TUESDAY_FROM = date(2025, 9, 1)
EXPIRY_CLOSE = time(15, 30)
ENTRY_BAR, LAST_BAR = 0, 23
MINUTES_A_YEAR = 365.0 * 24 * 60
LIMITS = {"max_fall": 62_500.0, "max_day_loss": 25_000.0, "min_year_sessions": 100, "k_stress": 0.9}


def _cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def price(side: str, spot: float, strike: float, years: float, vol: float) -> float:
    """European Black-Scholes value, or the intrinsic value at expiry."""

    if years <= 0 or vol <= 0:
        return max(spot - strike, 0.0) if side == "ce" else max(strike - spot, 0.0)
    root = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (RATE + 0.5 * vol * vol) * years) / root
    d2 = d1 - root
    disc = math.exp(-RATE * years)
    if side == "ce":
        return spot * _cdf(d1) - strike * disc * _cdf(d2)
    return strike * disc * _cdf(-d2) - spot * _cdf(-d1)


def implied_vol(side: str, value: float, spot: float, strike: float, years: float) -> float | None:
    """Volatility giving this price, or None if no sane one does."""

    low, high = 0.005, 3.0
    if not price(side, spot, strike, years, low) <= value <= price(side, spot, strike, years, high):
        return None
    for _ in range(60):
        mid = (low + high) / 2
        if price(side, spot, strike, years, mid) < value:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def next_expiry(day: date) -> date:
    weekday = 1 if day >= TUESDAY_FROM else 3
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def years_left(day: date, bar: int, expiry: date) -> float:
    """Calendar time from the close of `bar` to the expiry's close, in years."""

    now = datetime.combine(day, time(9, 15)) + timedelta(minutes=15 * (bar + 1))
    end = datetime.combine(expiry, EXPIRY_CLOSE)
    return max((end - now).total_seconds() / 60.0, 0.0) / MINUTES_A_YEAR


@dataclass(frozen=True)
class Session:
    day: date
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    vix: np.ndarray  # closing VIX of each bar, as a fraction


def replay(s: Session, stop_pct: float, k: float, costs: oi.OptionCosts) -> dict[str, Any]:
    """One modelled day of the rule."""

    expiry = next_expiry(s.day)
    spot = float(s.c[ENTRY_BAR])
    strike = round(spot / STRIKE_STEP) * STRIKE_STEP
    start_vol = k * float(s.vix[ENTRY_BAR])
    start_years = years_left(s.day, ENTRY_BAR, expiry)
    gross, fees, stopped = 0.0, 0.0, 0
    worst = 0.0
    entries = {
        x: price(x, spot, strike, start_years, start_vol) - SLIP_POINTS for x in ("ce", "pe")
    }
    exits: dict[str, float] = {}
    for bar in range(ENTRY_BAR + 1, LAST_BAR + 1):
        years = years_left(s.day, bar, expiry)
        vol = k * float(s.vix[bar])
        open_years = years_left(s.day, bar - 1, expiry)
        mark = 0.0
        for side in ("ce", "pe"):
            entry = entries[side]
            if side in exits:
                mark += entry - exits[side]
                continue
            stop = entry * (1 + stop_pct)
            at_open = price(side, float(s.o[bar]), strike, open_years, vol)
            extreme = float(s.h[bar]) if side == "ce" else float(s.l[bar])
            worst_price = price(side, extreme, strike, years, vol)
            if at_open >= stop:
                exits[side] = at_open + SLIP_POINTS
            elif worst_price >= stop:
                exits[side] = stop + SLIP_POINTS
            if side in exits:
                stopped += 1
                mark += entry - exits[side]
            else:
                mark += entry - worst_price
        worst = min(worst, mark * LOT)
    for side in ("ce", "pe"):
        if side not in exits:
            value = price(side, float(s.c[LAST_BAR]), strike, years_left(s.day, LAST_BAR, expiry),
                          k * float(s.vix[LAST_BAR]))  # fmt: skip
            exits[side] = value + SLIP_POINTS
        gross += (entries[side] - exits[side]) * LOT
        fees += costs.trade(entries[side], LOT, "sell") + costs.trade(exits[side], LOT, "buy")
    return {"day": s.day, "net": gross - fees, "stopped": stopped, "worst": worst,
            "premium": sum(entries.values()), "dte": (expiry - s.day).days}  # fmt: skip


def sessions_from(nifty: pd.DataFrame, vix: pd.DataFrame) -> list[Session]:
    """Each complete Nifty session with the last known VIX at every bar's close."""

    vix = vix.assign(key=vix["day"].map(lambda d: d.toordinal()) * 100 + vix["bar"])
    vix = vix.sort_values("key")
    nifty = nifty.assign(key=nifty["day"].map(lambda d: d.toordinal()) * 100 + nifty["bar"])
    joined = pd.merge_asof(nifty.sort_values("key"), vix[["key", "close"]].rename(
        columns={"close": "vix"}), on="key", direction="backward")  # fmt: skip
    out = []
    for day, g in joined.groupby("day"):
        if g["vix"].notna().all():
            out.append(Session(day, g["open"].to_numpy(float), g["high"].to_numpy(float),
                               g["low"].to_numpy(float), g["close"].to_numpy(float),
                               g["vix"].to_numpy(float) / 100.0))  # fmt: skip
    return out


def worst_fall(values: Sequence[float]) -> float:
    """The largest fall from a running peak of cumulative rupees, as a negative number."""

    equity = np.cumsum(values)
    peak = np.maximum.accumulate(np.maximum(equity, 0.0))
    return float((equity - peak).min()) if len(equity) else 0.0


def longest_under_water(values: Sequence[float]) -> int:
    """Most sessions in a row below a previous peak of cumulative rupees."""

    best = run = 0
    peak = total = 0.0
    for v in values:
        total += v
        if total >= peak:
            peak, run = total, 0
        else:
            run += 1
            best = max(best, run)
    return best


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(rows)
    nets = list(frame["net"])
    months = frame.groupby(frame["day"].map(lambda d: d.strftime("%Y-%m")))["net"].sum()
    years = frame.groupby(frame["day"].map(lambda d: d.year))["net"].agg(["count", "sum"])
    t = statistics.fmean(nets) / (statistics.stdev(nets) / math.sqrt(len(nets)))
    worst_days = frame.nsmallest(5, "net")
    return {
        "sessions": len(frame), "mean_net": float(frame["net"].mean()), "t": t,
        "total": float(frame["net"].sum()), "win_rate": float((frame["net"] > 0).mean()),
        "both_stopped_share": float((frame["stopped"] == 2).mean()),
        "worst_day": float(frame["net"].min()), "worst_fall": worst_fall(nets),
        "longest_under_water": longest_under_water(nets),
        "months_positive": float((months > 0).mean()), "worst_month": float(months.min()),
        "by_year": {str(y): {"sessions": int(r["count"]), "net": float(r["sum"])}
                    for y, r in years.iterrows()},
        "by_days_to_expiry": {
            str(k): float(v) for k, v in frame.groupby("dte")["net"].mean().items()
        },
        "worst_days": [(str(d), round(float(n))) for d, n in
                       zip(worst_days["day"], worst_days["net"], strict=True)],
    }  # fmt: skip


def verdict(base: dict[str, Any], stressed: dict[str, Any]) -> dict[str, Any]:
    fails = []
    if not base["mean_net"] > 0:
        fails.append("loses money on average")
    bad = [y for y, r in base["by_year"].items()
           if r["sessions"] >= LIMITS["min_year_sessions"] and r["net"] <= 0]  # fmt: skip
    if bad:
        fails.append(f"losing years {bad}")
    if base["worst_fall"] < -LIMITS["max_fall"]:
        fails.append(f"worst fall Rs {-base['worst_fall']:,.0f}")
    if base["worst_day"] < -LIMITS["max_day_loss"]:
        fails.append(f"worst day Rs {-base['worst_day']:,.0f}")
    if not stressed["mean_net"] > 0:
        fails.append("loses money with the premium 10% thinner")
    return {"supports": not fails, "fails": fails}


def calibrate(ledger: list[dict[str, Any]], sessions: dict[date, Session]) -> dict[str, Any]:
    """k = the real trades' implied vol / VIX, from each straddle's real entry prices."""

    ratios = []
    for row in ledger:
        if row.get("rule") != "straddle_30":
            continue
        day = date.fromisoformat(row["opened_at"][:10])
        s = sessions.get(day)
        if s is None:
            continue
        expiry = date.fromisoformat(row["expiry"])
        years = years_left(day, ENTRY_BAR, expiry)
        vols = []
        for leg in row["legs"]:
            vol = implied_vol(leg["side"], float(leg["entry"]), float(s.c[ENTRY_BAR]),
                              float(leg["strike"]), years)  # fmt: skip
            if vol:
                vols.append(vol)
        if vols:
            ratios.append({"day": str(day), "k": statistics.fmean(vols) / float(s.vix[ENTRY_BAR]),
                           "dte": (expiry - day).days})  # fmt: skip
    return {"k": statistics.median(r["k"] for r in ratios), "days": ratios}


def main() -> None:
    sessions = sessions_from(ib.load(ib.NIFTY), ib.load(ib.VIX))
    by_day = {s.day: s for s in sessions}
    text = oi.LEDGER.read_text(encoding="utf-8")
    ledger = [json.loads(x) for x in text.splitlines() if x.strip()]
    cal = calibrate(ledger, by_day)
    k = cal["k"]
    costs = oi.OptionCosts()
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "limits": LIMITS, "k": k,
        "calibration": cal["days"], "first": str(sessions[0].day), "last": str(sessions[-1].day),
        "rules": {},
    }  # fmt: skip
    real = {
        rule: {row["opened_at"][:10]: float(row["net_inr"]) for row in ledger
               if row.get("rule") == rule and "net_inr" in row}
        for rule in oi.STOPS
    }  # fmt: skip
    print("k", round(k, 3), "sessions", len(sessions), flush=True)
    for rule, stop in oi.STOPS.items():
        rows = [replay(s, stop, k, costs) for s in sessions]
        base = summarise(rows)
        stressed = summarise([replay(s, stop, k * LIMITS["k_stress"], costs) for s in sessions])
        high = summarise([replay(s, stop, k * 1.1, costs) for s in sessions])
        pairs = [(float(r["net"]), real[rule][str(r["day"])]) for r in rows
                 if str(r["day"]) in real[rule]]  # fmt: skip
        check = {
            "days": len(pairs), "model_total": sum(a for a, _ in pairs),
            "real_total": sum(b for _, b in pairs),
            "correlation": (
                float(np.corrcoef(*zip(*pairs, strict=True))[0, 1]) if len(pairs) > 2 else None
            ),
        }  # fmt: skip
        report["rules"][rule] = {
            **verdict(base, stressed), "base": base,
            "k_minus_10pct": {x: stressed[x] for x in ("mean_net", "total", "worst_fall")},
            "k_plus_10pct": {x: high[x] for x in ("mean_net", "total", "worst_fall")},
            "against_real_trades": check,
        }  # fmt: skip
        print(rule, json.dumps(report["rules"][rule], default=str), flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
