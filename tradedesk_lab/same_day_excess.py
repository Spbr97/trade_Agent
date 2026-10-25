"""Same-day excess: a faster, market-neutral evidence feed for `profit_gate`'s verdicts.

`profit_gate.py` already judges each setup with an always-valid confidence sequence on live
calls. Two things slow it down:

1. it waits for each call's full exit (up to 40 sessions for trailing exits);
2. it uses raw R, and about 23% of a stock's 20-session move is the market moving every
   stock at once (NSE 2017-2026), so many same-day calls rise or fall together.

This module scores every distinct live call at a FIXED horizon (10 sessions) against a
same-day matched random sample, then feeds the day-level averages into the SAME
`profit_gate.confidence_sequence`:

    call R      = (close[t+10] - open[t+1]) / (entry - stop)
    random R    = (close'[t+10] - open'[t+1]) / (k x ATR14'[t])  for 10 random stocks that
                  day, from the same liquidity quintile, k = the call's stop in ATR units
    excess      = call R - mean(random R)
    unit        = one decision day (the mean excess of that day's calls), so same-day calls
                  are not counted as independent evidence

It measures selection and entry at a fixed horizon, not the exit: exits still need the full
resolved trades the gate already uses. A setup "ahead" here has beaten same-day random
stocks with time-uniform 95% confidence; "behind" has lost to them. Research only: it never
changes a verdict, alert, size or order.

    python -m tradedesk_lab.same_day_excess --markets nse bse
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from tradedesk.broker.indstocks.models import IST
from tradedesk.profit_gate import ALPHA, JUDGED_CONTRACTS, UNTRADEABLE_PREFIX, confidence_sequence
from tradedesk_lab.artifacts import OUTPUT, ROOT

HORIZON = 10
RANDOM_PER_CALL = 10
MIN_TURNOVER = 5e7  # Rs 5 crore average daily traded value
LIQUIDITY_BANDS = 5
DEFAULT_SEED = 20261025
TRACKERS = {
    "nse": ROOT / "data" / "reports" / "nse_signal_tracking.jsonl",
    "bse": ROOT / "data" / "reports" / "bse_signal_tracking.jsonl",
}
DBS = {"nse": ROOT / "data" / "tradedesk.duckdb", "bse": ROOT / "data" / "bse.duckdb"}
OUT_DIR = OUTPUT / "same_day_excess"


@dataclass(frozen=True)
class Call:
    setup: str
    market: str
    scrip_code: str
    symbol: str
    armed_on: date
    entry: float
    stop: float


@dataclass
class StockDays:
    """One stock's daily arrays, aligned on `dates`."""

    dates: np.ndarray  # numpy datetime64[D]
    open: np.ndarray
    close: np.ndarray
    atr14: np.ndarray
    turn20: np.ndarray  # mean close x volume over the PRIOR 20 sessions


def stock_days(
    dates: Sequence[Any],
    o: Sequence[float],
    h: Sequence[float],
    low: Sequence[float],
    c: Sequence[float],
    v: Sequence[float],
) -> StockDays:
    """Wilder ATR(14) and prior-20-session turnover from plain OHLCV arrays."""

    o_, h_, l_, c_, v_ = (np.asarray(x, float) for x in (o, h, low, c, v))
    n = len(c_)
    pc = np.r_[c_[0], c_[:-1]]
    tr = np.maximum(h_ - l_, np.maximum(abs(h_ - pc), abs(l_ - pc)))
    atr = np.full(n, np.nan)
    if n >= 14:
        atr[13] = tr[:14].mean()
        for i in range(14, n):
            atr[i] = (atr[i - 1] * 13 + tr[i]) / 14
    tv = c_ * v_
    turn = np.full(n, np.nan)
    if n > 20:
        cs = np.cumsum(np.r_[0.0, tv])
        turn[20:] = (cs[20:n] - cs[: n - 20]) / 20  # bars i-20..i-1, known at i's open
    return StockDays(np.asarray(dates, "datetime64[D]"), o_, c_, atr, turn)


