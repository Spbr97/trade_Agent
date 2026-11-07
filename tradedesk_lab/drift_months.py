"""Does drift's total profit beat its total loss over short periods, and would pausing help?

Owner's bar (2026-11-07): losing calls are fine, but total profit must beat total loss. An average
over ten years can hide bad stretches, so this cuts drift's history into months, quarters and years.
It scores the book Rs 1 lakh could hold: ten Rs 10,000 slots, the live entry and exit, 0.564% round
trip, skipping marked calls, calls with every slot full and a stock already held. Same-day calls
have no natural order, so the book is re-run with them shuffled and the range is reported.

COMMITTED BEFORE THE RUN (2026-11-07), not changed afterwards:

PERIOD BAR. Profit beats loss in at least 60% of months with a trade, at least 70% of quarters and
every full year. This describes drift's history and promises nothing about a future period.

PAUSE RULE, one version, no tuning. Before a new call, add up every unmarked call that exited in
the last 60 sessions. If that sum is below zero, do not take the call. Trades already held stay.
The sum uses all unmarked calls, not only the book's own, or a paused book could never restart.
It is adopted only if ALL hold:
  1. the worst fall from a peak shrinks by at least 25%;
  2. total rupees fall by no more than 10%;
  3. quarters where loss beat profit fall in both the first and second half of the history.

A failed pause rule is reported as failed and no other window is tried. Nothing here changes a live
rule or issues a call. History holds only stocks listed today, which flatters it.

RESULT (2026-11-07, 1,603 calls, 633 in the book, entries 2016-2026): the book made Rs 1,01,318 on
Rs 1 lakh without compounding (Rs 68,873 to Rs 1,09,417 by same-day order), total profit Rs 2,83,582
against total loss Rs 1,82,264, 61% win. PERIOD BAR FAILS: months 68% and quarters 70% clear it, but
only 8 of 10 full years do (2016 -Rs 860, 2022 -Rs 4,416). 2025 made +Rs 958 and 2026 so far -Rs 18,
so the last two years are about flat. Worst month -Rs 7,601, worst trade -Rs 6,062, worst fall from a
peak -Rs 15,842, longest wait for a new peak 398 sessions. The Nifty's yearly gains on Rs 1 lakh sum
to about Rs 1,22,300 for 2017-2026, before its costs and dividends, and it beat the book in 6 of those
10 years; the book's idle cash earns nothing here.
PAUSE RULE NOT ADOPTED: the worst fall shrinks 37% to -Rs 9,981 and losing quarters fall from 6 and 6
to 4 and 3, but rupees fall 17% to Rs 84,063, past the 10% limit. No other window is tried.

    python -m tradedesk_lab.drift_months
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "drift_months"
AMOUNT = 10_000.0
CAPITAL = 100_000.0
SLOTS = 10
PAUSE_SESSIONS = 60
DOWN_DAY = -0.01
SHUFFLES = 200
SEED = 20261107
MONTH_BAR = 0.60
QUARTER_BAR = 0.70
MIN_DRAWDOWN_CUT = 0.25
MAX_RUPEE_CUT = 0.10


def exit_of(
    opens: np.ndarray, highs: np.ndarray, closes: np.ndarray, j: int, first: int, last: int
) -> tuple[int, float] | None:
    """The live exit, as drift_timing.trade, also returning the session it happens on."""

    entry = opens[first, j]
    if not (math.isfinite(entry) and entry > 0) or last >= len(closes):
        return None
    level = entry * (1 + dt.TARGET)
    at, price = last, closes[last, j]
    for k in range(first, last + 1):
        if math.isfinite(highs[k, j]) and highs[k, j] >= level:
            gap = opens[k, j]
            at, price = k, max(level, gap) if math.isfinite(gap) else level
            break
    if not math.isfinite(price):
        return None
    return at, float(price / entry - 1 - dt.RT)


def build_calls(p: dict[str, Any], highs: pd.DataFrame) -> pd.DataFrame:
    """Every historical call with its entry and exit sessions, and whether it is marked skip."""

    c = p["close"]
    opens, hi, closes = p["open"].to_numpy(), highs.to_numpy(), c.to_numpy()
    nifty = c[eh.NIFTY]
    above = (nifty > nifty.rolling(200).mean()).to_numpy()
    turn = (c * p["volume"]).rolling(20).mean().shift(1)
    liquid = (turn >= eh.MIN_TURNOVER) & (c.shift(1) >= eh.MIN_PRICE)
    market = c.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0).to_numpy()
    col = {code: j for j, code in enumerate(c.columns)}
    rows = []
    ev = edv.events(p)
    for code, i1 in zip(ev["code"], ev["i1"], strict=True):
        i1 = int(i1)
        got = exit_of(opens, hi, closes, col[code], i1 + 1, i1 + edv.HOLD)
        if got is None:
            continue
        rows.append({
            "code": code, "entry_i": i1 + 1, "exit_i": got[0], "net": got[1],
            "marked": bool(market[i1] <= DOWN_DAY and not above[i1]),
        })  # fmt: skip
    frame = pd.DataFrame(rows).sort_values("entry_i", kind="stable").reset_index(drop=True)
    days = pd.to_datetime(pd.Series(list(c.index)))
    frame["exit_day"] = days.iloc[frame["exit_i"].to_numpy()].to_numpy()
    return frame


def paused(calls: pd.DataFrame, sessions: int, window: int = PAUSE_SESSIONS) -> np.ndarray:
    """True where unmarked calls that exited in the last `window` sessions sum below zero."""

    exits = np.zeros(sessions + 1)
    mine = calls[~calls["marked"]]
    np.add.at(exits, mine["exit_i"].to_numpy(), mine["net"].to_numpy())
    total = np.concatenate([[0.0], np.cumsum(exits)])
    out = []
    for e in calls["entry_i"].to_numpy():
        start = max(0, int(e) - window)
        out.append(total[int(e)] - total[start] < -1e-12)  # exits strictly before the entry session
    return np.array(out, dtype=bool)


def book(calls: pd.DataFrame, slots: int = SLOTS) -> pd.DataFrame:
    """Calls in order, skipping marked ones, a stock already held and a full book. A slot stays
    held on its exit session, because a new entry buys at the open and the exit comes later."""

    taken: list[int] = []
    held: list[tuple[int, str]] = []
    rows = zip(calls.index, calls["code"], calls["entry_i"], calls["exit_i"], calls["marked"],
               strict=True)  # fmt: skip
    for idx, code, entry_i, exit_i, marked in rows:
        if marked:
            continue
        held = [(x, c) for x, c in held if x >= entry_i]
        if len(held) >= slots or any(c == code for _, c in held):
            continue
        held.append((int(exit_i), code))
        taken.append(idx)
    return calls.loc[taken]


def periods(trades: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Rupee profit and loss per calendar period of the exit, at Rs 10,000 a trade."""

    inr = trades["net"].to_numpy() * AMOUNT
    when = pd.to_datetime(trades["exit_day"]).dt.to_period(freq).to_numpy()
    frame = pd.DataFrame({"period": when, "profit": np.where(inr > 0, inr, 0.0),
                          "loss": np.where(inr < 0, inr, 0.0), "trades": 1})  # fmt: skip
    out = frame.groupby("period").sum().sort_index()
    out["net"] = out["profit"] + out["loss"]
    return out


