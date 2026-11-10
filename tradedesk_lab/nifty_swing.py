"""Nifty 50 swing strategies: the validation the owner's manual asks for before any paper trade.

docs/nifty-swing-manual.md (owner, 2026-11-11) sets the system: Nifty 50 cash stocks, long only,
held 2-10 sessions, at most 3 positions, 0.5% of capital risked a trade, reward at least twice the
risk, and no strategy traded on paper until it has passed on five or more years of history. This
study is that test. Research only: it never alerts, sizes a real order or trades.

UNIVERSE. The Nifty 50 as it stood on each day, never today's list: today's 50 from NSE
(ind_nifty50list.csv, read 2026-11-11) walked back through the 19 changes in NSE Indices' press
releases (data/m14_m18/index_changes; `CHANGES`). Walking back reproduces the 51 securities of
September 2016. Not modelled: Jio Financial's seven weeks as a 51st member after its 2023 demerger.
HDFC Ltd (merged 2023) and Tata Motors DVR have no stored prices, so their member-days are missing
and reported; every other past member is priced under its present symbol (`RENAMED`). A session
whose open is 30%+ away from the previous close is a split, bonus or demerger the feed left
unadjusted (HDFC Bank 2020-08, Nestle 2020-08, Kotak 2021-01, IOC 2021-22, Tata Motors 2025-10);
earlier prices are scaled to remove the jump and each one is listed in the report.

SIGNALS, each judged on a completed daily bar and bought at the next open. All need: the stock in
the index that day, 250 sessions of history, and the Nifty closing above its 200-day EMA (longs are
not opened in a falling market). A stock cannot signal the same strategy again within 5 sessions.
  ema_pullback     close > EMA50 > EMA200 and EMA20 > EMA50; the low touched the EMA20 today or
                   yesterday; today closes above the EMA20 and above yesterday's high.
  breakout_retest  a close above the prior 20-session high 2-10 sessions ago; today's low comes
                   back to within 1% of that level and the bar closes above it, up on the day.
  rel_strength     60-session return in the top 10 of the index, close > EMA50, and today is the
                   first new 10-session closing high.
  momentum         ADX(14) > 25 with +DI > -DI, MACD histogram positive and rising, RSI(14) from
                   55 to 70, close > EMA20, an up day on 1.5x its 20-session average volume.
  structure_break  the first close above the last confirmed swing high (3 bars each side) while
                   the last confirmed swing low is above the one before it, close > EMA50.
  liquidity_sweep  the low undercuts the lowest low of the prior 20 sessions but the bar closes
                   back above that low and in the top half of its range, close > EMA200.
  support_bounce   support is the lowest low of sessions 6-60 back; a low of the last 3 sessions
                   came within 2% of it, none of them closed below it, and today closes above
                   yesterday's high, close > EMA200.
Not testable on stored data, so absent: the 4-hour layer and intraday VWAP (15-minute history
starts 2024-09) and delivery volume (not stored).

TRADE, the same for every strategy. Stop = signal close - 1.5 x ATR(14), never moved down. Entry at
the next open; no trade if that open is more than 1 ATR above the signal close (chased) or at or
under the stop. R = entry - stop. Quantity = Rs 500 (0.5% of Rs 1 lakh) / R, whole shares, capped
at Rs 33,333 a position so three fit. Half is sold at +2R (target 1), after which the stop on the
rest is the entry price from the next session; the rest at +3R (target 2) or at the 10th session's
close. An open beyond the stop or a target fills at the open; a bar touching both the stop and a
target is a stop. 0.05% slippage on market and stop fills, none on target limits. Real charges per
leg from risk.yaml (intraday rates if sold on the entry day). Results are in R after charges.

BOOK. A strategy's own Rs 1 lakh book: at most 3 positions, one per stock, the strongest
60-session return first when signals exceed free slots, and no new entry in a week whose closed
trades have already lost 2% of capital.

PASS BAR (written 2026-11-11, before any result was seen; entries from 2022-01-01 are out of sample
and nothing was fitted to the earlier years either, each rule has one fixed setting).
APPROVED FOR PAPER, the manual's bar, needs all of:
  1. at least 100 trades, at least 40 of them out of sample;
  2. mean net R > 0 after charges both in sample and out of sample;
  3. mean net R > 0 in at least 60% of calendar years that have 10+ trades;
  4. its book's worst fall from a peak no more than 10% of capital, and no more than 15% in 95%
     of 2,000 random re-orderings of its trades;
  5. mean net R > 0 at both neighbouring settings of its one parameter (`SETTINGS`).
PROVEN EDGE, the agent's own bar, needs in addition:
  6. mean net R above the same weeks' any-day entries (every index stock bought on every allowed
     day with this same exit) with t >= 3.0, one entry week as one unit, and positive both in
     sample and out of sample. t >= 3 is about Bonferroni for seven strategies.
Long rules in a rising market make money without any skill, which is what bar 6 removes. A strategy
that meets 1-5 but not 6 may trade on paper, labelled "not proven against random". No setting, exit
or bar is changed after this run to rescue a failure.

RESULT (2026-11-11; signals 2017-09 to 2026-10, 2,518 sessions, 98.5% of member-days priced, the
Nifty above its 200-day EMA on 78% of sessions, 10 price jumps removed). ALL SEVEN FAIL, on every
part of the manual's bar, in sample and out of sample, and none beats any-day entries. No strategy
is approved for paper. Mean net R a trade (in sample, out of sample), then against any-day entries,
then the three-position book on Rs 1 lakh:
  any-day entries  85,618 trades, 40% win, -0.139R (-0.132, -0.147)
  ema_pullback      2,437 trades, 41% win, -0.109R (-0.058, -0.154), -0.027R t -1.1; -Rs 47,644
  breakout_retest   3,160 trades, 39% win, -0.198R (-0.151, -0.237), -0.085R t -3.2; -Rs 47,119
  rel_strength      1,633 trades, 38% win, -0.225R (-0.168, -0.280), -0.130R t -4.5; -Rs 68,519
  momentum            359 trades, 40% win, -0.174R (-0.192, -0.155), -0.040R t -0.7; -Rs 22,596
  structure_break   2,099 trades, 38% win, -0.210R (-0.185, -0.233), -0.184R t -6.8; -Rs 71,858
  liquidity_sweep     860 trades, 41% win, -0.138R (-0.136, -0.139), +0.008R t  0.2; -Rs 37,749
  support_bounce      632 trades, 40% win, -0.126R (-0.145, -0.111), -0.035R t -0.7; -Rs 27,171
Books fell 24% to 72% from their peaks; no rule made money in more than 3 of 10 years.
Why, from a read-only check the same day: before charges an any-day entry makes +0.002R, nothing.
The 1.5-ATR stop sits a median 3.7% under the entry, so Rs 500 of risk buys about Rs 12,800 of
stock, and the charges on that round trip (median Rs 63, 0.50%) are 0.14R of every trade. 43% of
trades hit the stop within ten sessions and 5% reach both targets. The same entries simply held
ten sessions make +0.26% (+0.08R) before charges: the stop and targets give up the market's drift
and add nothing. The best rule before charges is ema_pullback at +0.035R, a quarter of the charges.
Three rules are significantly WORSE than any-day entries: buying a Nifty 50 stock on a breakout
retest, a new high among the leaders or a structure break loses to buying it on any day.
Do not re-test these rules, this stop or these targets on this history. A different entry or exit
is a new hypothesis and needs its own bar written before it runs.

    python -m tradedesk_lab.nifty_swing
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.engine import indicators as ind
from tradedesk_lab import new_listings as nl
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "nifty_swing"
NIFTY = "NSE_40000001"
CAPITAL = 100_000.0
RISK_INR = 0.005 * CAPITAL
MAX_POSITIONS = 3
MAX_POSITION_INR = CAPITAL / MAX_POSITIONS
WEEK_LOSS_INR = 0.02 * CAPITAL
STOP_ATR, CHASE_ATR = 1.5, 1.0
T1_R, T2_R = 2.0, 3.0
MAX_HOLD = 10
SLIPPAGE = 0.0005
COOL_DOWN = 5
WARM_UP = 250
ARTIFACT_GAP = 0.30
OOS_FROM = date(2022, 1, 1)
PASS = {"min_trades": 100, "min_oos": 40, "min_year_share": 0.6, "min_year_trades": 10,
        "max_fall": 0.10, "max_fall_mc": 0.15, "min_t": 3.0}  # fmt: skip
SHUFFLES = 2000

# NSE's list on 2026-11-11 (nsearchives.nseindia.com/content/indices/ind_nifty50list.csv).
CURRENT = (
    "ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJAJFINSV BAJFINANCE BEL "
    "BHARTIARTL BSE CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE "
    "HINDALCO HINDUNILVR ICICIBANK INDIGO INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI "
    "MAXHEALTH NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA "
    "TATACONSUM TATASTEEL TCS TECHM TITAN TMPV TRENT ULTRACEMCO"
).split()
# (effective date, included, excluded), symbols as each release printed them.
CHANGES: tuple[tuple[date, tuple[str, ...], tuple[str, ...]], ...] = (
    (date(2017, 3, 31), ("IBULHSGFIN", "IOC"), ("BHEL", "IDEA")),
    (date(2017, 5, 26), ("VEDL",), ("GRASIM",)),
    (date(2017, 9, 29), ("BAJFINANCE", "HINDPETRO", "UPL"),
     ("ACC", "BANKBARODA", "TATAPOWER", "TATAMTRDVR")),
    (date(2018, 4, 2), ("BAJAJFINSV", "GRASIM", "TITAN"), ("AMBUJACEM", "AUROPHARMA", "BOSCHLTD")),
    (date(2018, 9, 28), ("JSWSTEEL",), ("LUPIN",)),
    (date(2019, 3, 29), ("BRITANNIA",), ("HINDPETRO",)),
    (date(2019, 9, 27), ("NESTLEIND",), ("IBULHSGFIN",)),
    (date(2020, 3, 19), ("SHREECEM",), ("YESBANK",)),
    (date(2020, 7, 31), ("HDFCLIFE",), ("VEDL",)),
    (date(2020, 9, 25), ("DIVISLAB", "SBILIFE"), ("INFRATEL", "ZEEL")),
    (date(2021, 3, 31), ("TATACONSUM",), ("GAIL",)),
    (date(2022, 3, 31), ("APOLLOHOSP",), ("IOC",)),
    (date(2022, 9, 30), ("ADANIENT",), ("SHREECEM",)),
    (date(2023, 7, 13), ("LTIM",), ("HDFC",)),
    (date(2024, 3, 28), ("SHRIRAMFIN",), ("UPL",)),
    (date(2024, 9, 30), ("BEL", "TRENT"), ("DIVISLAB", "LTIM")),
    (date(2025, 3, 28), ("JIOFIN", "ZOMATO"), ("BPCL", "BRITANNIA")),
    (date(2025, 9, 30), ("INDIGO", "MAXHEALTH"), ("HEROMOTOCO", "INDUSINDBK")),
    (date(2026, 9, 30), ("BSE",), ("WIPRO",)),
)  # fmt: skip
RENAMED = {"ZOMATO": "ETERNAL", "IBULHSGFIN": "SAMMAANCAP", "INFRATEL": "INDUSTOWER",
           "LTIM": "LTM", "TATAMOTORS": "TMPV"}  # fmt: skip
NO_PRICES = ("HDFC", "TATAMTRDVR")
# One parameter a strategy: (lower neighbour, the setting used, upper neighbour).
SETTINGS: dict[str, tuple[float, float, float]] = {
    "ema_pullback": (1, 2, 3),  # sessions back the EMA20 touch may be
    "breakout_retest": (7, 10, 13),  # sessions back the breakout may be
    "rel_strength": (7, 10, 13),  # top-N by 60-session return
    "momentum": (20, 25, 30),  # ADX floor
    "structure_break": (2, 3, 4),  # bars each side of a swing point
    "liquidity_sweep": (15, 20, 25),  # sessions of lows swept
    "support_bounce": (45, 60, 75),  # sessions the support looks back
}


def _now(symbol: str) -> str:
    return RENAMED.get(symbol, symbol)


def initial_members() -> set[str]:
    """The index before the first change, in today's symbols, by undoing every change."""

    state = set(CURRENT)
    for _, included, excluded in reversed(CHANGES):
        adds, drops = {_now(s) for s in included}, {_now(s) for s in excluded}
        if not adds <= state or drops & state:
            raise ValueError(f"index changes do not chain at {included} / {excluded}")
        state = (state - adds) | drops
    return state


