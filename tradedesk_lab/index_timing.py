"""Simple Nifty timing from published research: intraday momentum and the overnight effect.

Internet research, owner's request 2026-11-07. Gao, Han, Li and Zhou found the S&P 500's first
half hour predicts its last half hour, and a later study found the same in 12 of 16 markets; no
study found tests the Nifty. Popular articles claim the Nifty's whole return comes overnight and
its day session loses, which is unverified, and our own results-short work saw the market slide
from its open. Both are cheap to test on index futures, which the owner can trade at far lower
charges than stocks.

Data: 15-minute Nifty bars from 2024 (tradedesk_lab/index_bars.py) and daily bars from 2016. Index
prices stand in for the future. Costs from edge_hunt: 0.04% each way, and for a night held long one
night of its 5.2% yearly carry.

Rules:
  momentum       the sign of 09:15-09:45's return, traded from the 15:00 bar's open to the 15:15
                 bar's close, in the last half hour;
  momentum_big   the same, only when that first half hour's move is in the top third of the previous
                 60 sessions' moves: Gao finds predictability rises with volatility;
  overnight      long from each close to the next open, every night;
  day_short      short from each open to that close, every session;
  overnight_low  the overnight rule only when India VIX closed under its own 250-session median.

PASS BAR (written 2026-11-07, before any of these ran). A rule passes only if: at least 200 trades;
mean net > 0 after costs; t >= 3.0 on net, one trade as one unit; positive in both halves; positive
in at least 60% of calendar years with 20+ trades (momentum's 3 years count as they come). A pass
becomes a proposal for forward paper trades. 5 rules; t >= 3 is well past Bonferroni's bar.

    python -m tradedesk_lab.index_timing
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import index_bars as ib
from tradedesk_lab import positioning as ps
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "index_timing"
FIRST_BARS = (0, 1)  # 09:15 and 09:30
LAST_OPEN_BAR, LAST_CLOSE_BAR = 23, 24  # 15:00 and 15:15
BIG_WINDOW = 60
VIX_WINDOW = 250
PASS = {"min_trades": 200, "min_t": 3.0, "min_year_share": 0.6, "min_year_trades": 20}
RT_COST = 2 * eh.FUT_COST
NIGHT_CARRY = eh.CARRY / 252


def intraday_days(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per session: the first half hour's return and the last half hour's."""

    rows = []
    for day, g in bars.groupby("day"):
        by_bar = g.set_index("bar")
        first = by_bar.at[FIRST_BARS[1], "close"] / by_bar.at[FIRST_BARS[0], "open"] - 1
        last = by_bar.at[LAST_CLOSE_BAR, "close"] / by_bar.at[LAST_OPEN_BAR, "open"] - 1
        rows.append({"day": day, "first": first, "last": last})
    return pd.DataFrame(rows)


def momentum_trades(days: pd.DataFrame, big_only: bool = False) -> pd.DataFrame:
    frame = days[days["first"] != 0].copy()
    if big_only:
        size = days["first"].abs()
        floor = size.shift(1).rolling(BIG_WINDOW).quantile(2 / 3)
        keep = size >= floor
        frame = frame[keep.reindex(frame.index).fillna(False).astype(bool)]
    side = frame["first"].map(lambda x: 1 if x > 0 else -1)
    return pd.DataFrame({"day": frame["day"], "net": side * frame["last"] - RT_COST})


def overnight_trades(daily: pd.DataFrame) -> pd.DataFrame:
    """Each close to the next open, dated by the morning it ends."""

    move = daily["open"] / daily["close"].shift(1) - 1
    frame = pd.DataFrame({"day": daily.index, "net": (move - RT_COST - NIGHT_CARRY).to_numpy()})
    return frame.dropna().reset_index(drop=True)


def day_short_trades(daily: pd.DataFrame) -> pd.DataFrame:
    move = daily["close"] / daily["open"] - 1
    return pd.DataFrame({"day": daily.index, "net": (-move - RT_COST).to_numpy()}).dropna()


def calm_nights(daily: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    """The overnight rule on nights when the prior close's VIX is under its trailing median."""

    aligned = vix.reindex(daily.index).ffill()
    median = aligned.rolling(VIX_WINDOW).median()
    calm = (aligned < median).shift(1, fill_value=False)
    frame = overnight_trades(daily)
    keep = calm.reindex(frame["day"]).fillna(False).astype(bool).to_numpy()
    return frame[keep].reset_index(drop=True)


def judge(frame: pd.DataFrame) -> dict[str, Any]:
    n = len(frame)
    if n < 2:
        return {"trades": n, "passed": False, "fails": ["too few trades"]}
    net = frame["net"]
    sd = float(net.std(ddof=1))
    t = float(net.mean() / (sd / math.sqrt(n))) if sd > 0 else math.nan
    days = sorted(frame["day"])
    cut = days[n // 2]
    halves = [float(net[frame["day"] < cut].mean()), float(net[frame["day"] >= cut].mean())]
    years = frame.groupby(frame["day"].map(lambda d: d.year))["net"].agg(["count", "mean"])
    judged = years[years["count"] >= PASS["min_year_trades"]]
    share = float((judged["mean"] > 0).mean()) if len(judged) else 0.0
    fails = []
    if n < PASS["min_trades"]:
        fails.append(f"only {n} trades")
    if not net.mean() > 0:
        fails.append("loses money after costs")
    if not t >= PASS["min_t"]:
        fails.append(f"t {t:.2f} < {PASS['min_t']}")
    if not all(h > 0 for h in halves):
        fails.append("not positive in both halves")
    if share < PASS["min_year_share"]:
        fails.append(f"positive in {share:.0%} of years")
    return {
        "trades": n, "mean_net": float(net.mean()), "win_rate": float((net > 0).mean()), "t": t,
        "before_costs": float(net.mean() + RT_COST), "halves": halves, "year_share": share,
        "by_year": {str(y): float(m) for y, m in years["mean"].items()},
        "first": str(days[0]), "last": str(days[-1]), "passed": not fails, "fails": fails,
    }  # fmt: skip


def main() -> None:
    import duckdb

    from tradedesk_lab import new_listings as nl

    daily = ps._nifty()
    con = duckdb.connect(str(nl.DB), read_only=True)
    try:
        raw = con.execute(
            "SELECT ts, close FROM candles WHERE interval = '1day' AND scrip_code = ? ORDER BY ts",
            [ib.VIX],
        ).df()
    finally:
        con.close()
    raw["day"] = pd.to_datetime(raw["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    vix = raw.drop_duplicates("day").set_index("day")["close"]
    days = intraday_days(ib.load(ib.NIFTY))
    rules = {
        "momentum": momentum_trades(days),
        "momentum_big": momentum_trades(days, big_only=True),
        "overnight": overnight_trades(daily),
        "day_short": day_short_trades(daily),
        "overnight_low": calm_nights(daily, vix),
    }
    report = {
        "generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS,
        "rules": {name: judge(frame) for name, frame in rules.items()},
    }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    for name, row in report["rules"].items():
        print(name, json.dumps(row, default=str))


if __name__ == "__main__":
    main()
