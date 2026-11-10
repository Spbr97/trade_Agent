"""Drift signals traded with the manual's plan: does the owner's swing plan suit the one signal
that has a record?

tradedesk_lab/nifty_swing.py found no chart rule on Nifty 50 stocks that survives the manual's
plan (docs/nifty-swing-manual.md). The results jump behind the live drift calls is different in
kind: company news, not a chart pattern, and the only rule here that passed a bar written in
advance. Owner approved this test on 2026-11-11. It needs every liquid stock, not the Nifty 50,
because the index alone has too few results jumps; that departs from the manual and was agreed.
Research only: it never alerts, sizes a real order or trades, and changes no live rule.

CALLS. Exactly drift's history (drift_months.build_calls: a liquid stock up 5%+ over the market on
2x volume after results, bought at the next open), each call once. Calls marked "skip with real
money" (reaction on a market down day with the Nifty under its 200-day average) are reported but
not judged, as the live rule tells real money to skip them. A call with a 30%+ overnight price jump
in the 30 sessions before its signal or the 20 after is dropped from both plans and counted: such
a jump is an unadjusted split or demerger.

TWO PLANS on the same calls.
  manual  nifty_swing's trade, unchanged: stop 1.5 x ATR(14) under the signal close; no trade if
          the next open is more than 1 ATR above that close or at or under the stop; Rs 500 of
          risk a trade (0.5% of Rs 1 lakh), at most Rs 33,333 a position; half sold at +2R, the
          rest at +3R, at the entry price once +2R has traded, or at the 10th session's close;
          real charges per leg. Its book: 3 positions, one per stock, the biggest two-session
          reaction first, no new entry in a week that has lost 2% of capital.
  live    drift as it runs: Rs 10,000 a call, sold at +10% or the 20th close, no stop, 0.564%
          round trip. Its book: ten slots, calls in order (drift_months.book).

PASS BAR (written 2026-11-11, before any result was seen; entries from 2022-01-01 are out of
sample; nothing is fitted, the signal is the live rule's and the plan is the manual's).
The manual plan is APPROVED FOR PAPER on drift signals only if all of:
  1. at least 100 trades, at least 40 of them out of sample;
  2. mean net R > 0 after charges both in sample and out of sample;
  3. mean net R > 0 in at least 60% of calendar years that have 10+ trades;
  4. its book's worst fall from a peak no more than 10% of capital, and no more than 15% in 95%
     of 2,000 random re-orderings of its trades.
PROVEN EDGE, in addition:
  5. mean net R above the same weeks' any-day entries with t >= 3.0, one entry week as one unit,
     positive in sample and out of sample. Any-day entries are a fixed random 6% of all unmarked
     stock-days that meet the calls' own liquidity floor, traded with the same plan.
WHICH EXIT IS BETTER, reported for the owner and decided by this rule alone: the manual plan beats
drift's own exit only if its Rs 1 lakh book made at least as many rupees as the live ten-slot book
over the same calls AND its worst fall was no larger. Otherwise the live exit stays the better way
to trade these calls, whatever bar 1-4 says. No setting is changed after the run.

Known before the run: fixed 8% and 12% stops on these calls were tested in
earnings_drift_variants.py and not adopted; an ATR stop with R targets and a 10-session limit has
not been tested.

    python -m tradedesk_lab.drift_swing
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.engine import indicators as ind
from tradedesk_lab import drift_months as dm
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab import nifty_swing as ns
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "drift_swing"
JUMP_BACK, JUMP_AHEAD = 30, 20
BASE_SHARE = 0.06
SEED = 20261111
LIVE_AMOUNT = 10_000.0


def near_jump(opens: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """True where a 30%+ overnight jump lies within 30 sessions before or 20 after the row."""

    with np.errstate(invalid="ignore", divide="ignore"):
        gap = np.abs(opens[1:] / closes[:-1] - 1) >= ns.ARTIFACT_GAP
    jump = np.vstack([np.zeros((1, opens.shape[1]), dtype=bool), gap])
    total = np.vstack([np.zeros((1, opens.shape[1])), np.cumsum(jump, axis=0)])
    rows = np.arange(opens.shape[0])
    first = np.maximum(rows - JUMP_BACK, 0)
    last = np.minimum(rows + JUMP_AHEAD, opens.shape[0] - 1)
    return (total[last + 1] - total[first]) > 0


def marked_days(close: pd.DataFrame, volume: pd.DataFrame, nifty: pd.Series) -> np.ndarray:
    """Sessions whose calls are marked skip, as drift_months.build_calls marks them."""

    above = (nifty > nifty.rolling(200).mean()).to_numpy()
    turn = (close * volume).rolling(20).mean().shift(1)
    liquid = (turn >= eh.MIN_TURNOVER) & (close.shift(1) >= eh.MIN_PRICE)
    market = close.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0).to_numpy()
    return (market <= dm.DOWN_DAY) & ~above


def any_day_cells(close: pd.DataFrame, volume: pd.DataFrame, marked: np.ndarray,
                  share: float = BASE_SHARE, seed: int = SEED) -> np.ndarray:  # fmt: skip
    """A fixed random share of the unmarked stock-days that meet the calls' liquidity floor."""

    turn = (close * volume).rolling(20).mean().shift(1)
    ok = ((turn >= edv.MIN_TURNOVER) & (close.shift(1) >= eh.MIN_PRICE)).to_numpy()
    ok &= ~marked[:, None]
    return ok & (np.random.default_rng(seed).random(ok.shape) < share)