def membership(days: Sequence[date], columns: Sequence[str]) -> pd.DataFrame:
    """day x symbol: was the stock in the Nifty 50 that day (unpriced members get a column)."""

    state = initial_members()
    names = sorted(set(columns) | state | {_now(s) for _, inc, _ in CHANGES for s in inc})
    out = np.zeros((len(days), len(names)), dtype=bool)
    at = {n: i for i, n in enumerate(names)}
    step = 0
    for row, day in enumerate(days):
        while step < len(CHANGES) and CHANGES[step][0] <= day:
            _, included, excluded = CHANGES[step]
            state = (state - {_now(s) for s in excluded}) | {_now(s) for s in included}
            step += 1
        out[row, [at[s] for s in state]] = True
    return pd.DataFrame(out, index=list(days), columns=names)


@dataclass(frozen=True)
class Bars:
    open: pd.DataFrame  # day x symbol
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    nifty: pd.Series  # close


def load(db: Any = nl.DB) -> Bars:
    import duckdb

    wanted = sorted({_now(s) for s in CURRENT} | initial_members()
                    | {_now(s) for _, inc, _ in CHANGES for s in inc})  # fmt: skip
    con = duckdb.connect(str(db), read_only=True)
    try:
        frame = con.execute(
            "SELECT i.trading_symbol AS symbol, c.ts, c.open, c.high, c.low, c.close, c.volume "
            "FROM candles c JOIN instruments i ON i.scrip_code = c.scrip_code "
            "WHERE c.interval = '1day' AND i.exch = 'NSE' AND i.series = 'EQ' "
            "AND list_contains(?, i.trading_symbol)", [wanted],
        ).df()  # fmt: skip
        index = con.execute(
            "SELECT ts, close FROM candles WHERE interval = '1day' AND scrip_code = ?", [NIFTY]
        ).df()
    finally:
        con.close()

    def day(ts: pd.Series) -> pd.Series:
        return pd.to_datetime(ts, unit="s", utc=True).dt.tz_convert(IST).dt.date

    index["day"] = day(index["ts"])
    nifty = index.drop_duplicates("day", keep="last").set_index("day")["close"].sort_index()
    frame["day"] = day(frame["ts"])
    piv = {
        k: frame.pivot_table(index="day", columns="symbol", values=k, aggfunc="last")
        .reindex(nifty.index)
        for k in ("open", "high", "low", "close", "volume")
    }
    return Bars(piv["open"], piv["high"], piv["low"], piv["close"], piv["volume"], nifty)


