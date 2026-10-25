"""Pre-registered checks of three published edges on this project's own data (25 Oct 2026).

Each check's pass bar was written before it was run (docs/literature-checks.md):

A. Pre-results run-up (earnings announcement premium; Frazzini & Lamont, NBER w13090; Indian
   large caps: Kundu, Banerjee & McMillan 2021). Entry: next open after the close 10 sessions
   before the PREDICTED results date (same company's date 350-380 days earlier + 365 days, so
   nothing after the entry is used); exit: close of the session before the actual date.
   PASS: mean net excess over the same-window equal-weight liquid universe > 0 after a 0.56%
   round trip, day-clustered t > 3, positive in 2017-21 and 2022-26.
B. Momentum refinements (frog-in-the-pan, Da, Gurun & Warachka 2014; residual momentum,
   Blitz, Huij & Martens 2011) against plain 12-1 momentum, top decile, every 20 sessions,
   held 20, cost 0.5% x turnover. PASS: beats plain by paired t > 2.5 in both halves.
C. Market intraday momentum (Gao, Han, Li & Zhou): NIFTY 50 previous close -> 09:45 return
   decides the direction of NIFTYBEES 15:00 open -> 15:30 close; cost 0.08% round trip.
   PASS: mean net > 0, t > 2, both halves positive.

Results on the 16-17 Oct data snapshot - all three FAIL:
A +0.27% gross excess over ~9 sessions (a real but small premium), -0.21% net (t -1.7);
B frog-in-the-pan +0.08% and residual +0.13% per 20 sessions over plain (paired t 0.4, 0.7);
C -0.096% net a day over 247 days (t -8.2): no intraday momentum in NIFTY this past year.
Research only.

    python -m tradedesk_lab.literature_checks [--db data/tradedesk.duckdb]
        [--intraday-db data/m14_m18/intraday_history/nse.duckdb]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab.artifacts import OUTPUT, ROOT

MIN_TURNOVER = 5e7
MIN_PRICE = 50.0
RUNUP_COST = 0.0056
MOMENTUM_COST = 0.005
INTRADAY_COST = 0.0008
NIFTY50, NIFTYBEES = "NSE_40000001", "NSE_10576"
OUT_DIR = OUTPUT / "literature_checks"
ETF_PATTERN = (
    "(ETF|BEES|LIQUID|GILT|NIFTY|SENSEX|INAV|IETF|MAFANG|MON100|NASDAQ|HANGSENG|BHARAT ?BOND)"
)


# ----------------------------------------------------------------------------- statistics


def clustered_t(values: Sequence[float], keys: Sequence[Any]) -> tuple[float, float, int]:
    """Mean and t-statistic of per-key means (keys = decision days), so same-day
    observations count once."""

    groups: dict[Any, list[float]] = defaultdict(list)
    for v, k in zip(values, keys, strict=True):
        groups[k].append(float(v))
    means = np.array([np.mean(g) for g in groups.values()])
    if len(means) < 3:
        return float(means.mean()) if len(means) else math.nan, math.nan, len(means)
    se = means.std(ddof=1) / math.sqrt(len(means))
    return float(means.mean()), float(means.mean() / se) if se > 0 else math.nan, len(means)


def passes(full: tuple[float, float], halves: Sequence[float], *, t_bar: float) -> bool:
    mean, t = full
    return bool(mean > 0 and t > t_bar and all(h > 0 for h in halves))


# ----------------------------------------------------------------------------- A: run-up


def predicted_date(history: Sequence[int], event: int) -> int | None:
    """Predicted results day (epoch days) from the same company's results date 350-380 days
    earlier, + 365. None when there is no such prior date. Uses only dates before `event`."""

    prior = [d for d in history if d < event and 350 <= event - d <= 380]
    return prior[-1] + 365 if prior else None


# ----------------------------------------------------------------------------- B: momentum


def information_discreteness(daily_returns: np.ndarray, formation_return: float) -> float:
    """Da, Gurun & Warachka's ID: sign(formation return) x (% negative days - % positive
    days). Low ID = the move came in many small steps (continuous information)."""

    r = daily_returns[np.isfinite(daily_returns)]
    if len(r) == 0:
        return math.nan
    return float(np.sign(formation_return) * ((r < 0).mean() - (r > 0).mean()))


def residual_score(stock: np.ndarray, market: np.ndarray) -> float:
    """Cumulative return left after the market's share (beta-only market model, no
    intercept - with an intercept the formation residuals would sum to zero by
    construction), scaled by the residuals' own volatility."""

    ok = np.isfinite(stock) & np.isfinite(market)
    s, m = stock[ok], market[ok]
    if len(s) < 60 or float((m * m).sum()) == 0:
        return math.nan
    beta = float((m * s).sum() / (m * m).sum())
    res = s - beta * m
    sd = float(res.std())
    return float(res.sum() / (sd * math.sqrt(len(res)))) if sd > 0 else math.nan