def plan_trades(calls: pd.DataFrame, out: ns.Outcomes, column: Mapping[str, int],
                days: Sequence[date], reaction: np.ndarray) -> pd.DataFrame:  # fmt: skip
    """Each call under the manual plan; `traded` is False where the plan makes no trade."""

    rows = calls["entry_i"].to_numpy(int) - 1
    cols = np.array([column[c] for c in calls["code"]], dtype=int)
    net_r = out.net_r[rows, cols]
    value = out.qty[rows, cols] * out.entry[rows, cols]
    with np.errstate(invalid="ignore", divide="ignore"):
        share = out.net_inr[rows, cols] / value
    return pd.DataFrame({
        "signal_row": rows, "entry_row": rows + 1, "exit_row": out.exit_row[rows, cols],
        "day": [days[r + 1] for r in rows], "symbol": list(calls["code"]),
        "net_r": net_r, "net_inr": out.net_inr[rows, cols], "strength": reaction[rows, cols],
        "marked": calls["marked"].to_numpy(bool), "traded": np.isfinite(net_r),
        "manual_net": share, "live_net": calls["net"].to_numpy(float),
        "live_exit_row": calls["exit_i"].to_numpy(int),
    })  # fmt: skip


def money(results: Sequence[float]) -> dict[str, Any]:
    """The owner's figures for one book: rupees made, total profit against total loss."""

    values = np.asarray(results, dtype=float)
    return {
        "trades": len(values), "net_inr": float(values.sum()),
        "total_profit": float(values[values > 0].sum()),
        "total_loss": float(values[values < 0].sum()),
        "win_rate": float((values > 0).mean()) if len(values) else None,
        "worst_fall_inr": ns.worst_fall(values),
    }  # fmt: skip


def paired(trades: pd.DataFrame) -> dict[str, Any]:
    """Manual minus live return on the money in each call both plans traded, by entry week."""

    both = trades[trades["traded"]]
    if both.empty:
        return {"calls": 0}
    diff = (both["manual_net"] - both["live_net"]).groupby(both["day"].map(ns._week)).mean()
    n, sd = len(diff), float(diff.std(ddof=1)) if len(diff) > 1 else 0.0
    return {
        "calls": len(both), "manual_mean": float(both["manual_net"].mean()),
        "live_mean": float(both["live_net"].mean()), "difference": float(diff.mean()),
        "t": float(diff.mean()) / (sd / math.sqrt(n)) if sd > 0 else None,
    }  # fmt: skip


def which_exit(manual: Mapping[str, Any], live: Mapping[str, Any]) -> dict[str, Any]:
    """The decision rule of the module docstring."""

    more = manual["net_inr"] >= live["net_inr"]
    safer = manual["worst_fall_inr"] <= live["worst_fall_inr"]
    reasons = ([] if more else ["made fewer rupees"]) + ([] if safer else ["fell further"])
    return {"manual_beats_live": more and safer, "why_not": reasons}


