"""Post-earnings drift (PEAD) through the harness gauntlet - the "post-earnings drift" item of
docs/plan-baseline-accuracy.md Phase 4, run on the results dates `data refresh-results` /
`data import-results` already store.

Hypothesis, fixed before any run: a liquid NSE stock whose results reaction is strongly
positive keeps outperforming over the following ten sessions. This is a stock-selection
claim, unlike the timing setups the project has already rejected.

Rule (the base case; the sensitivity sweep nudges the three numeric parameters +/-20%):
  event     results date E from the stored calendar; an E that is not a trading day for the
            stock maps to its next trading day.
  reaction  close(E+1) / close(E-1) - 1, minus the median of that same two-day return over
            every liquid NSE stock - covers both intraday and after-close announcements.
  signal    reaction >= REACTION_MIN, decided at close(E+1); the stock must be liquid then
            (20-day average traded value >= Rs 2 crore, the movers screen's own floor).
  entry     open of E+2 - the first price after the decision.
  stop      entry - STOP_ATR x ATR14 measured at close(E+1); gap-aware, stop wins ties
            (`backtest/null_baseline.py::_bar_barrier`, the same resolver the nulls use).
  target    entry + TARGET_R x risk; exit at the close of the HOLD-th session otherwise
            (config/risk.yaml max_hold_sessions = 10).
  size      config/risk.yaml: max_risk_per_trade_pct of trading_capital; delivery costs from
            the real EquityCostModel plus slippage_pct per fill. Trades that cannot be sized
            to one share inside that risk are excluded and counted.

Nulls. `run_gauntlet`'s random-entry stage is run exactly as for every other daily candidate
(`run_lab_candidate.py`: random entry inside the trade's own holding window). That is a weak
test of a stock-selection edge, so two stronger nulls are reported beside it and must also
be beaten by the eligibility margin (+0.10R, config/setups.yaml) for a pass:
  same_stock_random_time   the same stock, entered at a random session with no results date
                           nearby, same ATR stop, target multiple and hold.
  random_stock_same_date   a random other liquid stock without results nearby, entered on
                           the same day with the same rules.
Research only: nothing here can alert, size or trade; a pass would become a review_queue item.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.backtest.null_baseline import _bar_barrier
from tradedesk.engine.indicators import atr
from tradedesk.models import TradeType
from tradedesk_lab.harness.trade import HarnessTrade

STRATEGY_NAME = "post_earnings_drift"
LIQUID_NOTIONAL = 2e7  # Rs 2 crore 20-day average traded value (potential_movers.MIN_NOTIONAL)
QUIET_SESSIONS = 15  # a null draw keeps this far from any results date
NULL_COHORTS = 200
SEED = 20261014


@dataclass(frozen=True)
class DriftParams:
    reaction_min: float = 0.05
    stop_atr: float = 2.0
    target_r: float = 3.0
    hold: int = 10


@dataclass(frozen=True)
class StockBars:
    """One stock's daily bars as positional arrays plus what the rule needs at each bar."""

    code: str
    dates: list[date]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr14: np.ndarray
    liquid: np.ndarray  # bool: 20-day average traded value >= LIQUID_NOTIONAL, known at that close
    results_pos: tuple[int, ...]  # positions of results dates (mapped to trading days)

    def pos_of(self) -> dict[date, int]:
        return {d: i for i, d in enumerate(self.dates)}


@dataclass(frozen=True)
class DriftTrade:
    code: str
    event_date: str
    decision_date: str  # close(E+1): everything used was known here
    entry_date: str
    exit_date: str
    entry_pos: int
    exit_pos: int
    reaction: float
    entry: float
    stop: float
    target: float
    gross_r: float
    net_r: float
    gross_return: float