# ----------------------------------------------------------------------------- C: intraday


def intraday_momentum_day(prev_close: float, close_0945: float, entry_1500: float,
                          exit_1530: float, *, cost: float = INTRADAY_COST) -> float:  # fmt: skip
    """One day of the strategy: direction from previous close -> 09:45, traded 15:00 ->
    15:30 at a round-trip `cost`. Returns the net return."""

    signal = close_0945 / prev_close - 1
    last = exit_1530 / entry_1500 - 1
    return float(np.sign(signal) * last - cost) if signal != 0 else 0.0


# ----------------------------------------------------------------------------- runners


def _daily_panel(db: Path) -> dict[str, np.ndarray]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        a = con.execute(
            f"""select c.scrip_code, (c.ts // 86400)::int as dn, c.open, c.close, c.volume,
                       i.trading_symbol
                from candles c join instruments i using (scrip_code)
                where c.interval = '1day' and i.exch = 'NSE' and i.series = 'EQ'
                  and c.close > 0 and c.open > 0
                  and not regexp_matches(upper(coalesce(i.name, '') || ' ' ||
                      coalesce(i.trading_symbol, '')), '{ETF_PATTERN}')
                order by c.scrip_code, c.ts"""
        ).fetchnumpy()
        events = con.execute(
            "select symbol, event_date from results_events where purpose ilike '%result%'"
            " order by symbol, event_date"
        ).fetchall()
    finally:
        con.close()
    a["events"] = events  # type: ignore[assignment]
    return a


def _turnover20(close: np.ndarray, volume: np.ndarray, starts: np.ndarray) -> np.ndarray:
    out = np.full(len(close), np.nan)
    for s, e in zip(starts[:-1], starts[1:], strict=True):
        tv = close[s:e] * volume[s:e]
        cs = np.cumsum(np.r_[0.0, tv])
        if e - s > 20:
            out[s + 20 : e] = (cs[20 : e - s] - cs[: e - s - 20]) / 20
    return out