def beats(table: pd.DataFrame) -> float | None:
    return float((table["net"] > 0).mean()) if len(table) else None


def drawdown(trades: pd.DataFrame) -> dict[str, float]:
    """Worst fall from a peak of realised rupees, and the longest wait for a new peak."""

    if trades.empty:
        return {"worst_inr": 0.0, "longest_sessions": 0.0}
    by_exit = trades.assign(inr=trades["net"] * AMOUNT).groupby("exit_i")["inr"].sum().sort_index()
    total = peak = worst = 0.0
    peak_at = int(by_exit.index[0])
    longest = 0
    for at, inr in by_exit.items():
        total += float(inr)
        if total >= peak:
            peak, peak_at = total, int(at)
        else:
            worst = min(worst, total - peak)
            longest = max(longest, int(at) - peak_at)
    return {"worst_inr": worst, "longest_sessions": float(longest)}


def period_bar(trades: pd.DataFrame, final_year: int) -> dict[str, Any]:
    months, quarters, years = (periods(trades, f) for f in ("M", "Q", "Y"))
    full = years[[p.year < final_year for p in years.index]]
    month_share, quarter_share = beats(months), beats(quarters)
    fails = []
    if month_share is None or month_share < MONTH_BAR:
        fails.append("months")
    if quarter_share is None or quarter_share < QUARTER_BAR:
        fails.append("quarters")
    if len(full) == 0 or (full["net"] <= 0).any():
        fails.append("full years")
    return {
        "months": len(months), "months_profit_beat_loss": month_share,
        "quarters": len(quarters), "quarters_profit_beat_loss": quarter_share,
        "full_years": len(full), "full_years_profit_beat_loss": beats(full),
        "worst_month_inr": float(months["net"].min()) if len(months) else None,
        "worst_quarter_inr": float(quarters["net"].min()) if len(quarters) else None,
        "worst_year_inr": float(full["net"].min()) if len(full) else None,
        "by_year": {str(k): round(float(v)) for k, v in years["net"].items()},
        "passed": not fails, "fails": fails,
    }  # fmt: skip