def build_stock_bars(
    frame: pd.DataFrame, code: str, results: Sequence[date]
) -> StockBars | None:
    """`frame`: one stock's daily rows with a `date` column and OHLCV, any order."""

    g = frame.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(g) < 30:
        return None
    dates = list(g["date"])
    notional = (g["volume"].astype(float) * g["close"].astype(float)).rolling(20, min_periods=15)
    liquid = (notional.mean() >= LIQUID_NOTIONAL).to_numpy()
    atr14 = atr(g[["open", "high", "low", "close"]].astype(float)).to_numpy()
    positions: list[int] = []
    for when in results:
        i = bisect_left(dates, when)  # first trading day on or after E
        if i < len(dates):
            positions.append(i)
    return StockBars(
        code=code,
        dates=dates,
        open=g["open"].to_numpy(float),
        high=g["high"].to_numpy(float),
        low=g["low"].to_numpy(float),
        close=g["close"].to_numpy(float),
        atr14=atr14,
        liquid=liquid,
        results_pos=tuple(sorted(set(positions))),
    )


def market_two_day_median(stocks: Mapping[str, StockBars]) -> dict[date, float]:
    """Median of close(d+1)/close(d-1)-1 over the stocks liquid at d-1 - the market part of a
    results reaction, removed so a broad rally is not mistaken for a stock-specific one."""

    columns: dict[str, pd.Series] = {}
    for code, s in stocks.items():
        c = pd.Series(s.close, index=s.dates)
        r2 = c.shift(-1) / c.shift(1) - 1
        ok = pd.Series(s.liquid, index=s.dates).shift(1, fill_value=False)
        columns[code] = r2.where(ok)
    wide = pd.DataFrame(columns)
    return {d: float(v) for d, v in wide.median(axis=1, skipna=True).items() if np.isfinite(v)}


def resolve(
    s: StockBars, entry_pos: int, risk_atr_pos: int, params: DriftParams
) -> tuple[float, float, float, float, int] | None:
    """(entry, stop, target, gross_r, exit_pos) for a long entered at open(entry_pos) with its
    stop sized from ATR at `risk_atr_pos` (the decision close). None when it cannot run."""

    last = entry_pos + params.hold - 1
    if risk_atr_pos < 0 or last >= len(s.close):
        return None
    risk = params.stop_atr * float(s.atr14[risk_atr_pos])
    entry = float(s.open[entry_pos])
    if not (math.isfinite(risk) and risk > 0 and entry > 0):
        return None
    stop, target = entry - risk, entry + params.target_r * risk
    gross_r = _bar_barrier(
        s.open, s.high, s.low, s.close, entry_pos, last, entry=entry, stop=stop, target=target
    )
    exit_pos = last
    for k in range(params.hold):  # the bar the barrier resolved on, for dating the trade
        i = entry_pos + k
        if (k > 0 and s.open[i] <= stop) or s.low[i] <= stop or s.high[i] >= target:
            exit_pos = i
            break
    return entry, stop, target, gross_r, exit_pos


def find_signals(
    stocks: Mapping[str, StockBars], mkt2: Mapping[date, float], params: DriftParams
) -> list[tuple[str, int, float]]:
    """(code, event_pos, reaction) for every results event whose reaction clears the bar.
    Uses closes up to E+1 only, and the stock's liquidity at E+1."""

    out: list[tuple[str, int, float]] = []
    for code, s in stocks.items():
        for i in s.results_pos:
            if i < 1 or i + 1 >= len(s.close):
                continue
            base = mkt2.get(s.dates[i])
            if base is None or not s.liquid[i + 1]:
                continue
            reaction = float(s.close[i + 1] / s.close[i - 1] - 1) - base
            if reaction >= params.reaction_min:
                out.append((code, i, reaction))
    return out


