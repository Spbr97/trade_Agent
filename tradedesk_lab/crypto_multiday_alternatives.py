"""Better crypto multi-day calls? Four slow trend rules, after Indian crypto tax (2026-10-20).

Research only. Reads a COPY of data/crypto.duckdb; writes
data/m14_m18/crypto_multiday_alternatives/.
Nothing here alerts, sizes or trades. Rules, costs, periods and the pass bar below were fixed
before any result was seen.

Why slow: the live crypto multi-day rules lost about Rs 1,018 per Rs 10,000 call in the week to
2026-10-20, no better than random picks. In India every winning crypto sale pays 30% tax and a
losing one cannot offset it (s.115BBH), and a CoinDCX round trip costs ~0.57% (0.2% fee each
side + 18% GST on the fee + 0.05% slippage each side). A rule that trades often pays tax on its
winners and keeps all its losers, so only rules that trade rarely and ride long trends can win.

Rules (signals on a daily close, traded at the next day's open, long only, spot only):
  btc_sma200   hold BTC while it closes above its 200-day average, else cash.
  eth_sma200   the same for ETH.
  btc_eth_dual half the money in a BTC sleeve, half in an ETH sleeve; each held while the coin
               is above its 200-day average AND up over the last 90 days.
  xs_momentum  every 7 days: among the 30 coins with the highest 60-day average turnover that
               have >= 365 days of history (stablecoins excluded), hold the 5 with the best
               28-day return, equal slots; only while BTC is above its 200-day average (checked
               daily; below it everything is sold). A holding leaving the top 5 is sold and its
               slot refilled at the next rebalance.
Costs: 0.2% fee + 18% GST on it, and 0.05% slippage, on every buy and every sell. Tax: 30% of
each sale's gain over its cost (fees included); losses earn no credit. The 1% TDS is a credit
against that tax, not a cost, so it is not charged (it does tie up cash until filing).

Periods: design 2021-01-01..2022-12-31 and holdout 2023-01-01..latest, each simulated from cash.
(The design period was first 2019-2022; the first run showed CoinDCX's own 2018-2020 daily
BTC history is corrupt - 143 impossible bars, e.g. a Rs 5.4 lakh open closing at Rs 0.29 on
1-3 BTC of volume - and none from 2021 on. The holdout, which decides the pass, is unchanged.)
Null: 300 random-timing versions per rule on the holdout - the single-coin and dual rules keep
their own in-market and in-cash spell lengths in shuffled order (same exposure, same number of
trades); xs_momentum keeps its in-market days and rebalance days but picks 5 random coins from
the same eligible 30.
Pass (all four, holdout): after-tax money grows (final equity > 1); beats its random-timing
null with p < 0.05/4; worst drawdown at most half of buy-and-hold BTC's in the same period; and
the design period also grows after tax. Return versus buy-and-hold BTC is reported, not required:
the point is BTC-like gains with much smaller falls, without paying 30% on churn.

Caveat: the coin list is CoinDCX's current one (delisted coins are missing), which flatters
xs_momentum; the single-coin rules do not have that bias.

Result (2026-10-20, holdout 2023-01-01..2026-10-20, after costs and tax): NONE PASSES.
  btc_buy_hold   x4.28, worst fall 47% (comparison only)
  btc_sma200     x2.42, fall 45%, 20 trades; random timing median x1.83, p 0.20
  eth_sma200     x2.06, fall 40%, 8 trades; random median x1.12, p 0.13
  btc_eth_dual   x1.83, fall 50%; random median x1.28, p 0.12
  xs_momentum    x0.34, fall 79%, 263 trades (better than random picks, p 0.000, but loses money)
The trend filters make money after tax but less than simply holding BTC, cut its falls only a
little and are not better than random timing; rotating between coins loses heavily to costs and
tax. Do not re-test these without new data.

Usage: python -m tradedesk_lab.crypto_multiday_alternatives --db <copy of data/crypto.duckdb>
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "crypto_multiday_alternatives"
FEE = 0.002 * 1.18
SLIP = 0.0005
TAX = 0.30
BTC, ETH = "CDX_BTCINR", "CDX_ETHINR"
STABLE = ("USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD")
DESIGN = (date(2021, 1, 1), date(2022, 12, 31))
HOLDOUT_START = date(2023, 1, 1)
NULL_DRAWS = 300
P_BAR = 0.05 / 4
TOP_LIQUID, PICKS, REBALANCE_DAYS = 30, 5, 7


def load(db: Path) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        frame = con.execute(
            "SELECT scrip_code, ts, open, close, volume FROM candles WHERE interval = '1day' "
            "ORDER BY scrip_code, ts"
        ).df()  # sorted, so a duplicated calendar day keeps its last bar
    finally:
        con.close()
    frame["day"] = (
        pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    )
    frame = frame[~frame["scrip_code"].str.contains("|".join(STABLE))]
    piv = {
        k: frame.pivot_table(
            index="day", columns="scrip_code", values=k, aggfunc="last"
        ).sort_index()
        for k in ("open", "close", "volume")
    }
    raw_close = piv["close"]
    piv["open"] = clean(piv["open"], raw_close)
    piv["close"] = clean(raw_close, raw_close).mask(  # a close impossibly far from its own open
        (raw_close / piv["open"] > 5) | (raw_close / piv["open"] < 0.2)
    )
    piv["turnover"] = piv["close"] * piv["volume"]
    piv["mark"] = piv["close"].ffill()  # last known close, for marks and forced sales
    return piv


def clean(prices: pd.DataFrame, closes: pd.DataFrame, limit: float = 5.0) -> pd.DataFrame:
    """Blank a price more than `limit` times away from the median of the previous 15
    closes. Added 2026-10-20 after the first run: 2019-2020 CoinDCX history has phantom bars
    (BTC closing at Rs 0 against an Rs 8 lakh open) that faked a 100% drawdown. Data
    correction only; no rule, cost or pass bar changed."""
    ref = closes.where(closes > 0).rolling(15, min_periods=1).median().shift(1)
    ratio = prices / ref
    bad = (prices <= 0) | (ratio > limit) | (ratio < 1 / limit)
    return prices.mask(bad)


# --- one portfolio: sleeves of money, each holding at most one coin at a time -------------


@dataclass
class Book:
    cash: float = 1.0
    lots: dict[str, tuple[float, float]] = field(default_factory=dict)  # coin -> (qty, cost)
    trades: list[dict[str, Any]] = field(default_factory=list)
    entered: dict[str, date] = field(default_factory=dict)

    def buy(self, coin: str, money: float, price: float, day: date) -> None:
        if money <= 0 or not np.isfinite(price) or price <= 0:
            return
        qty = money * (1 - FEE) / (price * (1 + SLIP))
        self.cash -= money
        self.lots[coin] = (qty, money)
        self.entered[coin] = day

    def sell(self, coin: str, price: float, day: date) -> None:
        qty, cost = self.lots.pop(coin)
        proceeds = qty * price * (1 - SLIP) * (1 - FEE)
        gain = proceeds - cost
        tax = TAX * gain if gain > 0 else 0.0
        self.cash += proceeds - tax
        self.trades.append(
            {
                "coin": coin,
                "entry": self.entered.pop(coin).isoformat(),
                "exit": day.isoformat(),
                "after_tax_return": (proceeds - tax) / cost - 1,
            }
        )

    def value(self, close: pd.Series) -> float:
        held = sum(q * float(close.get(c, np.nan)) for c, (q, _) in self.lots.items())
        return self.cash + (held if np.isfinite(held) else 0.0)


Targets = Callable[[int], list[str]]  # day index -> coins wanted after that day's close


def simulate(
    p: dict[str, pd.DataFrame], days: list[date], wanted: Targets, slots: int
) -> dict[str, Any]:
    """Signals at day i's close, trades at day i+1's open. A coin leaving the wanted set is
    sold; a coin joining it gets an equal share of the free cash per empty slot."""
    opens, marks = p["open"], p["mark"]
    book = Book()
    curve = []
    for i, day in enumerate(days[:-1]):
        nxt = days[i + 1]
        want = wanted(i)
        o = opens.loc[nxt]
        for coin in [c for c in book.lots if c not in want]:
            price = o.get(coin, np.nan)
            book.sell(coin, price if np.isfinite(price) else float(marks.loc[day, coin]), nxt)
        joining = [c for c in want if c not in book.lots and np.isfinite(o.get(c, np.nan))]
        empty = slots - len(book.lots)
        for coin in joining[:empty]:
            book.buy(coin, book.cash / max(1, empty), float(o[coin]), nxt)
            empty -= 1
        curve.append(book.value(marks.loc[nxt]))
    last = days[-1]
    for coin in list(book.lots):  # mark the end: sold at the last close, taxed like any sale
        book.sell(coin, float(marks.loc[last, coin]), last)
    curve[-1] = book.cash  # after the final sale and its tax
    equity = pd.Series(curve, index=days[1:])
    peak = equity.cummax()
    years = max((days[-1] - days[0]).days / 365.25, 1e-9)
    returns = [t["after_tax_return"] for t in book.trades]
    return {
        "final_equity": float(book.cash),
        "cagr": float(book.cash ** (1 / years) - 1) if book.cash > 0 else -1.0,
        "max_drawdown": float((1 - equity / peak).max()),
        "trades": len(returns),
        "win_rate": float(np.mean([r > 0 for r in returns])) if returns else None,
        "mean_trade_after_tax": float(np.mean(returns)) if returns else None,
    }


def sma_above(p: dict[str, pd.DataFrame], coin: str, n: int = 200) -> pd.Series:
    close = p["mark"][coin]  # last known close, so a blanked bar does not void the average
    return (close > close.rolling(n, min_periods=n).mean()).fillna(False)


def up_over(p: dict[str, pd.DataFrame], coin: str, n: int = 90) -> pd.Series:
    close = p["mark"][coin]
    return (close > close.shift(n)).fillna(False)


def single(state: pd.Series, coin: str) -> Targets:
    values = state.to_numpy()
    return lambda i: [coin] if values[i] else []


def run_single(p, days, state: pd.Series, coin: str) -> dict[str, Any]:  # noqa: ANN001
    return simulate(p, days, single(state.reindex(days).fillna(False), coin), 1)


def run_dual(p, days, states: dict[str, pd.Series]) -> dict[str, Any]:  # noqa: ANN001
    """Two independent half-money sleeves, combined."""
    parts = [run_single(p, days, s, c) for c, s in states.items()]
    final = float(np.mean([r["final_equity"] for r in parts]))
    years = max((days[-1] - days[0]).days / 365.25, 1e-9)
    return {
        "final_equity": final,
        "cagr": float(final ** (1 / years) - 1) if final > 0 else -1.0,
        "max_drawdown": max(r["max_drawdown"] for r in parts),  # conservative: the worse sleeve
        "trades": sum(r["trades"] for r in parts),
        "sleeves": {c: r for c, r in zip(states, parts, strict=True)},
    }


def eligible(p: dict[str, pd.DataFrame], days: list[date]) -> dict[int, list[str]]:
    turnover = p["turnover"].rolling(60, min_periods=60).mean()
    history = p["close"].notna().cumsum()
    out = {}
    for i, day in enumerate(days):
        if i % REBALANCE_DAYS:
            continue
        ok = turnover.loc[day][history.loc[day] >= 365].dropna()
        out[i] = list(ok.sort_values(ascending=False).index[:TOP_LIQUID])
    return out


def xs_targets(p, days, regime: pd.Series, pools: dict[int, list[str]], pick) -> Targets:  # noqa: ANN001
    on = regime.reindex(days).fillna(False).to_numpy()
    held: list[str] = []

    def wanted(i: int) -> list[str]:
        nonlocal held
        if not on[i]:
            held = []
        elif i in pools:
            held = pick(i, pools[i])
        return list(held)

    return wanted


def momentum_pick(p: dict[str, pd.DataFrame], days: list[date]):  # noqa: ANN201
    ret28 = p["close"] / p["close"].shift(28) - 1

    def pick(i: int, pool: list[str]) -> list[str]:
        r = ret28.loc[days[i], pool].dropna()
        return list(r.sort_values(ascending=False).index[:PICKS])

    return pick


def spells_null(state: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Same in-market and in-cash spell lengths as `state`, in random order."""
    values = state.to_numpy().astype(bool)
    runs, start = [], 0
    for k in range(1, len(values) + 1):
        if k == len(values) or values[k] != values[start]:
            runs.append((bool(values[start]), k - start))
            start = k
    ins = [n for v, n in runs if v]
    outs = [n for v, n in runs if not v]
    rng.shuffle(ins)
    rng.shuffle(outs)
    flag = bool(rng.integers(2))
    seq: list[bool] = []
    while ins or outs:
        pool = ins if flag else outs
        if pool:
            seq += [flag] * pool.pop()
        flag = not flag
    return pd.Series(seq[: len(values)], index=state.index)