def run_checks(db: Path, intraday_db: Path | None) -> dict[str, Any]:
    p = _daily_panel(db)
    code, dn, o, c = p["scrip_code"], p["dn"], p["open"].astype(float), p["close"].astype(float)
    n = len(dn)
    starts = np.r_[0, np.nonzero(code[1:] != code[:-1])[0] + 1, n]
    sid = np.repeat(np.arange(len(starts) - 1), np.diff(starts))
    turn = _turnover20(c, p["volume"].astype(float), starts)
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
    report: dict[str, Any] = {"generated_at": datetime.now(IST).isoformat()}

    # A
    first = {str(code[s]): (s, e) for s, e in zip(starts[:-1], starts[1:], strict=True)}
    sym_to_code = {
        str(sy): str(cd)
        for cd, sy in zip(code[starts[:-1]], p["trading_symbol"][starts[:-1]], strict=True)
    }  # noqa: E501
    by_sym: dict[str, list[int]] = defaultdict(list)
    for sy, d in p["events"]:  # type: ignore[union-attr]
        by_sym[str(sy)].append(int(np.datetime64(d, "D").astype(int)))
    vals, keys, yrs = [], [], []
    for sy, hist in by_sym.items():
        span = first.get(sym_to_code.get(sy, ""))
        if span is None:
            continue
        s, e = span
        days = dn[s:e]
        for ev in hist:
            pred = predicted_date(hist, ev)
            if pred is None:
                continue
            sig = int(np.searchsorted(days, pred)) - 10
            ex = int(np.searchsorted(days, ev)) - 1
            if sig < 253 or ex <= sig + 1 or ex + 1 >= len(days) or not liq[s + sig]:
                continue
            t = s + sig
            r = c[s + ex] / o[t + 1] - 1
            u = math.exp(lcum[di[s + ex] + 1] - lcum[di[t + 1]]) - 1
            vals.append(r - u - RUNUP_COST)
            keys.append(int(dn[t]))
            yrs.append(int(year[di[t]]))
    vals_a, yrs_a = np.array(vals), np.array(yrs)
    full = clustered_t(vals_a, keys)
    halves = [clustered_t(vals_a[sel], np.array(keys)[sel])[0]
              for sel in (yrs_a <= 2021, yrs_a >= 2022)]  # fmt: skip
    report["A_pre_results_runup"] = {
        "events": len(vals), "mean_net_excess": full[0], "t": full[1], "days": full[2],
        "halves": halves, "passed": passes(full[:2], halves, t_bar=3.0),
    }  # fmt: skip

    # B
    order = np.argsort(di, kind="stable")
    bounds = np.searchsorted(di[order], np.arange(len(dates) + 1))
    begin = int(np.searchsorted(dates, int(np.datetime64("2017-09-15").astype(int))))
    prev: dict[str, set[int]] = {}
    recs: list[dict[str, float]] = []
    for d in range(begin, len(dates) - 21, 20):
        rows = order[bounds[d] : bounds[d + 1]]
        feats = []
        for t in rows[liq[rows]]:
            if t < 252 or sid[t - 252] != sid[t] or t + 20 >= n or sid[t + 20] != sid[t]:
                continue
            window = slice(t - 251, t - 20)
            mom = c[t - 21] / c[t - 252] - 1
            idv = information_discreteness(ret[window], mom)
            rs = residual_score(ret[window], mkt[di[window]])
            feats.append((mom, idv, rs, c[t + 20] / o[t + 1] - 1, sid[t]))
        if len(feats) < 200:
            continue
        mom_, idv_, rs_, fwd, ss = np.array(feats).T
        top30 = mom_ >= np.quantile(mom_, 0.7)
        sets = {
            "plain": ss[mom_ >= np.quantile(mom_, 0.9)],
            "fip": ss[top30 & (idv_ <= np.nanquantile(idv_[top30], 1 / 3))],
            "residual": ss[rs_ >= np.nanquantile(rs_, 0.9)],
        }
        rec = {"year": float(year[d])}
        for name, chosen in sets.items():
            cur = set(chosen.astype(int))
            turnover = len(cur - prev[name]) / max(len(cur), 1) if name in prev else 1.0
            prev[name] = cur
            rec[name] = float(fwd[np.isin(ss, chosen)].mean() - fwd.mean()
                              - MOMENTUM_COST * turnover)  # fmt: skip
        recs.append(rec)
    yrs_b = np.array([r["year"] for r in recs])
    out_b: dict[str, Any] = {"rebalances": len(recs)}
    for name in ("fip", "residual"):
        diff = np.array([r[name] - r["plain"] for r in recs])
        full_b = clustered_t(diff, list(range(len(diff))))
        halves_b = [float(diff[yrs_b <= 2021].mean()), float(diff[yrs_b >= 2022].mean())]
        out_b[name] = {
            "minus_plain": full_b[0], "paired_t": full_b[1], "halves": halves_b,
            "passed": passes(full_b[:2], halves_b, t_bar=2.5),
        }  # fmt: skip
    out_b["plain_net_excess"] = float(np.mean([r["plain"] for r in recs]))
    report["B_momentum_refinements"] = out_b

    # C
    if intraday_db is not None and intraday_db.exists():
        report["C_nifty_intraday_momentum"] = _intraday(intraday_db)
    return report


def _intraday(intraday_db: Path) -> dict[str, Any]:
    import duckdb

    con = duckdb.connect(str(intraday_db), read_only=True)
    try:
        bars = {
            cd: con.execute(
                "select ts, open, close from m15 where code = ? order by ts", [cd]
            ).fetchnumpy()
            for cd in (NIFTY50, NIFTYBEES)
        }
    finally:
        con.close()

    def by_day(b: dict[str, np.ndarray]) -> dict[str, dict[str, tuple[float, float]]]:
        out: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
        for ts, op, cl in zip(b["ts"], b["open"], b["close"], strict=True):
            t = datetime.fromtimestamp(int(ts), IST)
            out[t.date().isoformat()][t.strftime("%H:%M")] = (float(op), float(cl))
        return out

    nf, nb = by_day(bars[NIFTY50]), by_day(bars[NIFTYBEES])
    days = sorted(set(nf) & set(nb))
    net = []
    for prev_day, day in zip(days[:-1], days[1:], strict=True):
        try:
            net.append(intraday_momentum_day(
                nf[prev_day]["15:15"][1], nf[day]["09:30"][1],
                nb[day]["15:00"][0], nb[day]["15:15"][1],
            ))  # fmt: skip
        except KeyError:
            continue
    x = np.array(net)
    half = len(x) // 2
    full = clustered_t(x, list(range(len(x))))
    halves = [float(x[:half].mean()), float(x[half:].mean())]
    return {"days": len(x), "mean_net": full[0], "t": full[1], "halves": halves,
            "passed": passes(full[:2], halves, t_bar=2.0)}  # fmt: skip


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "tradedesk.duckdb")
    intraday = ROOT / "data" / "m14_m18" / "intraday_history" / "nse.duckdb"
    ap.add_argument("--intraday-db", type=Path, default=intraday)
    a = ap.parse_args(argv)
    report = run_checks(a.db, a.intraday_db)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "latest.json"
    out.write_text(json.dumps(report, indent=2, default=float))
    print(json.dumps(report, indent=2, default=lambda v: round(float(v), 4)))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