def net_r_for(
    entry: float, stop: float, gross_r: float, *, costs: Any, risk_rupees: float, slip: float
) -> tuple[float, int] | None:
    """Net R after slippage and real delivery costs at policy sizing; None if unsizable."""

    risk_per_share = entry - stop
    qty = int(risk_rupees // risk_per_share)
    if qty < 1:
        return None
    exit_price = entry + gross_r * risk_per_share
    fill_in, fill_out = entry * (1 + slip), exit_price * (1 - slip)
    cost = float(
        costs.round_trip_cost(
            trade_type=TradeType.DELIVERY,
            qty=qty,
            entry_price=Decimal(str(round(fill_in, 2))),
            exit_price=Decimal(str(round(fill_out, 2))),
        ).total
    )
    pnl = (fill_out - fill_in) * qty - cost
    return pnl / (qty * risk_per_share), qty


def build_trades(
    stocks: Mapping[str, StockBars],
    mkt2: Mapping[date, float],
    params: DriftParams,
    *,
    costs: Any,
    risk_rupees: float,
    slip: float,
) -> tuple[list[DriftTrade], dict[str, int]]:
    counts = {"signals": 0, "incomplete": 0, "unsizable": 0}
    trades: list[DriftTrade] = []
    for code, i, reaction in find_signals(stocks, mkt2, params):
        counts["signals"] += 1
        s = stocks[code]
        got = resolve(s, i + 2, i + 1, params)
        if got is None:
            counts["incomplete"] += 1
            continue
        entry, stop, target, gross_r, exit_pos = got
        net = net_r_for(entry, stop, gross_r, costs=costs, risk_rupees=risk_rupees, slip=slip)
        if net is None:
            counts["unsizable"] += 1
            continue
        trades.append(
            DriftTrade(
                code=code,
                event_date=s.dates[i].isoformat(),
                decision_date=s.dates[i + 1].isoformat(),
                entry_date=s.dates[i + 2].isoformat(),
                exit_date=s.dates[exit_pos].isoformat(),
                entry_pos=i + 2,
                exit_pos=exit_pos,
                reaction=reaction,
                entry=entry,
                stop=stop,
                target=target,
                gross_r=gross_r,
                net_r=net[0],
                gross_return=gross_r * (entry - stop) / entry,
            )
        )
    trades.sort(key=lambda t: (t.entry_date, t.code))
    return trades, counts


def quiet_mask(s: StockBars, hold: int) -> np.ndarray:
    """True where an entry at that position keeps QUIET_SESSIONS clear of every results date
    on both sides of its whole holding window."""

    quiet = np.ones(len(s.close), dtype=bool)
    for r in s.results_pos:
        quiet[max(0, r - hold - QUIET_SESSIONS) : r + QUIET_SESSIONS + 1] = False
    return quiet


def same_stock_random_time(
    trades: Sequence[DriftTrade],
    stocks: Mapping[str, StockBars],
    params: DriftParams,
    *,
    cohorts: int = NULL_COHORTS,
    seed: int = SEED,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    means = np.zeros(cohorts)
    used = 0
    pools: dict[str, list[int]] = {}
    for t in trades:
        s = stocks[t.code]
        if t.code not in pools:
            quiet = quiet_mask(s, params.hold)
            pools[t.code] = [
                p for p in range(2, len(s.close) - params.hold) if s.liquid[p - 1] and quiet[p]
            ]
        pool = pools[t.code]
        if not pool:
            continue
        used += 1
        picks = rng.choice(pool, size=cohorts, replace=True)
        for m, p in enumerate(picks):
            got = resolve(s, int(p), int(p) - 1, params)
            means[m] += got[3] if got else 0.0
    return _null_summary(means / max(used, 1), trades, used)


def random_stock_same_date(
    trades: Sequence[DriftTrade],
    stocks: Mapping[str, StockBars],
    params: DriftParams,
    *,
    cohorts: int = NULL_COHORTS,
    seed: int = SEED + 1,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    positions = {code: s.pos_of() for code, s in stocks.items()}
    quiet = {code: quiet_mask(s, params.hold) for code, s in stocks.items()}
    by_date: dict[str, list[tuple[str, int]]] = {}
    for t in trades:
        if t.entry_date in by_date:
            continue
        day = date.fromisoformat(t.entry_date)
        pool = []
        for code, s in stocks.items():
            p = positions[code].get(day)
            if p is None or p < 1 or p + params.hold > len(s.close):
                continue
            if s.liquid[p - 1] and quiet[code][p]:
                pool.append((code, p))
        by_date[t.entry_date] = pool
    means = np.zeros(cohorts)
    used = 0
    for t in trades:
        pool = by_date[t.entry_date]
        if not pool:
            continue
        used += 1
        for m, k in enumerate(rng.integers(0, len(pool), size=cohorts)):
            code, p = pool[int(k)]
            got = resolve(stocks[code], p, p - 1, params)
            means[m] += got[3] if got else 0.0
    return _null_summary(means / max(used, 1), trades, used)


def _null_summary(
    cohort_means: np.ndarray, trades: Sequence[DriftTrade], used: int
) -> dict[str, Any]:
    real = float(np.mean([t.gross_r for t in trades])) if trades else 0.0
    null = float(cohort_means.mean())
    return {
        "trades_matched": used,
        "cohorts": len(cohort_means),
        "real_mean_gross_r": real,
        "null_mean_gross_r": null,
        "margin_r": real - null,
        "p_value": float(np.mean(cohort_means >= real)),
    }


def to_harness_trades(
    trades: Sequence[DriftTrade], stocks: Mapping[str, StockBars]
) -> tuple[list[HarnessTrade], dict[str, tuple[Any, Any, Any, Any]]]:
    """House convention for daily candidates (`run_lab_candidate.to_harness_trades`): the
    random-entry window is the trade's own holding window."""

    harness = [
        HarnessTrade(
            scrip_code=t.code,
            entry_at=t.entry_date,
            exit_at=t.exit_date,
            entry_bar=t.entry_pos,
            window_start=t.entry_pos,
            window_end=t.exit_pos,
            entry=t.entry,
            stop=t.stop,
            target=t.target,
            gross_r=t.gross_r,
            net_r=t.net_r,
        )
        for t in trades
    ]
    codes = {t.code for t in trades}
    bars = {
        code: (stocks[code].open, stocks[code].high, stocks[code].low, stocks[code].close)
        for code in codes
    }
    return harness, bars


def describe(trades: Sequence[DriftTrade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    net = np.array([t.net_r for t in trades])
    gross = np.array([t.gross_r for t in trades])
    ret = np.array([t.gross_return for t in trades])
    by_year: dict[str, dict[str, float]] = {}
    for year in sorted({t.entry_date[:4] for t in trades}):
        sub = [t for t in trades if t.entry_date.startswith(year)]
        by_year[year] = {
            "trades": len(sub),
            "net_r": round(float(np.mean([t.net_r for t in sub])), 4),
            "gross_return_pct": round(float(np.mean([t.gross_return for t in sub])) * 100, 2),
        }
    cut = int(len(trades) * 0.8)
    recent = trades[cut:]
    return {
        "trades": len(trades),
        "first_entry": trades[0].entry_date,
        "last_entry": trades[-1].entry_date,
        "mean_gross_r": float(gross.mean()),
        "mean_net_r": float(net.mean()),
        "net_r_ci95": [
            float(net.mean() - 1.96 * net.std(ddof=1) / math.sqrt(len(net))),
            float(net.mean() + 1.96 * net.std(ddof=1) / math.sqrt(len(net))),
        ],
        "win_rate_net": float((net > 0).mean()),
        "mean_gross_return_pct": float(ret.mean() * 100),
        "median_gross_return_pct": float(np.median(ret) * 100),
        "most_recent_20pct": {
            "trades": len(recent),
            "from": recent[0].entry_date if recent else None,
            "mean_net_r": float(np.mean([t.net_r for t in recent])) if recent else None,
        },
        "by_year": by_year,
    }


def load_nse(root: Path) -> tuple[dict[str, StockBars], dict[str, str], pd.DataFrame | None]:
    from tradedesk.config import load_config
    from tradedesk.leader_discovery import _connect_read_only
    from tradedesk.potential_movers import read_results_dates

    settings = load_config(root)
    db = root / "data/tradedesk.duckdb"
    con = _connect_read_only(db)
    try:
        equities = {
            code: symbol
            for code, symbol in con.execute(
                "SELECT scrip_code, trading_symbol FROM instruments "
                "WHERE kind='equity' AND exch='NSE'"
            ).fetchall()
        }
        frame = con.execute(
            "SELECT scrip_code, ts, open, high, low, close, volume FROM candles "
            "WHERE interval='1day' ORDER BY scrip_code, ts"
        ).df()
        bench = con.execute(
            "SELECT scrip_code FROM instruments WHERE kind='index' AND exch='NSE' "
            "AND (trading_symbol = ? OR symbol_name = ? OR name = ?) LIMIT 1",
            [settings.universe.benchmark] * 3,
        ).fetchone()
    finally:
        con.close()
    frame["date"] = (
        pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    )
    results = read_results_dates("nse", db)
    stocks: dict[str, StockBars] = {}
    for code, group in frame[frame["scrip_code"].isin(equities)].groupby("scrip_code"):
        bars = build_stock_bars(group, str(code), results.get(str(code), []))
        if bars is not None:
            stocks[str(code)] = bars
    benchmark = None
    if bench is not None:
        b = frame[frame["scrip_code"] == bench[0]].drop_duplicates("date").sort_values("date")
        benchmark = b.set_index(pd.DatetimeIndex(pd.to_datetime(b["date"])))[
            ["open", "high", "low", "close", "volume"]
        ]
    return stocks, equities, benchmark


def main(root: Path | None = None) -> dict[str, Any]:
    from tradedesk.config import load_config
    from tradedesk.engine.indicators import daily_features
    from tradedesk.markets.market import nse_market
    from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json
    from tradedesk_lab.harness.gauntlet import run_gauntlet
    from tradedesk_lab.harness.regime_split import classify_market_regime
    from tradedesk_lab.harness.sensitivity import sweep_parameter
    from tradedesk_lab.harness.spec import KillCriteria

    root = root or ROOT
    settings = load_config(root)
    market = nse_market(settings)
    costs = market.costs
    slip = float(costs.slippage_pct)
    risk_rupees = float(settings.risk.trading_capital) * float(settings.risk.max_risk_per_trade_pct)
    params = DriftParams(hold=int(settings.risk.max_hold_sessions))

    stocks, symbols, benchmark = load_nse(root)
    mkt2 = market_two_day_median(stocks)
    trades, counts = build_trades(
        stocks, mkt2, params, costs=costs, risk_rupees=risk_rupees, slip=slip
    )

    def expectancy_at(**change: float) -> float:
        varied = replace(params, **change)
        got, _ = build_trades(
            stocks, mkt2, varied, costs=costs, risk_rupees=risk_rupees, slip=slip
        )
        return float(np.mean([t.net_r for t in got])) if got else -10.0

    sensitivity = [
        sweep_parameter(name, getattr(params, name), lambda v, n=name: expectancy_at(**{n: v}))
        for name in ("reaction_min", "stop_atr", "target_r")
    ]

    regime_labels = None
    if benchmark is not None and len(benchmark) > 60:
        feats = daily_features(benchmark.astype(float))
        regimes = classify_market_regime(feats)
        lookup = dict(zip(feats.index, regimes, strict=True))
        regime_labels = [str(lookup.get(pd.Timestamp(t.entry_date), "unknown")) for t in trades]

    harness_trades, bars = to_harness_trades(trades, stocks)
    kill = KillCriteria(
        min_net_expectancy_r=0.05, min_sample_size=500, max_drawdown_pct=0.30, max_losing_streak=10
    )
    report = run_gauntlet(
        STRATEGY_NAME,
        harness_trades,
        bars,
        kill,
        n_null_cohorts=1000,
        n_monte_carlo=5000,
        sensitivity=sensitivity,
        risk_per_trade_pct=float(settings.risk.max_risk_per_trade_pct),
        regime_labels=regime_labels,
        walk_forward_splits=4,
    )
    nulls = {
        "same_stock_random_time": same_stock_random_time(trades, stocks, params),
        "random_stock_same_date": random_stock_same_date(trades, stocks, params),
    }
    margin = 0.10
    beats_strong_nulls = all(
        n["p_value"] < 0.05 and n["margin_r"] >= margin for n in nulls.values()
    )
    result = {
        "strategy_name": STRATEGY_NAME,
        "stopped_at": report.stopped_at,
        "stages": report.stages,
        "kill_criteria": report.kill_criteria,
        "trade_count": len(trades),
        "passed": report.stopped_at is None and beats_strong_nulls,
        "rule": asdict(params),
        "sizing": {"risk_rupees": risk_rupees, "slippage_pct": slip},
        "signal_counts": counts,
        "strong_nulls": nulls,
        "strong_nulls_required_margin_r": margin,
        "beats_strong_nulls": beats_strong_nulls,
        "descriptive": describe(trades),
        "authority": "research_only",
        "examples": [
            {**asdict(t), "symbol": symbols.get(t.code, t.code)} for t in trades[-10:]
        ],
    }
    destination = OUTPUT / "harness" / STRATEGY_NAME
    write_json(destination / "report.json", result)
    write_json(destination / "latest.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