def adjust(bars: Bars) -> tuple[Bars, list[dict[str, Any]]]:
    """Remove unadjusted corporate-action jumps (see the module docstring): prices before a
    session that opens 30%+ away from the prior close are scaled so that open equals that close."""

    factor = pd.DataFrame(1.0, index=bars.close.index, columns=bars.close.columns)
    found = []
    for symbol in bars.close.columns:
        close = bars.close[symbol].dropna()
        gap = bars.open[symbol].reindex(close.index) / close.shift(1)
        for day_, ratio in gap[(gap - 1).abs() >= ARTIFACT_GAP].items():
            factor.loc[factor.index < day_, symbol] *= float(ratio)
            found.append({"symbol": symbol, "day": str(day_), "ratio": round(float(ratio), 4)})
    scaled = Bars(bars.open * factor, bars.high * factor, bars.low * factor, bars.close * factor,
                  bars.volume / factor, bars.nifty)  # fmt: skip
    return scaled, found


@dataclass(frozen=True)
class Features:
    ema20: pd.DataFrame
    ema50: pd.DataFrame
    ema200: pd.DataFrame
    rsi: pd.DataFrame
    hist: pd.DataFrame  # MACD histogram
    adx: pd.DataFrame
    plus_di: pd.DataFrame
    minus_di: pd.DataFrame
    atr: pd.DataFrame
    rel_volume: pd.DataFrame
    ret60: pd.DataFrame
    seasoned: pd.DataFrame  # 250+ sessions of history
    regime_ok: pd.Series  # Nifty close above its 200-day EMA