def btc_buy_hold(p: dict[str, pd.DataFrame], days: list[date]) -> dict[str, Any]:
    return run_single(p, days, pd.Series(True, index=days), BTC)


def evaluate(p: dict[str, pd.DataFrame], seed: int = 7) -> dict[str, Any]:
    all_days = list(p["close"].index)
    periods = {
        "design": [d for d in all_days if DESIGN[0] <= d <= DESIGN[1]],
        "holdout": [d for d in all_days if d >= HOLDOUT_START],
    }
    states = {
        "btc_sma200": (BTC, sma_above(p, BTC)),
        "eth_sma200": (ETH, sma_above(p, ETH)),
    }
    dual_states = {c: sma_above(p, c) & up_over(p, c) for c in (BTC, ETH)}
    regime = sma_above(p, BTC)
    rng = np.random.default_rng(seed)
    report: dict[str, Any] = {"periods": {}, "rules": {}}
    for name, days in periods.items():
        report["periods"][name] = {
            "from": days[0].isoformat(),
            "to": days[-1].isoformat(),
            "btc_buy_hold": btc_buy_hold(p, days),
        }
    for name, days in periods.items():
        res: dict[str, Any] = {}
        for rule, (coin, state) in states.items():
            res[rule] = run_single(p, days, state, coin)
        res["btc_eth_dual"] = run_dual(p, days, dual_states)
        pools = eligible(p, days)
        res["xs_momentum"] = simulate(
            p, days, xs_targets(p, days, regime, pools, momentum_pick(p, days)), PICKS
        )
        for rule, r in res.items():
            report["rules"].setdefault(rule, {})[name] = r

    days = periods["holdout"]
    pools = eligible(p, days)
    nulls: dict[str, list[float]] = {k: [] for k in report["rules"]}
    for _ in range(NULL_DRAWS):
        for rule, (coin, state) in states.items():
            fake = spells_null(state.reindex(days).fillna(False), rng)
            nulls[rule].append(run_single(p, days, fake, coin)["final_equity"])
        fakes = {c: spells_null(s.reindex(days).fillna(False), rng) for c, s in dual_states.items()}
        nulls["btc_eth_dual"].append(run_dual(p, days, fakes)["final_equity"])
        pick = lambda i, pool: list(rng.choice(pool, size=min(PICKS, len(pool)), replace=False))  # noqa: E731
        nulls["xs_momentum"].append(
            simulate(p, days, xs_targets(p, days, regime, pools, pick), PICKS)["final_equity"]
        )

    bh = report["periods"]["holdout"]["btc_buy_hold"]
    for rule, by_period in report["rules"].items():
        hold, design = by_period["holdout"], by_period["design"]
        null = np.array(nulls[rule])
        p_value = float((null >= hold["final_equity"]).mean())
        checks = {
            "holdout_grows_after_tax": hold["final_equity"] > 1,
            "beats_random_timing": p_value < P_BAR,
            "drawdown_at_most_half_of_btc": hold["max_drawdown"] <= 0.5 * bh["max_drawdown"],
            "design_grows_after_tax": design["final_equity"] > 1,
        }
        by_period["null"] = {
            "draws": NULL_DRAWS,
            "median_final_equity": float(np.median(null)),
            "p_value": p_value,
        }
        by_period["checks"] = checks
        by_period["passed"] = all(checks.values())
    report["passed"] = [r for r, v in report["rules"].items() if v["passed"]]
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate(load(args.db))
    report["written_at"] = datetime.now().astimezone().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
