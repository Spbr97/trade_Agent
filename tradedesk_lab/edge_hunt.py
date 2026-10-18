"""Edge hunt: three ideas with the best documented odds, tested on ten years of NSE history.

Research only (owner-approved 2026-10-18). Reads a COPY of the production daily store and
NSE's published sector-index member lists; writes data/m14_m18/edge_hunt/. Nothing here
alerts, sizes or trades. Every pass bar below was fixed before any result was seen.

1. index_trend - NIFTY 50 as an index future. Rules: 12-month time-series momentum (monthly),
   price vs its 200-day average (daily), 50-day breakout / 20-day exit (daily); each tested
   long/short and long/flat. A long future earns the price return MINUS carry (~5.2%/yr:
   6.5% interest less 1.3% dividends); a short earns the carry. Costs 0.04% per unit traded
   plus 0.02% a month to roll. Pass: Sharpe of excess return >= 0.76 (t > 2.39 over the
   years tested, i.e. p < 0.05/6), above simply holding the future, positive in >= 60% of
   years, max drawdown < 25%.

2. earnings_drift - after a results announcement, a stock that rose >= 5% more than the
   market from the last close before the date to the first close after it, on >= 2x its usual
   volume, is bought at the next open and held 20 or 60 sessions, net of the delivery round
   trip, against the equal-weight liquid market over the same days. Pass: day-clustered
   excess p < 0.05/2, mean net return > 0, excess positive in >= 70% of years.

3. sector_rotation - equal-weight baskets of each sector index's members; at each month end
   hold the 3 sectors with the best 63- (or 126-) session return for the next month, costs on
   the share of the portfolio replaced. Pass: monthly excess over an equal mix of all sectors
   has t > 2.24 (p < 0.05/2) and is positive in >= 60% of years.

Caveat for 2 and 3: stock and member lists are today's (delisted names are missing), which
flatters long-only results somewhat - a pass would still need live confirmation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "edge_hunt"
NIFTY = "NSE_40000001"
CARRY = 0.052
FUT_COST = 0.0004
ROLL_COST = 0.0002
DELIVERY_RT = 0.0039098  # the production delivery round trip (multiday_hold_study's figure)
MIN_TURNOVER = 5e7
MIN_PRICE = 50.0


def _z_to_p(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def load_panels(db: Path) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        frame = con.execute(
            "SELECT c.scrip_code, c.ts, c.open, c.close, c.volume FROM candles c "
            "JOIN instruments i ON i.scrip_code = c.scrip_code "
            "WHERE c.interval = '1day' AND (i.series = 'EQ' OR c.scrip_code = ?)",
            [NIFTY],
        ).df()
        symbols = dict(con.execute(
            "SELECT trading_symbol, scrip_code FROM instruments WHERE exch='NSE' AND series='EQ'"
        ).fetchall())  # fmt: skip
        events = con.execute("SELECT DISTINCT symbol, event_date FROM results_events").df()
    finally:
        con.close()
    frame["day"] = (
        pd.to_datetime(frame["ts"], unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.normalize()
        .dt.tz_localize(None)
    )
    panels = {
        k: frame.pivot_table(
            index="day", columns="scrip_code", values=k, aggfunc="last"
        ).sort_index()
        for k in ("open", "close", "volume")
    }
    return {**panels, "symbols": symbols, "events": events}


def _stats(excess: pd.Series, periods_per_year: float) -> dict[str, Any]:
    excess = excess.dropna()
    years = len(excess) / periods_per_year
    mean, sd = excess.mean(), excess.std(ddof=1)
    sharpe = float(mean / sd * math.sqrt(periods_per_year)) if sd > 0 else 0.0
    equity = (1 + excess).cumprod()
    dd = float((1 - equity / equity.cummax()).max())
    by_year = excess.groupby(excess.index.year).apply(lambda s: float((1 + s).prod() - 1))
    return {
        "annual_excess": float((1 + excess).prod() ** (1 / years) - 1) if years > 0 else None,
        "sharpe": sharpe, "t": sharpe * math.sqrt(years), "max_drawdown": dd, "years": years,
        "by_year": {str(k): v for k, v in by_year.items()},
        "positive_year_share": float((by_year > 0).mean()) if len(by_year) else 0.0,
    }  # fmt: skip


def index_trend(p: dict[str, Any]) -> list[dict[str, Any]]:
    close = p["close"][NIFTY].dropna()
    ret = close.pct_change()
    carry_d = CARRY / 252
    month_end = close.groupby([close.index.year, close.index.month]).tail(1).index
    signals: dict[str, pd.Series] = {}
    mom = pd.Series(np.nan, index=close.index)
    mom.loc[month_end] = np.sign(close.loc[month_end] / close.shift(252).loc[month_end] - 1)
    signals["tsmom12"] = mom.ffill()
    signals["sma200"] = np.sign(close - close.rolling(200).mean())
    hi, lo = close.rolling(50).max().shift(1), close.rolling(20).min().shift(1)
    hi_s, lo_s = close.rolling(20).max().shift(1), close.rolling(50).min().shift(1)
    state, pos = 0.0, []
    for c, h, low, hs, ls in zip(close, hi, lo, hi_s, lo_s, strict=True):
        if state <= 0 and c > h:
            state = 1.0
        elif state >= 0 and c < ls:
            state = -1.0
        elif state > 0 and c < low:
            state = 0.0
        elif state < 0 and c > hs:
            state = 0.0
        pos.append(state)
    signals["donchian50_20"] = pd.Series(pos, index=close.index)
    roll = pd.Series(0.0, index=close.index)
    roll.loc[month_end] = ROLL_COST
    hold = (ret - carry_d).dropna()
    out = [{"rule": "always_long_future", "variant": "benchmark", **_stats(hold, 252)}]
    for name, sig in signals.items():
        for variant in ("long_short", "long_flat"):
            s = sig.clip(lower=0) if variant == "long_flat" else sig
            position = s.shift(1).fillna(0)  # decided at the close, earns the next day
            excess = (
                position * ret
                - position * carry_d
                - position.diff().abs().fillna(0) * FUT_COST
                - position.abs() * roll
            )
            st = _stats(excess.loc[close.index[260] :], 252)
            st["passed"] = bool(
                st["sharpe"] >= 0.76
                and st["sharpe"] > out[0]["sharpe"]
                and st["positive_year_share"] >= 0.6
                and st["max_drawdown"] < 0.25
            )
            out.append({"rule": name, "variant": variant, **st})
    return out


def earnings_drift(p: dict[str, Any]) -> list[dict[str, Any]]:
    opens, closes, vol = p["open"], p["close"], p["volume"]
    dates = closes.index
    turnover20 = (closes * vol).rolling(20).mean().shift(1)
    vol20 = vol.rolling(20).mean().shift(1)
    liquid = (turnover20 >= MIN_TURNOVER) & (closes.shift(1) >= MIN_PRICE)
    rows = []
    for sym, event_day in p["events"].itertuples(index=False):
        code = p["symbols"].get(sym)
        if code not in closes.columns:
            continue
        d = pd.Timestamp(event_day)
        i1 = dates.searchsorted(d, side="right")  # first session strictly after the date
        i0 = dates.searchsorted(d, side="left") - 1  # last session strictly before it
        if i0 < 20 or i1 + 61 >= len(dates):
            continue
        t0, t1 = dates[i0], dates[i1]
        if not liquid.at[t1, code]:
            continue
        c0, c1 = closes.at[t0, code], closes.at[t1, code]
        window_vol = vol.iloc[i0 + 1 : i1 + 1][code].max()
        if not (np.isfinite(c0) and np.isfinite(c1) and c0 > 0 and np.isfinite(window_vol)):
            continue
        rows.append(
            (code, i0, i1, c1 / c0 - 1, window_vol / vol20.iat[i1, closes.columns.get_loc(code)])
        )
    ev = pd.DataFrame(rows, columns=["code", "i0", "i1", "react", "volx"])
    # market reaction over the same window: equal-weight liquid stocks
    daily_mkt = closes.pct_change().where(liquid).mean(axis=1)
    ev["mkt"] = [
        float((1 + daily_mkt.iloc[a + 1 : b + 1]).prod() - 1)
        for a, b in zip(ev["i0"], ev["i1"], strict=True)
    ]
    picks = ev[(ev["react"] - ev["mkt"] >= 0.05) & (ev["volx"] >= 2.0)]
    out = []
    for hold in (20, 60):
        fwd_stock, fwd_mkt, entry_days = [], [], []
        for code, i1 in zip(picks["code"], picks["i1"], strict=True):
            e, x = i1 + 1, i1 + hold
            o = opens.iat[e, closes.columns.get_loc(code)]
            c = closes.iat[x, closes.columns.get_loc(code)]
            if not (np.isfinite(o) and np.isfinite(c) and o > 0):
                continue
            m_open = opens.iloc[e].where(liquid.iloc[e])
            m_close = closes.iloc[x].where(liquid.iloc[e])
            m = float((m_close / m_open - 1).mean())
            fwd_stock.append(c / o - 1 - DELIVERY_RT)
            fwd_mkt.append(m)
            entry_days.append(dates[e])
        t = pd.DataFrame({"day": entry_days, "net": fwd_stock, "mkt": fwd_mkt})
        t["excess"] = t["net"] + DELIVERY_RT - t["mkt"]
        daily = t.groupby("day")["excess"].mean()
        z = (
            float(daily.mean() / (daily.std(ddof=1) / math.sqrt(len(daily))))
            if len(daily) > 1
            else 0.0
        )
        years = t.groupby(pd.to_datetime(t["day"]).dt.year)["excess"].mean()
        res = {
            "rule": f"earnings_jump_5pct_hold{hold}", "calls": int(len(t)), "days": int(len(daily)),
            "mean_net_return": float(t["net"].mean()), "win_rate": float((t["net"] > 0).mean()),
            "mean_excess": float(t["excess"].mean()), "beat_market_rate": float((t["excess"] > 0).mean()),  # noqa: E501
            "z": z, "p": _z_to_p(z), "by_year_excess": {str(k): float(v) for k, v in years.items()},
            "positive_year_share": float((years > 0).mean()) if len(years) else 0.0,
            "calls_per_year": float(len(t) / max(1, len(years))),
        }  # fmt: skip
        res["passed"] = bool(
            res["p"] < 0.025 and res["mean_net_return"] > 0 and res["positive_year_share"] >= 0.7
        )
        out.append(res)
    return out


def sector_rotation(p: dict[str, Any], sector_dir: Path) -> list[dict[str, Any]]:
    closes = p["close"]
    rets = closes.pct_change()
    baskets = {}
    for path in sorted(sector_dir.glob("*.csv")):
        with path.open(encoding="utf-8-sig") as fh:
            members = [p["symbols"].get(row["Symbol"].strip()) for row in csv.DictReader(fh)]
        codes = [c for c in members if c in closes.columns]
        if len(codes) >= 5:
            baskets[path.stem] = rets[codes].mean(axis=1)
    sector = pd.DataFrame(baskets).dropna(how="all")
    level = (1 + sector.fillna(0)).cumprod()
    month_end = level.groupby([level.index.year, level.index.month]).tail(1).index
    monthly = level.loc[month_end].pct_change()
    out = []
    for look in (63, 126):
        strength = level / level.shift(look) - 1
        weights_prev = pd.Series(0.0, index=sector.columns)
        rows = []
        for a, b in zip(month_end[:-1], month_end[1:], strict=True):
            rank = strength.loc[a].dropna().sort_values(ascending=False)
            if len(rank) < 6:
                continue
            w = pd.Series(0.0, index=sector.columns)
            w[rank.index[:3]] = 1 / 3
            turnover = float((w - weights_prev).abs().sum() / 2)
            weights_prev = w
            nxt = monthly.loc[b]
            strat = float((w * nxt).sum()) - turnover * DELIVERY_RT
            bench = float(nxt.mean())
            rows.append((b, strat - bench))
        ex = pd.Series(dict(rows))
        st = _stats(ex, 12)
        st["passed"] = bool(st["t"] > 2.24 and st["positive_year_share"] >= 0.6)
        out.append(
            {
                "rule": f"top3_of_{len(sector.columns)}_sectors_{look}d",
                "sectors": list(sector.columns),
                **st,
            }
        )
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sectors", type=Path, required=True)
    args = parser.parse_args(argv)
    p = load_panels(args.db)
    print(
        f"loaded {p['close'].shape[1]} codes x {p['close'].shape[0]} sessions; {len(p['events'])} results events",  # noqa: E501
        flush=True,
    )
    report = {
        "generated_at": datetime.now().astimezone().isoformat(), "authority": "research_only",
        "index_trend": index_trend(p), "earnings_drift": earnings_drift(p),
        "sector_rotation": sector_rotation(p, args.sectors), "doc": __doc__,
    }  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    for name in (f"{stamp}.json", "latest.json"):
        (OUT / name).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    for r in report["index_trend"]:
        print(
            f"INDEX  {r['rule']:20s} {r['variant']:10s} excess/yr={r['annual_excess']:+.1%} sharpe={r['sharpe']:+.2f} t={r['t']:+.2f} dd={r['max_drawdown']:.0%} years+={r['positive_year_share']:.0%} {'PASS' if r.get('passed') else ''}"  # noqa: E501
        )
    for r in report["earnings_drift"]:
        print(
            f"EARN   {r['rule']:28s} calls={r['calls']} ({r['calls_per_year']:.0f}/yr) net={r['mean_net_return']:+.2%} excess={r['mean_excess']:+.2%} beat={r['beat_market_rate']:.0%} z={r['z']:+.2f} years+={r['positive_year_share']:.0%} {'PASS' if r['passed'] else 'fail'}"  # noqa: E501
        )
    for r in report["sector_rotation"]:
        print(
            f"SECTOR {r['rule']:28s} excess/yr={r['annual_excess']:+.1%} sharpe={r['sharpe']:+.2f} t={r['t']:+.2f} dd={r['max_drawdown']:.0%} years+={r['positive_year_share']:.0%} {'PASS' if r['passed'] else 'fail'}"  # noqa: E501
        )


if __name__ == "__main__":
    main()