def features(bars: Bars) -> Features:
    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in (
        "ema20", "ema50", "ema200", "rsi", "hist", "adx", "plus_di", "minus_di", "atr",
        "rel_volume", "ret60", "seasoned")}  # fmt: skip
    for symbol in bars.close.columns:
        df = pd.DataFrame({
            "high": bars.high[symbol], "low": bars.low[symbol], "close": bars.close[symbol],
            "volume": bars.volume[symbol],
        }).dropna()  # fmt: skip
        if df.empty:
            continue
        close = df["close"]
        dmi = ind.adx(df, 14)
        made = {
            "ema20": ind.ema(close, 20), "ema50": ind.ema(close, 50), "ema200": ind.ema(close, 200),
            "rsi": ind.rsi(close, 14), "hist": ind.macd(close)[2], "adx": dmi["adx"],
            "plus_di": dmi["plus_di"], "minus_di": dmi["minus_di"], "atr": ind.atr(df, 14),
            "rel_volume": ind.volume_ratio(df, 20), "ret60": close / close.shift(60) - 1,
            "seasoned": pd.Series(np.arange(len(df)) >= WARM_UP, index=df.index),
        }  # fmt: skip
        for name, series in made.items():
            cols[name][symbol] = series
    frames = {
        name: pd.DataFrame(made).reindex(index=bars.close.index, columns=bars.close.columns)
        for name, made in cols.items()
    }
    frames["seasoned"] = frames["seasoned"].eq(True)
    nifty_ok = (bars.nifty > ind.ema(bars.nifty, 200)) & (np.arange(len(bars.nifty)) >= WARM_UP)
    return Features(**frames, regime_ok=nifty_ok)


# --------------------------------------------------------------------------- signals


