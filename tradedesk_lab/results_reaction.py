"""Results-day reaction: buy a stock whose results are out and the market is visibly buying.

Question (owner, 2026-10-28): can the agent catch big movers like ONESOURCE (+13.8% on
2026-10-27, results filed at 10:07, almost the whole move after the open)? Predicting such
moves before the news was tested and fails (pre_results_hold.py: -0.66% an event). This tests
the next best thing: acting on the reaction once it is visible, using only what a live system
sees at each 15-minute bar close.

Rule (one signal per results event at most):
  - the stock has results on day E (results_events); watch the first session on or after E
    and the one after it (results filed after the close show up the next morning);
  - at the close of a 15-minute bar starting 09:15-14:30, the stock is up >= MOVE more than
    the equal-weight market since the previous close, AND that bar's volume is >= 3x its
    usual volume for that time of day (median of the prior 20 sessions);
  - buy at that bar's close (+0.05% slippage). Exits tested: the same day's 15:15 price
    (intraday, before broker square-off), the 5th and the 20th session close (delivery).
Costs are the real per-trade charges at Rs 10,000 (INTRADAY or DELIVERY cost model); excess
is net return minus the equal-weight market over the same holding.

No-news control: the identical rule on stocks with NO results within 5 days, on the same
days. If news-driven reactions are not better than ordinary high-volume pops, the edge (if
any) is generic momentum - which this project has already found fails (ORB + volume,
intraday_plan_test.py).

PASS BAR (written 2026-10-28, before any result was seen). A variant passes only if all hold:
  1. at least 100 trades;
  2. mean net return per trade > 0 after costs;
  3. mean excess over the market > 0 with a day-clustered t >= 3.0 (one signal day = one unit);
  4. positive mean excess in both halves of the sample (split at the median signal day);
  5. beats the same-day no-news control, paired by day, with t >= 2.0.
Variants: MOVE in {2%, 4%} x exit in {same day, 5 sessions, 20 sessions} = 6. t >= 3 is
stricter than a Bonferroni correction for six at 5%. Research only: nothing here alerts.

Caveats: the 15-minute store holds today's listings (survivorship flatters long tests a
little); results_events gives the date, not the minute, so the rule watches two sessions.

    python -m tradedesk_lab.results_reaction
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab.artifacts import OUTPUT, ROOT

M15_DB = ROOT / "data" / "m14_m18" / "intraday_history" / "nse.duckdb"
DAILY_DB = ROOT / "data" / "tradedesk.duckdb"
OUT_DIR = OUTPUT / "results_reaction"

MOVES = (0.02, 0.04)
VOLUME_X = 3.0
EXITS = {"same_day": 0, "5_sessions": 5, "20_sessions": 20}
LAST_SIGNAL_BAR = 21  # bar starting 14:30
EXIT_BAR = 23  # bar starting 15:00: its close is the 15:15 price
TYPICAL_SESSIONS = 20
SLIPPAGE = 0.0005
NOTIONAL = 10_000.0
NEWS_WINDOW_DAYS = 5
PASS = {"min_trades": 100, "min_t": 3.0, "min_control_t": 2.0}


def prepare(m15: pd.DataFrame) -> pd.DataFrame:
    """15-minute bars with the session day, bar number (0 = 09:15), previous close, usual
    volume for that bar (prior sessions only) and the equal-weight market's move since the
    previous close at that bar."""
    df = m15.rename(columns={"code": "scrip_code"}).copy()
    t = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    df["day"] = t.dt.date
    df["bar"] = ((t.dt.hour * 60 + t.dt.minute) - (9 * 60 + 15)) // 15
    df = df[(df["bar"] >= 0) & (df["bar"] <= 24)]
    df = df.sort_values(["scrip_code", "day", "bar"]).reset_index(drop=True)
    last = df.groupby(["scrip_code", "day"])["close"].last()
    prev = last.groupby(level=0).shift(1).rename("prev_close")
    df = df.join(prev, on=["scrip_code", "day"])
    df["typ_vol"] = df.groupby(["scrip_code", "bar"])["volume"].transform(
        lambda s: s.rolling(TYPICAL_SESSIONS, min_periods=10).median().shift(1)
    )
    df["r"] = (df["close"] / df["prev_close"] - 1).clip(-0.5, 0.5)
    df["mret"] = df.groupby(["day", "bar"])["r"].transform("mean")
    df["vs_mkt"] = df["r"] - df["mret"]
    df["vol_x"] = df["volume"] / df["typ_vol"]
    return df


def watch_days(sessions: Sequence[date], event_day: date) -> list[date]:
    """The first session on or after the results date, and the one after it."""
    later = [s for s in sessions if s >= event_day]
    return later[:2]


def first_triggers(rows: pd.DataFrame, move: float) -> pd.DataFrame:
    """First qualifying bar per (scrip_code, key) among `rows`, which carry a `key` column."""
    hit = rows[
        (rows["bar"] <= LAST_SIGNAL_BAR)
        & (rows["vs_mkt"] >= move)
        & (rows["vol_x"] >= VOLUME_X)
        & rows["typ_vol"].gt(0)
    ]
    return hit.sort_values(["day", "bar"]).groupby(["scrip_code", "key"], sort=False).head(1)


def news_signals(
    df: pd.DataFrame, events: Mapping[str, Sequence[date]], move: float
) -> pd.DataFrame:
    sessions = {code: sorted(g.unique()) for code, g in df.groupby("scrip_code")["day"]}
    keys = []
    for code, days in events.items():
        if code not in sessions:
            continue
        for e in days:
            for d in watch_days(sessions[code], e):
                keys.append((code, d, f"{code}:{e}"))
    if not keys:
        return df.iloc[0:0].assign(key=[])
    k = pd.DataFrame(keys, columns=["scrip_code", "day", "key"])
    rows = df.merge(k.drop_duplicates(["scrip_code", "day"]), on=["scrip_code", "day"])
    return first_triggers(rows, move)


def control_signals(
    df: pd.DataFrame, events: Mapping[str, Sequence[date]], days: set[date], move: float
) -> pd.DataFrame:
    """The same rule on the same days for stocks with no results within NEWS_WINDOW_DAYS."""
    near: set[tuple[str, date]] = set()
    for code, evs in events.items():
        for e in evs:
            for k in range(-NEWS_WINDOW_DAYS, NEWS_WINDOW_DAYS + 1):
                near.add((code, e + timedelta(days=k)))
    rows = df[df["day"].isin(days)]
    keep = [(c, d) not in near for c, d in zip(rows["scrip_code"], rows["day"], strict=True)]
    rows = rows[keep].assign(key=lambda x: x["scrip_code"] + ":" + x["day"].astype(str))
    return first_triggers(rows, move)


@dataclass(frozen=True)
class Context:
    bars: pd.DataFrame  # prepared, indexed by (scrip_code, day, bar)
    closes: pd.DataFrame  # daily close per day x code (last 15-minute close)
    market: pd.Series  # equal-weight daily return per day
    sessions: list[date]
    cost: Any  # (trade_type, entry, exit) -> cost fraction


def context(df: pd.DataFrame, cost: Any) -> Context:
    closes = df.groupby(["day", "scrip_code"])["close"].last().unstack()
    market = closes.pct_change(fill_method=None).clip(-0.5, 0.5).mean(axis=1)
    return Context(df.set_index(["scrip_code", "day", "bar"]).sort_index(), closes, market,
                   list(closes.index), cost)  # fmt: skip


def outcome(sig: Mapping[str, Any], ctx: Context, exit_name: str) -> dict[str, Any] | None:
    code, day, bar = sig["scrip_code"], sig["day"], int(sig["bar"])
    entry = float(sig["close"]) * (1 + SLIPPAGE)
    mret_sig = float(sig["mret"])
    hold = EXITS[exit_name]
    if hold == 0:
        try:
            day_bars = ctx.bars.loc[(code, day)]
        except KeyError:
            return None
        after = day_bars[(day_bars.index > bar) & (day_bars.index <= EXIT_BAR)]
        if after.empty:
            return None
        row = after.iloc[-1]
        exit_px = float(row["close"]) * (1 - SLIPPAGE)
        mkt = (1 + float(row["mret"])) / (1 + mret_sig) - 1
        trade_type = "intraday"
    else:
        i = ctx.sessions.index(day)
        if i + hold >= len(ctx.sessions):
            return None
        exit_day = ctx.sessions[i + hold]
        px = ctx.closes.at[exit_day, code] if code in ctx.closes.columns else float("nan")
        if not math.isfinite(px):
            return None
        exit_px = float(px) * (1 - SLIPPAGE)
        try:
            close_mret = float(ctx.bars.loc[(code, day)]["mret"].iloc[-1])
        except KeyError:
            return None
        mkt_today = (1 + close_mret) / (1 + mret_sig) - 1
        later = ctx.market.loc[ctx.sessions[i + 1] : exit_day].fillna(0.0)
        mkt = (1 + mkt_today) * float((1 + later).prod()) - 1
        trade_type = "delivery"
    net = exit_px / entry - 1 - ctx.cost(trade_type, entry, exit_px)
    return {"scrip_code": code, "day": day, "bar": bar, "net": net, "excess": net - mkt}


def day_t(trades: pd.DataFrame, col: str = "excess") -> tuple[float, float, int]:
    """(mean of day means, t over days, days) - one signal day is one piece of evidence."""
    by_day = trades.groupby("day")[col].mean()
    n = len(by_day)
    if n < 2:
        return (float(by_day.mean()) if n else float("nan"), float("nan"), n)
    sd = float(by_day.std(ddof=1))
    mean = float(by_day.mean())
    return mean, (mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")), n


def judge(news: pd.DataFrame, control: pd.DataFrame) -> dict[str, Any]:
    if news.empty:
        return {"trades": 0, "passed": False, "fails": ["no trades"]}
    mean_x, t_x, n_days = day_t(news)
    days = sorted(news["day"].unique())
    cut = days[len(days) // 2]
    halves = [day_t(news[news["day"] < cut])[0], day_t(news[news["day"] >= cut])[0]]
    paired = pd.concat(
        [news.groupby("day")["excess"].mean().rename("news"),
         control.groupby("day")["excess"].mean().rename("control")], axis=1
    ).dropna()  # fmt: skip
    diff = paired["news"] - paired["control"]
    t_c = (
        float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff))))
        if len(diff) > 1 and diff.std(ddof=1) > 0
        else float("nan")
    )
    out = {
        "trades": len(news), "days": n_days, "win_rate": float((news["net"] > 0).mean()),
        "mean_net": float(news["net"].mean()), "mean_excess_day": mean_x, "t_excess": t_x,
        "halves_excess": halves, "control_trades": len(control),
        "control_mean_excess": float(control["excess"].mean()) if len(control) else None,
        "vs_control_day": float(diff.mean()) if len(diff) else None, "t_vs_control": t_c,
    }  # fmt: skip
    fails = []
    if len(news) < PASS["min_trades"]:
        fails.append(f"only {len(news)} trades")
    if not out["mean_net"] > 0:
        fails.append("loses money after costs")
    if not (mean_x > 0 and t_x >= PASS["min_t"]):
        fails.append(f"excess t {t_x:.2f} < {PASS['min_t']}")
    if not all(h > 0 for h in halves):
        fails.append("not positive in both halves")
    if not (t_c >= PASS["min_control_t"]):
        fails.append(f"vs no-news control t {t_c:.2f} < {PASS['min_control_t']}")
    out["passed"] = not fails
    out["fails"] = fails
    return out


def cost_function() -> Any:
    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    model = EquityCostModel(load_config(ROOT).risk.costs)
    kinds = {"intraday": TradeType.INTRADAY, "delivery": TradeType.DELIVERY}

    def cost(kind: str, entry: float, exit_px: float) -> float:
        qty = max(1, int(NOTIONAL // entry))
        rt = model.round_trip_cost(
            trade_type=kinds[kind], qty=qty, entry_price=Decimal(str(round(entry, 2))),
            exit_price=Decimal(str(round(exit_px, 2))),
        )  # fmt: skip
        return float(rt.total) / (qty * entry)

    return cost


def load() -> tuple[pd.DataFrame, dict[str, list[date]]]:
    import duckdb

    con = duckdb.connect(str(M15_DB), read_only=True)
    try:
        m15 = con.execute("SELECT code, ts, open, high, low, close, volume FROM m15").df()
    finally:
        con.close()
    con = duckdb.connect(str(DAILY_DB), read_only=True)
    try:
        symbols = dict(con.execute(
            "SELECT trading_symbol, scrip_code FROM instruments WHERE exch='NSE' AND series='EQ'"
        ).fetchall())  # fmt: skip
        ev = con.execute("SELECT DISTINCT symbol, event_date FROM results_events").fetchall()
    finally:
        con.close()
    events: dict[str, list[date]] = {}
    for sym, d in ev:
        code = symbols.get(sym)
        if code is not None:
            events.setdefault(code, []).append(pd.Timestamp(d).date())
    return m15, events


def _outcomes(signals: pd.DataFrame, ctx: Context, exit_name: str) -> pd.DataFrame:
    rows = [o for s in signals.to_dict("records") if (o := outcome(s, ctx, exit_name))]
    return pd.DataFrame(rows, columns=["scrip_code", "day", "bar", "net", "excess"])


def main() -> None:
    m15, events = load()
    df = prepare(m15)
    first, last = df["day"].min(), df["day"].max()
    events = {c: [d for d in ds if first <= d <= last] for c, ds in events.items()}
    ctx = context(df, cost_function())
    report: dict[str, Any] = {
        "generated_at": datetime.now(IST).isoformat(), "bars_from": str(first),
        "bars_to": str(last), "stocks": int(df["scrip_code"].nunique()),
        "results_events": sum(map(len, events.values())), "pass_bar": PASS, "variants": {},
    }  # fmt: skip
    for move in MOVES:
        sigs = news_signals(df, events, move)
        ctl = control_signals(df, events, set(sigs["day"]), move)
        for exit_name in EXITS:
            news = _outcomes(sigs, ctx, exit_name)
            control = _outcomes(ctl, ctx, exit_name)
            name = f"up{int(move * 100)}pct_{exit_name}"
            result = judge(news, control) if len(control) else judge(news, news.iloc[0:0])
            if len(news):
                early = news["bar"] <= 1
                result["by_signal_bar"] = {
                    "opening_half_hour": int(early.sum()), "later": int((~early).sum()),
                    "opening_half_hour_mean_net": float(news.loc[early, "net"].mean()),
                    "later_mean_net": float(news.loc[~early, "net"].mean()),
                }  # fmt: skip
            report["variants"][name] = result
            print(name, json.dumps({k: v for k, v in result.items() if k != "by_signal_bar"},
                                   default=float))  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print("report:", OUT_DIR / "report.json")


if __name__ == "__main__":
    main()