def losing_quarters(trades: pd.DataFrame, split: pd.Period) -> tuple[int, int]:
    table = periods(trades, "Q")
    lost = table[table["net"] <= 0]
    early = sum(1 for p in lost.index if p < split)
    return early, len(lost) - early


def judge_pause(base: pd.DataFrame, held_back: pd.DataFrame, split: pd.Period) -> dict[str, Any]:
    """The three committed tests, comparing the book without and with the pause."""

    before, after = drawdown(base), drawdown(held_back)
    rupees = float(base["net"].sum() * AMOUNT)
    paused_rupees = float(held_back["net"].sum() * AMOUNT)
    q_before, q_after = losing_quarters(base, split), losing_quarters(held_back, split)
    fails = []
    if before["worst_inr"] == 0 or after["worst_inr"] > 0:
        fails.append("no fall to compare")
    elif -after["worst_inr"] > -before["worst_inr"] * (1 - MIN_DRAWDOWN_CUT):
        fails.append("the worst fall did not shrink by 25%")
    if rupees <= 0 or paused_rupees < rupees * (1 - MAX_RUPEE_CUT):
        fails.append("rupees fell by more than 10%")
    if not (q_after[0] < q_before[0] and q_after[1] < q_before[1]):
        fails.append("losing quarters did not fall in both halves")
    return {
        "trades": [len(base), len(held_back)], "rupees": [rupees, paused_rupees],
        "worst_fall": [before["worst_inr"], after["worst_inr"]],
        "longest_wait_sessions": [before["longest_sessions"], after["longest_sessions"]],
        "losing_quarters_first_half": [q_before[0], q_after[0]],
        "losing_quarters_second_half": [q_before[1], q_after[1]],
        "adopted": not fails, "fails": fails,
    }  # fmt: skip


def shuffled_range(calls: pd.DataFrame, shuffles: int = SHUFFLES) -> dict[str, float]:
    """The book's total rupees when same-session calls arrive in a different order."""

    rng = np.random.default_rng(SEED)
    totals = []
    for _ in range(shuffles):
        order = calls.assign(_r=rng.random(len(calls))).sort_values(["entry_i", "_r"])
        totals.append(float(book(order.drop(columns="_r"))["net"].sum() * AMOUNT))
    return {"low": min(totals), "median": float(np.median(totals)), "high": max(totals)}


def yearly_nifty(nifty: pd.Series) -> dict[str, int]:
    """What Rs 1 lakh of the Nifty gained in each calendar year, before any costs."""

    closes = nifty.dropna()
    last = closes.groupby(pd.to_datetime(pd.Series(closes.index)).dt.year.to_numpy()).last()
    return {str(y): round(float(v) * CAPITAL) for y, v in last.pct_change().dropna().items()}


def run() -> dict[str, Any]:
    p = eh.load_panels(dt.DB)
    highs = mr.load(dt.DB)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    calls = build_calls(p, highs)
    sessions = len(p["close"])
    days = pd.to_datetime(pd.Series(list(p["close"].index)))
    final_year = int(days.iloc[-1].year)
    split = pd.Period(days.iloc[sessions // 2], "Q")
    base = book(calls)
    held_back = book(calls[~paused(calls, sessions)])
    nets = base["net"].to_numpy() * AMOUNT
    report = {
        "generated_at": datetime.now(IST).isoformat(),
        "calls": len(calls),
        "marked_skip": int(calls["marked"].sum()),
        "book_trades": len(base),
        "book_rupees": float(nets.sum()),
        "book_total_profit": float(nets[nets > 0].sum()),
        "book_total_loss": float(nets[nets < 0].sum()),
        "book_win_rate": float((nets > 0).mean()),
        "worst_trade_inr": float(nets.min()),
        "book_if_same_day_order_differs": shuffled_range(calls),
        "drawdown": drawdown(base),
        "period_bar": period_bar(base, final_year),
        "pause": judge_pause(base, held_back, split),
        "nifty_on_a_lakh_by_year": yearly_nifty(p["close"][eh.NIFTY]),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