def _prior_swing(points: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(last, the one before it) of the confirmed swing points known on each day."""

    last = points.ffill()
    before = pd.DataFrame(index=points.index, columns=points.columns, dtype=float)
    for symbol in points.columns:
        seen = points[symbol].dropna()
        before[symbol] = seen.shift(1).reindex(points.index).ffill()
    return last, before


def raw_signal(name: str, bars: Bars, f: Features, members: pd.DataFrame, setting: float) -> (
    pd.DataFrame
):  # fmt: skip
    """The strategy's condition on each completed bar, before the shared gates."""

    o, h, lo, c = bars.open, bars.high, bars.low, bars.close
    n = int(setting)
    if name == "ema_pullback":
        touched = (lo <= f.ema20).astype(float).rolling(n, min_periods=1).max() > 0
        trend = (c > f.ema50) & (f.ema50 > f.ema200) & (f.ema20 > f.ema50)
        return trend & touched & (c > f.ema20) & (c > h.shift(1))
    if name == "breakout_retest":
        level = h.rolling(20, min_periods=20).max().shift(1)
        broke = c > level
        hit = pd.DataFrame(False, index=c.index, columns=c.columns)
        for back in range(2, n + 1):
            was = level.shift(back)
            hit |= broke.shift(back, fill_value=False) & (lo <= was * 1.01) & (c > was)
        return hit & (c > o)
    if name == "rel_strength":
        rank = f.ret60.where(members.reindex(columns=c.columns)).rank(axis=1, ascending=False)
        top = c >= c.rolling(10, min_periods=10).max()
        return (rank <= n) & (c > f.ema50) & top & ~top.shift(1, fill_value=False)
    if name == "momentum":
        return (
            (f.adx > setting) & (f.plus_di > f.minus_di) & (f.hist > 0) & (f.hist > f.hist.shift(1))
            & (f.rsi >= 55) & (f.rsi <= 70) & (c > f.ema20) & (c > o) & (f.rel_volume >= 1.5)
        )  # fmt: skip
    if name == "structure_break":
        width = 2 * n + 1
        highs = h.where(h == h.rolling(width, center=True, min_periods=width).max()).shift(n)
        lows = lo.where(lo == lo.rolling(width, center=True, min_periods=width).min()).shift(n)
        swing_high = highs.ffill()
        last_low, low_before = _prior_swing(lows)
        return (c > swing_high) & (c.shift(1) <= swing_high) & (last_low > low_before) & (
            c > f.ema50
        )
    if name == "liquidity_sweep":
        floor = lo.rolling(n, min_periods=n).min().shift(1)
        return (lo < floor) & (c > floor) & (c >= (h + lo) / 2) & (c > f.ema200)
    if name == "support_bounce":
        support = lo.rolling(n - 5, min_periods=n - 5).min().shift(6)
        near = lo.rolling(3, min_periods=3).min() <= support * 1.02
        held = c.rolling(3, min_periods=3).min() >= support
        return near & held & (c > h.shift(1)) & (c > f.ema200)
    raise ValueError(name)


def signals(name: str, bars: Bars, f: Features, members: pd.DataFrame, setting: float) -> (
    np.ndarray
):  # fmt: skip
    """day x symbol bools: the condition, the shared gates, then the 5-session cool-down."""

    inside = members.reindex(index=bars.close.index, columns=bars.close.columns).eq(True)
    raw = raw_signal(name, bars, f, members, setting).eq(True)
    gated = (raw & inside & f.seasoned).to_numpy() & f.regime_ok.to_numpy()[:, None]
    out = np.zeros_like(gated)
    last = np.full(gated.shape[1], -COOL_DOWN - 1)
    for row in range(gated.shape[0]):
        take = gated[row] & (row - last > COOL_DOWN)
        out[row] = take
        last[take] = row
    return out


# --------------------------------------------------------------------------- the trade


def cost_model() -> Callable[[int, float, float, bool], float]:
    """Rupee charges of buying and selling `qty` shares (intraday rates if the same day)."""

    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    model = EquityCostModel(load_config(nl.ROOT).risk.costs)

    def cost(qty: int, entry: float, exit_px: float, same_day: bool) -> float:
        if qty <= 0:
            return 0.0
        kind = TradeType.INTRADAY if same_day else TradeType.DELIVERY
        charged = model.round_trip_cost(
            trade_type=kind, qty=qty, entry_price=Decimal(str(round(entry, 2))),
            exit_price=Decimal(str(round(exit_px, 2))),
        )  # fmt: skip
        return float(charged.total)

    return cost


def _ahead(values: np.ndarray, k: int) -> np.ndarray:
    """Row d holds row d+k (nan past the end)."""

    out = np.full(values.shape, np.nan)
    out[:-k] = values[k:]
    return out


@dataclass(frozen=True)
class Outcomes:
    """Every (signal day, stock) traded with the shared plan; nan where no trade could be made."""

    entry: np.ndarray
    stop: np.ndarray
    qty: np.ndarray
    net_inr: np.ndarray
    net_r: np.ndarray
    exit_row: np.ndarray  # row of the last exit


def outcomes(bars: Bars, atr: pd.DataFrame, cost: Callable[[int, float, float, bool], float],
             only: np.ndarray | None = None) -> Outcomes:  # fmt: skip
    """`only` limits the work to the cells that can ever be asked for."""

    o, h = bars.open.to_numpy(float), bars.high.to_numpy(float)
    lo, c = bars.low.to_numpy(float), bars.close.to_numpy(float)
    a = atr.to_numpy(float)
    first_open = _ahead(o, 1)
    stop0 = c - STOP_ATR * a
    entry = first_open * (1 + SLIPPAGE)
    risk = entry - stop0
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = (first_open <= c + CHASE_ATR * a) & (first_open > stop0) & (risk > 0)
        qty = np.floor(np.minimum(RISK_INR / risk, MAX_POSITION_INR / entry))
    ok &= np.isfinite(qty) & (qty >= 1)
    if only is not None:
        ok &= only
    qty = np.where(ok, qty, 0.0)
    first = np.ceil(qty / 2)
    t1, t2 = entry + T1_R * risk, entry + T2_R * risk
    stop = stop0.copy()
    live = [ok.copy(), ok & (qty - first > 0)]  # the two halves
    price = [np.full(c.shape, np.nan), np.full(c.shape, np.nan)]
    step = [np.zeros(c.shape, dtype=int), np.zeros(c.shape, dtype=int)]
    broken = np.zeros(c.shape, dtype=bool)
    for k in range(1, MAX_HOLD + 1):
        ok_open, hk, lk, ck = _ahead(o, k), _ahead(h, k), _ahead(lo, k), _ahead(c, k)
        holding = live[0] | live[1]
        broken |= holding & ~np.isfinite(ck)
        with np.errstate(invalid="ignore"):
            gapped = holding & (ok_open <= stop)
            stopped = gapped | (holding & (lk <= stop))
            stop_fill = np.where(gapped, ok_open, stop) * (1 - SLIPPAGE)
            reached = [~stopped & live[0] & (hk >= t1), ~stopped & live[1] & (hk >= t2)]
        target = [np.maximum(t1, ok_open), np.maximum(t2, ok_open)]
        for half in (0, 1):
            out = live[half] & stopped
            price[half][out], step[half][out] = stop_fill[out], k
            won = reached[half]
            price[half][won], step[half][won] = target[half][won], k
            live[half] = live[half] & ~out & ~won
            if k == MAX_HOLD:
                left = live[half]
                price[half][left], step[half][left] = ck[left] * (1 - SLIPPAGE), k
                live[half] = np.zeros(c.shape, dtype=bool)
        stop = np.where(reached[0], np.maximum(stop, entry), stop)
    ok &= ~broken
    net_inr = np.full(c.shape, np.nan)
    for row, col in zip(*np.nonzero(ok), strict=True):
        q, q1, e = int(qty[row, col]), int(first[row, col]), float(entry[row, col])
        legs = [(q1, float(price[0][row, col]), int(step[0][row, col]))]
        if q > q1:
            legs.append((q - q1, float(price[1][row, col]), int(step[1][row, col])))
        if len(legs) == 2 and legs[0][1:] == legs[1][1:]:
            legs = [(q, legs[0][1], legs[0][2])]
        net_inr[row, col] = sum(n * (px - e) - cost(n, e, px, k == 1) for n, px, k in legs)
    with np.errstate(invalid="ignore", divide="ignore"):
        net_r = net_inr / (qty * risk)
    rows = np.arange(c.shape[0])[:, None] + np.maximum(step[0], step[1])
    return Outcomes(
        np.where(ok, entry, np.nan), np.where(ok, stop0, np.nan), qty, net_inr, net_r,
        np.where(ok, rows, -1),
    )  # fmt: skip


# --------------------------------------------------------------------------- judging


def trades_of(mask: np.ndarray, out: Outcomes, days: Sequence[date], symbols: Sequence[str],
              strength: np.ndarray) -> pd.DataFrame:  # fmt: skip
    """One row per signal that became a trade; the entry is the session after the signal."""

    rows, cols = np.nonzero(mask & np.isfinite(out.net_r))
    return pd.DataFrame({
        "signal_row": rows, "entry_row": rows + 1, "exit_row": out.exit_row[rows, cols],
        "day": [days[r + 1] for r in rows], "symbol": [symbols[j] for j in cols],
        "net_r": out.net_r[rows, cols], "net_inr": out.net_inr[rows, cols],
        "strength": strength[rows, cols],
    }).sort_values(["entry_row", "symbol"], ignore_index=True)  # fmt: skip


def _week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-{week:02d}"


def vs_any_day(trades: pd.DataFrame, base: pd.DataFrame) -> dict[str, Any]:
    """Mean net R over the same weeks' any-day entries; one entry week is one unit."""

    if trades.empty:
        return {"mean": None, "t": None, "weeks": 0, "in_sample": None, "out_of_sample": None}
    mine = trades.groupby(trades["day"].map(_week))["net_r"].mean()
    theirs = base.groupby(base["day"].map(_week))["net_r"].mean()
    excess = (mine - theirs.reindex(mine.index)).dropna()
    first_day = trades.groupby(trades["day"].map(_week))["day"].min().reindex(excess.index)
    late = first_day >= OOS_FROM
    n = len(excess)
    sd = float(excess.std(ddof=1)) if n > 1 else 0.0
    mean = float(excess.mean()) if n else None
    return {
        "mean": mean, "t": mean / (sd / math.sqrt(n)) if sd > 0 and mean is not None else None,
        "weeks": n,
        "in_sample": float(excess[~late].mean()) if (~late).any() else None,
        "out_of_sample": float(excess[late].mean()) if late.any() else None,
    }  # fmt: skip


def book(trades: pd.DataFrame, days: Sequence[date]) -> pd.DataFrame:
    """The trades a Rs 1 lakh, three-position book would hold (see the module docstring)."""

    taken, held = [], []  # held: (exit_row, symbol, net_inr)
    closed_week: dict[str, float] = {}
    order = trades.sort_values(["entry_row", "strength"], ascending=[True, False])
    for row in order.itertuples():
        still = []
        for exit_row, symbol, net in held:
            if exit_row < row.entry_row:
                week = _week(days[exit_row])
                closed_week[week] = closed_week.get(week, 0.0) + net
            else:
                still.append((exit_row, symbol, net))
        held = still
        if len(held) >= MAX_POSITIONS or any(s == row.symbol for _, s, _ in held):
            continue
        if closed_week.get(_week(days[row.entry_row]), 0.0) <= -WEEK_LOSS_INR:
            continue
        held.append((row.exit_row, row.symbol, row.net_inr))
        taken.append(row.Index)
    return trades.loc[taken].sort_values(["exit_row", "entry_row"], ignore_index=True)


def worst_fall(results: Sequence[float]) -> float:
    """Largest fall from a running peak of the summed results, as a positive number."""

    if len(results) == 0:
        return 0.0
    curve = np.concatenate(([0.0], np.cumsum(np.asarray(results, dtype=float))))
    return float(np.max(np.maximum.accumulate(curve) - curve))


def losing_streak(results: Sequence[float]) -> int:
    longest = run = 0
    for value in results:
        run = run + 1 if value <= 0 else 0
        longest = max(longest, run)
    return longest


def stats(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0}
    r = trades["net_r"]
    won, lost = float(r[r > 0].sum()), float(-r[r < 0].sum())
    return {
        "trades": len(r), "win_rate": float((r > 0).mean()), "mean_net_r": float(r.mean()),
        "profit_factor": won / lost if lost > 0 else None,
        "net_inr": float(trades["net_inr"].sum()), "losing_streak": losing_streak(list(r)),
    }  # fmt: skip


def book_stats(held: pd.DataFrame, days: Sequence[date], seed: int = 20261111) -> dict[str, Any]:
    if held.empty:
        return {"trades": 0, "worst_fall": 0.0, "worst_fall_mc95": 0.0}
    results = held["net_inr"].to_numpy(float)
    weekly = held.groupby(held["exit_row"].map(lambda i: _week(days[i])))["net_inr"].sum()
    span = pd.period_range(pd.Timestamp(days[int(held["entry_row"].min())]),
                           pd.Timestamp(days[int(held["exit_row"].max())]), freq="W")  # fmt: skip
    per_week = np.zeros(max(len(span), len(weekly)))
    per_week[: len(weekly)] = weekly.to_numpy(float)  # the zero weeks only matter by count
    sd = float(per_week.std(ddof=1)) if len(per_week) > 1 else 0.0
    down = float(np.sqrt(np.mean(np.minimum(per_week, 0.0) ** 2)))
    rng = np.random.default_rng(seed)
    falls = [worst_fall(rng.permutation(results)) for _ in range(SHUFFLES)]
    return {
        **stats(held), "worst_fall": worst_fall(results) / CAPITAL,
        "worst_fall_mc95": float(np.quantile(falls, 0.95)) / CAPITAL,
        "sharpe": float(per_week.mean()) / sd * math.sqrt(52) if sd > 0 else None,
        "sortino": float(per_week.mean()) / down * math.sqrt(52) if down > 0 else None,
        "net_per_year_inr": float(results.sum()) / (len(per_week) / 52),
        "trades_per_week": len(results) / len(per_week),
    }  # fmt: skip


def judge(trades: pd.DataFrame, held: Mapping[str, Any], edge: Mapping[str, Any],
          neighbours: Sequence[float | None]) -> dict[str, Any]:  # fmt: skip
    """The pass bar of the module docstring, every failing part reported."""

    late = trades["day"] >= OOS_FROM if not trades.empty else pd.Series(dtype=bool)
    ins, oos = trades[~late], trades[late]
    years = trades.groupby(trades["day"].map(lambda d: d.year))["net_r"].agg(["mean", "count"])
    full = years[years["count"] >= PASS["min_year_trades"]]
    share = float((full["mean"] > 0).mean()) if len(full) else 0.0
    fails = []
    if len(trades) < PASS["min_trades"] or len(oos) < PASS["min_oos"]:
        fails.append(f"too few trades ({len(trades)}, {len(oos)} out of sample)")
    if not (len(ins) and ins["net_r"].mean() > 0):
        fails.append("loses in sample")
    if not (len(oos) and oos["net_r"].mean() > 0):
        fails.append("loses out of sample")
    if share < PASS["min_year_share"]:
        fails.append(f"positive in only {share:.0%} of years")
    if held.get("worst_fall", 0) > PASS["max_fall"]:
        fails.append("book falls more than 10%")
    if held.get("worst_fall_mc95", 0) > PASS["max_fall_mc"]:
        fails.append("book falls more than 15% in re-orderings")
    if not all(n is not None and n > 0 for n in neighbours):
        fails.append("a neighbouring setting loses")
    edge_fails = []
    if not ((edge["mean"] or 0) > 0 and (edge["t"] or 0) >= PASS["min_t"]):
        edge_fails.append(f"t {edge['t'] or 0:.2f} over any-day entries")
    if not all((edge[k] or 0) > 0 for k in ("in_sample", "out_of_sample")):
        edge_fails.append("not ahead of any-day entries in both periods")
    return {
        "approved_for_paper": not fails, "proven_edge": not fails and not edge_fails,
        "fails": fails, "edge_fails": edge_fails,
        "in_sample": stats(ins), "out_of_sample": stats(oos), "positive_year_share": share,
        "by_year": {int(y): {"mean_net_r": float(m), "trades": int(n)}
                    for y, (m, n) in years.iterrows()},
    }  # fmt: skip


def run() -> dict[str, Any]:
    raw = load()
    bars, artifacts = adjust(raw)
    days = list(bars.close.index)
    symbols = list(bars.close.columns)
    members = membership(days, symbols)
    f = features(bars)
    strength = f.ret60.to_numpy(float)
    inside = members.reindex(columns=symbols).to_numpy(bool) & f.seasoned.to_numpy()
    allowed = inside & f.regime_ok.to_numpy()[:, None]
    out = outcomes(bars, f.atr, cost_model(), allowed)
    base = trades_of(allowed, out, days, symbols, strength)
    member_days = int(members.to_numpy().sum())
    priced = int(members.reindex(columns=symbols).to_numpy().sum())
    report: dict[str, Any] = {
        "first_day": str(days[0]), "last_day": str(days[-1]), "sessions": len(days),
        "member_days": member_days, "member_days_priced": priced / member_days,
        "members_per_day": sorted({int(n) for n in members.sum(axis=1)}),
        "unpriced": list(NO_PRICES), "price_jumps_removed": artifacts,
        "regime_ok_share": float(f.regime_ok.iloc[WARM_UP:].mean()),
        "any_day": {**stats(base), "by_period": {
            "in_sample": stats(base[base["day"] < OOS_FROM]),
            "out_of_sample": stats(base[base["day"] >= OOS_FROM])}},
        "strategies": {},
    }  # fmt: skip
    for name, (low, used, high) in SETTINGS.items():
        trades = trades_of(signals(name, bars, f, members, used), out, days, symbols, strength)
        neighbours = [
            stats(trades_of(signals(name, bars, f, members, s), out, days, symbols, strength))
            .get("mean_net_r")
            for s in (low, high)
        ]
        held = book(trades, days)
        held_stats = book_stats(held, days)
        edge = vs_any_day(trades, base)
        report["strategies"][name] = {
            "setting": used, **stats(trades), "vs_any_day": edge, "book": held_stats,
            "neighbours": {"low": neighbours[0], "high": neighbours[1]},
            **judge(trades, held_stats, edge, neighbours),
        }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    return report


def main() -> None:
    report = run()
    print(f"{report['first_day']} to {report['last_day']}, {report['sessions']} sessions, "
          f"{report['member_days_priced']:.1%} of member-days priced, members per day "
          f"{report['members_per_day']}, {len(report['price_jumps_removed'])} price jumps "
          "removed")  # fmt: skip
    base = report["any_day"]
    print(f"any-day entries: {base['trades']} trades, mean {base['mean_net_r']:+.3f}R, "
          f"win {base['win_rate']:.0%}")  # fmt: skip
    for name, s in report["strategies"].items():
        if not s["trades"]:
            print(f"{name}: no trades")
            continue
        edge, held = s["vs_any_day"], s["book"]
        verdict = "FAILS"
        if s["approved_for_paper"]:
            verdict = "PROVEN EDGE" if s["proven_edge"] else "APPROVED FOR PAPER, not proven"
        print(
            f"{name}: {s['trades']} trades, win {s['win_rate']:.0%}, mean {s['mean_net_r']:+.3f}R "
            f"(in {s['in_sample'].get('mean_net_r', float('nan')):+.3f}, "
            f"out {s['out_of_sample'].get('mean_net_r', float('nan')):+.3f}), "
            f"PF {s['profit_factor'] or 0:.2f}, vs any-day {edge['mean'] or 0:+.3f}R "
            f"t {edge['t'] or 0:.2f}; book {held['trades']} trades, "
            f"Rs {held.get('net_inr', 0):+,.0f}, fall {held['worst_fall']:.1%} -> {verdict} "
            f"{s['fails'] + s['edge_fails']}"
        )


if __name__ == "__main__":
    main()