def distinct_live_calls(rows: Iterable[Mapping[str, Any]], market: str) -> list[Call]:
    """Live calls, each counted once: the judged contracts only (a call graded under two
    contracts is one call), calls outside the ATR band dropped - as `profit_gate` does."""

    seen: set[tuple[str, str, str]] = set()
    out: list[Call] = []
    for row in rows:
        if row.get("source") != "live":
            continue
        if str(row.get("contract_version") or JUDGED_CONTRACTS[1]) not in JUDGED_CONTRACTS:
            continue
        if any(str(w).startswith(UNTRADEABLE_PREFIX) for w in row.get("rejected_for") or []):
            continue
        try:
            entry, stop = float(row["entry"]), float(row["stop"])
            armed = date.fromisoformat(str(row["armed_on"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not entry > stop > 0:
            continue
        key = (str(row.get("setup")), str(row.get("symbol") or row.get("scrip_code")), str(armed))
        if key in seen:
            continue
        seen.add(key)
        out.append(Call(str(row.get("setup")), market, str(row.get("scrip_code")),
                        str(row.get("symbol") or ""), armed, entry, stop))  # fmt: skip
    return out


def dedupe_across_markets(calls: Sequence[Call]) -> list[Call]:
    """The same setup on the same company on the same day is one bet, whichever exchange
    it was logged on (NSE first)."""

    seen: set[tuple[str, str, date]] = set()
    out = []
    for call in sorted(calls, key=lambda c: (c.market != "nse", c.armed_on)):
        key = (call.setup, call.symbol or call.scrip_code, call.armed_on)
        if key not in seen:
            seen.add(key)
            out.append(call)
    return out


class Universe:
    """Per-day cross-section of one market for matched random draws."""

    def __init__(self, stocks: Mapping[str, StockDays]) -> None:
        self.stocks = stocks
        self.by_day: dict[np.datetime64, list[tuple[str, int]]] = {}
        for code, s in stocks.items():
            ok = (s.turn20 >= MIN_TURNOVER) & np.isfinite(s.atr14) & (s.atr14 > 0)
            for i in np.nonzero(ok[: len(ok) - HORIZON - 1])[0]:
                self.by_day.setdefault(s.dates[i], []).append((code, int(i)))

    def forward_move(self, code: str, i: int) -> float:
        s = self.stocks[code]
        return float(s.close[i + HORIZON] - s.open[i + 1])

    def excess(self, call: Call, rng: np.random.Generator) -> float | None:
        s = self.stocks.get(call.scrip_code)
        if s is None:
            return None
        hit = np.nonzero(s.dates == np.datetime64(call.armed_on, "D"))[0]
        if len(hit) == 0:
            return None
        t = int(hit[0])
        if t + HORIZON >= len(s.close) or not (np.isfinite(s.atr14[t]) and s.atr14[t] > 0):
            return None  # not matured yet
        risk = call.entry - call.stop
        call_r = self.forward_move(call.scrip_code, t) / risk
        k_stop = risk / s.atr14[t]
        peers = [(c, i) for c, i in self.by_day.get(s.dates[t], []) if c != call.scrip_code]
        if len(peers) < RANDOM_PER_CALL:
            return None
        turns = np.array([self.stocks[c].turn20[i] for c, i in peers])
        own = s.turn20[t] if np.isfinite(s.turn20[t]) else np.median(turns)
        edges = np.quantile(turns, np.linspace(0, 1, LIQUIDITY_BANDS + 1))
        band = min(int(np.searchsorted(edges, own, side="right")) - 1, LIQUIDITY_BANDS - 1)
        lo, hi = edges[max(band, 0)], edges[max(band, 0) + 1]
        same = [p for p, tv in zip(peers, turns, strict=True) if lo <= tv <= hi]
        pool = same if len(same) >= RANDOM_PER_CALL else peers
        pick = rng.choice(len(pool), size=RANDOM_PER_CALL, replace=False)
        rand = [
            self.forward_move(pool[j][0], pool[j][1])
            / (k_stop * self.stocks[pool[j][0]].atr14[pool[j][1]])
            for j in pick
        ]
        return call_r - float(np.mean(rand))


@dataclass
class SetupEvidence:
    setup: str
    n_calls: int  # matured distinct calls scored
    n_days: int  # decision days (the unit)
    mean_excess_r: float | None
    lower: float
    upper: float
    verdict: str  # ahead / behind / collecting


def evaluate(calls: Sequence[Call], universes: Mapping[str, Universe], *,
             seed: int = DEFAULT_SEED) -> list[SetupEvidence]:  # fmt: skip
    rng = np.random.default_rng(seed)
    per_setup: dict[str, dict[date, list[float]]] = {}
    for call in calls:
        uni = universes.get(call.market)
        x = uni.excess(call, rng) if uni is not None else None
        if x is None or not np.isfinite(x):
            continue
        per_setup.setdefault(call.setup, {}).setdefault(call.armed_on, []).append(x)
    alpha = ALPHA / max(len(per_setup), 1)  # split across setups, as profit_gate does
    out = []
    for setup, days in sorted(per_setup.items()):
        day_means = [float(np.mean(v)) for _, v in sorted(days.items())]
        lower, upper = confidence_sequence(day_means, alpha=alpha)
        verdict = "ahead" if lower > 0 else "behind" if upper < 0 else "collecting"
        out.append(SetupEvidence(setup, sum(len(v) for v in days.values()), len(day_means),
                                 float(np.mean(day_means)), lower, upper, verdict))  # fmt: skip
    return out


def load_universe(db: Path, start: date) -> Universe:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "select scrip_code, ts, open, high, low, close, volume from candles "
            "where interval = '1day' and ts >= ? order by scrip_code, ts",
            [int(datetime(start.year, start.month, start.day).timestamp()) - 60 * 86400],
        ).fetchnumpy()
    finally:
        con.close()
    codes = rows["scrip_code"]
    cuts = np.r_[0, np.nonzero(codes[1:] != codes[:-1])[0] + 1, len(codes)]
    stocks = {}
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        d = (rows["ts"][a:b] + 19_800) // 86_400  # IST calendar day
        stocks[str(codes[a])] = stock_days(
            d.astype("datetime64[D]"), rows["open"][a:b], rows["high"][a:b],
            rows["low"][a:b], rows["close"][a:b], rows["volume"][a:b],
        )  # fmt: skip
    return Universe(stocks)


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    except OSError:
        return []


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets", nargs="+", choices=sorted(TRACKERS), default=["nse", "bse"])
    ap.add_argument("--db", nargs="*", type=Path, help="DuckDB per market, same order")
    a = ap.parse_args(argv)
    dbs = dict(zip(a.markets, a.db, strict=True)) if a.db else {m: DBS[m] for m in a.markets}
    calls = dedupe_across_markets(
        [c for m in a.markets for c in distinct_live_calls(_rows(TRACKERS[m]), m)]
    )
    if not calls:
        raise SystemExit("no live calls found")
    start = min(c.armed_on for c in calls)
    universes = {m: load_universe(dbs[m], start) for m in a.markets}
    evidence = evaluate(calls, universes)
    report = {
        "generated_at": datetime.now(IST).isoformat(),
        "markets": a.markets,
        "horizon_sessions": HORIZON,
        "distinct_calls": len(calls),
        "setups": [asdict(e) for e in evidence],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{'_'.join(a.markets)}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    for e in evidence:
        mean = f"{e.mean_excess_r:+.3f}R" if e.mean_excess_r is not None else "n/a"
        print(f"{e.setup:45s} calls={e.n_calls:4d} days={e.n_days:3d} excess={mean}"
              f" range=[{e.lower:+.2f}, {e.upper:+.2f}] {e.verdict}")  # fmt: skip
    print(f"report: {out}")


if __name__ == "__main__":
    main()
