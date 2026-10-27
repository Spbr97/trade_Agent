"""New listings: is there a buyable edge in a stock's first weeks after it lists?

Question (owner, 2026-10-28, after ZETWERK rose 10%+ while invisible to the scans): with
new listings now in the instrument list every day, should the agent call them?

A listing is a stock's first daily candle on NSE (EQ series) from 2017 on - our history starts
2016-09-14, so earlier first candles are where the data begins, not listings - excluding any
day on which more than 20 stocks first appear (a bulk history start, not real listings). It
includes SME migrations and demergers, which list the same way.

Rules (a call is made after a session's close and bought at the NEXT open, as drift calls are):
  debut         every listing, signalled on its listing day;
  strong_debut  the listing day closed 5%+ above its own open (buyers kept paying up);
  listing_high  within its first 20 sessions, the first close above every earlier high since
                listing (the "IPO base" breakout).
Filters: signal-day close >= Rs 50 and that day's traded value >= Rs 5 crore. Exits: the close
20 and 60 sessions after the signal. Costs: the real delivery round trip of a Rs 10,000 call;
0.05% slippage each way. Excess = net minus the equal-weight liquid market over the same days.

PASS BAR (written 2026-10-28, before any result was seen). A variant passes only if all hold:
  1. at least 100 calls;
  2. mean net return per call > 0 after costs;
  3. mean excess > 0 with t >= 3.0, one entry WEEK as one unit (listings come in waves);
  4. positive mean excess in both halves (split at the median signal date).
3 rules x 2 exits = 6 variants; t >= 3 is stricter than Bonferroni for six. Research only.

Caveat: the store holds today's listings only, so listings that later delisted are missing -
this flatters every long test here, so a pass needs to be clear, and a fail is a fail.

    python -m tradedesk_lab.new_listings
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab.artifacts import OUTPUT, ROOT

DB = ROOT / "data" / "tradedesk.duckdb"
OUT_DIR = OUTPUT / "new_listings"
FIRST_LISTING = date(2017, 1, 1)
BULK_START = 20
MIN_PRICE = 50.0
MIN_TURNOVER = 5e7
STRONG_DEBUT = 0.05
HIGH_WINDOW = 20
HOLDS = (20, 60)
SLIPPAGE = 0.0005
NOTIONAL = 10_000.0
RULES = ("debut", "strong_debut", "listing_high")
PASS = {"min_calls": 100, "min_t": 3.0}


@dataclass(frozen=True)
class Panels:
    open: pd.DataFrame  # day x code
    high: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame


def market(p: Panels) -> pd.Series:
    """Equal-weight daily return of liquid stocks (20-session traded value >= Rs 5 crore and
    previous close >= Rs 50), as earnings_drift measures the market."""
    turnover = (p.close * p.volume).rolling(20).mean().shift(1)
    ok = (turnover >= MIN_TURNOVER) & (p.close.shift(1) >= MIN_PRICE)
    return p.close.pct_change(fill_method=None).where(ok).clip(-0.5, 0.5).mean(axis=1)


def listings(p: Panels) -> dict[str, int]:
    """code -> row index of its first candle, for real listings only: not on the first day of
    the data (where history begins), not on a bulk-start day, not before FIRST_LISTING."""
    first = p.close.apply(lambda s: s.first_valid_index())
    first = first.dropna()
    counts = first.value_counts()
    bulk = set(counts[counts > BULK_START].index) | {p.close.index[0]}
    rows = {d: i for i, d in enumerate(p.close.index)}
    return {
        code: rows[d] for code, d in first.items()
        if d >= FIRST_LISTING and d not in bulk
    }  # fmt: skip


def signals(p: Panels, listed: Mapping[str, int], rule: str) -> list[tuple[str, int]]:
    out = []
    for code, i0 in listed.items():
        o, h, c, v = (p.open[code].to_numpy(), p.high[code].to_numpy(),
                      p.close[code].to_numpy(), p.volume[code].to_numpy())  # fmt: skip
        if rule in ("debut", "strong_debut"):
            hit = i0
            if rule == "strong_debut" and not c[i0] >= o[i0] * (1 + STRONG_DEBUT):
                continue
        else:
            hit = None
            for i in range(i0 + 1, min(i0 + HIGH_WINDOW + 1, len(c))):
                if math.isfinite(c[i]) and c[i] > max(h[i0:i]):
                    hit = i
                    break
            if hit is None:
                continue
        if not (c[hit] >= MIN_PRICE and c[hit] * v[hit] >= MIN_TURNOVER):
            continue
        out.append((code, hit))
    return out


def outcome(p: Panels, mkt: pd.Series, code: str, i: int, hold: int, cost: Any) -> dict | None:
    if i + hold >= len(p.close.index):
        return None
    entry = float(p.open[code].iat[i + 1]) * (1 + SLIPPAGE)
    exit_px = float(p.close[code].iat[i + hold]) * (1 - SLIPPAGE)
    if not (math.isfinite(entry) and math.isfinite(exit_px) and entry > 0):
        return None
    net = exit_px / entry - 1 - cost(entry, exit_px)
    m = float((1 + mkt.iloc[i + 1 : i + hold + 1].fillna(0.0)).prod() - 1)
    day = p.close.index[i]
    return {"code": code, "day": day, "net": net, "excess": net - m}


def week_t(calls: pd.DataFrame) -> tuple[float, float, int]:
    """(mean of weekly means, t over weeks, weeks): one entry week is one piece of evidence."""
    weeks = calls.groupby(calls["day"].map(lambda d: d.isocalendar()[:2]))["excess"].mean()
    n = len(weeks)
    if n < 2:
        return (float(weeks.mean()) if n else float("nan"), float("nan"), n)
    sd = float(weeks.std(ddof=1))
    mean = float(weeks.mean())
    return mean, (mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")), n


def judge(calls: pd.DataFrame) -> dict[str, Any]:
    if calls.empty:
        return {"calls": 0, "passed": False, "fails": ["no calls"]}
    mean_x, t_x, weeks = week_t(calls)
    days = sorted(calls["day"])
    cut = days[len(days) // 2]
    halves = [week_t(calls[calls["day"] < cut])[0], week_t(calls[calls["day"] >= cut])[0]]
    out = {
        "calls": len(calls), "weeks": weeks, "win_rate": float((calls["net"] > 0).mean()),
        "mean_net": float(calls["net"].mean()), "median_net": float(calls["net"].median()),
        "mean_excess_week": mean_x, "t_excess": t_x, "halves_excess": halves,
        "first": str(days[0]), "last": str(days[-1]),
    }  # fmt: skip
    fails = []
    if len(calls) < PASS["min_calls"]:
        fails.append(f"only {len(calls)} calls")
    if not out["mean_net"] > 0:
        fails.append("loses money after costs")
    if not (mean_x > 0 and t_x >= PASS["min_t"]):
        fails.append(f"excess t {t_x:.2f} < {PASS['min_t']}")
    if not all(h > 0 for h in halves):
        fails.append("not positive in both halves")
    out["passed"] = not fails
    out["fails"] = fails
    return out


def cost_function() -> Any:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    model = EquityCostModel(load_config(ROOT).risk.costs)

    def cost(entry: float, exit_px: float) -> float:
        qty = max(1, int(NOTIONAL // entry))
        rt = model.round_trip_cost(
            trade_type=TradeType.DELIVERY, qty=qty, entry_price=Decimal(str(round(entry, 2))),
            exit_price=Decimal(str(round(exit_px, 2))),
        )  # fmt: skip
        return float(rt.total) / (qty * entry)

    return cost


def load(db=DB) -> Panels:  # noqa: ANN001
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        frame = con.execute(
            "SELECT c.scrip_code, c.ts, c.open, c.high, c.close, c.volume FROM candles c "
            "JOIN instruments i ON i.scrip_code = c.scrip_code "
            "WHERE c.interval = '1day' AND i.exch = 'NSE' AND i.series = 'EQ'"
        ).df()
    finally:
        con.close()
    frame["day"] = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    piv = {
        k: frame.pivot_table(index="day", columns="scrip_code", values=k, aggfunc="last")
        .sort_index()
        for k in ("open", "high", "close", "volume")
    }
    return Panels(piv["open"], piv["high"], piv["close"], piv["volume"])


def main() -> None:
    p = load()
    mkt = market(p)
    listed = listings(p)
    cost = cost_function()
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "listings": len(listed),
        "pass_bar": PASS, "variants": {},
    }  # fmt: skip
    for rule in RULES:
        sigs = signals(p, listed, rule)
        for hold in HOLDS:
            rows = [o for code, i in sigs if (o := outcome(p, mkt, code, i, hold, cost))]
            calls = pd.DataFrame(rows, columns=["code", "day", "net", "excess"])
            name = f"{rule}_{hold}"
            report["variants"][name] = judge(calls)
            print(name, json.dumps(report["variants"][name], default=str))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