def run() -> dict[str, Any]:
    p = eh.load_panels(dt.DB)
    index, every = p["close"].index, p["close"].columns
    full = {k: v.reindex(index=index, columns=every) for k, v in mr.load(dt.DB).items()}
    calls = dm.build_calls(p, full["high"])
    marked = marked_days(p["close"], p["volume"], p["close"][eh.NIFTY])
    base_cells = any_day_cells(full["close"], full["volume"], marked)
    used = sorted(set(calls["code"]) | set(every[base_cells.any(axis=0)]))
    days = [d.date() for d in index]
    frames = {k: v[used].set_axis(days) for k, v in full.items()}
    bars = ns.Bars(frames["open"], frames["high"], frames["low"], frames["close"],
                   frames["volume"], p["close"][eh.NIFTY].set_axis(days))  # fmt: skip
    column = {code: j for j, code in enumerate(used)}
    atr = pd.DataFrame(index=days, columns=used, dtype=float)
    for code in used:
        one = pd.DataFrame({k: frames[k][code] for k in ("high", "low", "close")}).dropna()
        if len(one) > 14:
            atr[code] = ind.atr(one, 14).reindex(days)
    opens, closes = bars.open.to_numpy(float), bars.close.to_numpy(float)
    bad = near_jump(opens, closes)
    signal_rows = calls["entry_i"].to_numpy(int) - 1
    signal_cols = np.array([column[c] for c in calls["code"]], dtype=int)
    clean = ~bad[signal_rows, signal_cols]
    dropped = int((~clean).sum())
    calls = calls[clean].reset_index(drop=True)
    call_cells = np.zeros(closes.shape, dtype=bool)
    call_cells[signal_rows[clean], signal_cols[clean]] = True
    base_mask = base_cells[:, [every.get_loc(c) for c in used]] & ~bad
    out = ns.outcomes(bars, atr, ns.cost_model(), call_cells | base_mask)
    with np.errstate(invalid="ignore", divide="ignore"):
        reaction = closes / np.vstack([np.full((2, closes.shape[1]), np.nan), closes[:-2]]) - 1
    trades = plan_trades(calls, out, column, days, reaction)
    base = ns.trades_of(base_mask, out, days, used, reaction)
    judged = trades[~trades["marked"] & trades["traded"]].reset_index(drop=True)
    held = ns.book(judged, days)
    held_stats = ns.book_stats(held, days)
    edge = ns.vs_any_day(judged, base)
    verdict = ns.judge(judged, held_stats, edge, [1.0, 1.0])  # no setting to vary: bar 5 unused
    live_book = dm.book(calls).sort_values("exit_i", kind="stable")
    live = money(live_book["net"].to_numpy(float) * LIVE_AMOUNT)
    manual = money(held["net_inr"].to_numpy(float))
    unmarked = trades[~trades["marked"]]
    no_trade = unmarked[~unmarked["traded"]]
    gap_rows = no_trade["signal_row"].to_numpy(int)
    gap_cols = [column[c] for c in no_trade["symbol"]]
    with np.errstate(invalid="ignore"):
        chased = opens[gap_rows + 1, gap_cols] > closes[gap_rows, gap_cols] + ns.CHASE_ATR * (
            atr.to_numpy(float)[gap_rows, gap_cols]
        )
    report = {
        "first_entry": str(min(trades["day"])), "last_entry": str(max(trades["day"])),
        "calls": len(calls), "dropped_for_price_jumps": dropped,
        "marked_skip": int(trades["marked"].sum()), "unmarked": len(unmarked),
        "manual_no_trade": {"total": len(no_trade), "opened_over_one_atr_up": int(chased.sum())},
        "manual_per_trade": {**ns.stats(judged), **verdict, "vs_any_day": edge},
        "manual_marked_calls": ns.stats(trades[trades["marked"] & trades["traded"]]),
        "any_day": ns.stats(base),
        "manual_book": {**held_stats, **manual},
        "live_book": {**live, "worst_fall": live["worst_fall_inr"] / ns.CAPITAL},
        "same_calls_return_on_money": paired(unmarked),
        "which_exit": which_exit(manual, live),
    }  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    return report


def main() -> None:
    r = run()
    per, mb, lb = r["manual_per_trade"], r["manual_book"], r["live_book"]
    edge, same = per["vs_any_day"], r["same_calls_return_on_money"]
    print(f"{r['calls']} calls {r['first_entry']} to {r['last_entry']}, {r['marked_skip']} marked "
          f"skip, {r['dropped_for_price_jumps']} dropped for price jumps; manual plan made no "
          f"trade on {r['manual_no_trade']['total']} unmarked calls "
          f"({r['manual_no_trade']['opened_over_one_atr_up']} opened over 1 ATR up)")  # fmt: skip
    print(f"manual, per trade: {per['trades']} trades, win {per['win_rate']:.0%}, mean "
          f"{per['mean_net_r']:+.3f}R (in {per['in_sample'].get('mean_net_r', float('nan')):+.3f}, "
          f"out {per['out_of_sample'].get('mean_net_r', float('nan')):+.3f}), PF "
          f"{per['profit_factor'] or 0:.2f}, positive years {per['positive_year_share']:.0%}; "
          f"any-day {r['any_day']['mean_net_r']:+.3f}R, ahead by {edge['mean'] or 0:+.3f}R "
          f"t {edge['t'] or 0:.2f}")  # fmt: skip
    print(f"approved for paper: {per['approved_for_paper']}, proven edge: {per['proven_edge']}, "
          f"fails {per['fails'] + per['edge_fails']}")  # fmt: skip
    for name, b in (("manual book", mb), ("live book", lb)):
        print(f"{name}: {b['trades']} trades, Rs {b['net_inr']:+,.0f} (profit "
              f"Rs {b['total_profit']:,.0f}, loss Rs {b['total_loss']:,.0f}), win "
              f"{b['win_rate']:.0%}, worst fall {b['worst_fall']:.1%}")  # fmt: skip
    print(f"same calls, return on the money in each: manual {same['manual_mean']:+.2%}, live "
          f"{same['live_mean']:+.2%}, difference {same['difference']:+.2%} t {same['t'] or 0:.2f}")
    print("which exit:", r["which_exit"])


if __name__ == "__main__":
    main()
