"""Earnings drift: can the live calls be made a session sooner?

Owner's question (2026-10-28): ONESOURCE jumped 13.8% on results filed at 10:07 on 27 Oct, but
the drift rule - which knows only the results DATE - measures the reaction through the next
session's close and buys on the 29th. Two refinements, tested on the live rule's own events
(liquid NSE stocks, results jump >= 5% over the equal-weight market on >= 2x volume) and its
live exit (+10% target, otherwise the close 20 sessions after the reaction), with the real
0.564% delivery round trip:

A. Date results by their FILING TIME. When results are filed during market hours (09:15-14:45)
   on day D, judge the reaction on D's close and buy D+1's open, instead of judging on D+1's
   close and buying D+2's open. Filing times exist only from 2026-07-16 (our filings archive),
   so this is a small, indicative sample: events that qualify both ways are compared paired.
B. Buy near the reaction day's CLOSE instead of the next open, for every event 2016-2026:
   entry = that close plus 0.15% (an allowance for buying at about 15:15-15:25, before the
   official close), same exit date. Paired against the live next-open entry.

PASS BARS (written 2026-10-28, before any result was seen):
  A. at least 30 paired events; the earlier call makes more per call (mean paired gain > 0)
     with t >= 2.0.
  B. mean paired gain >= +0.30% a call (twice the late-entry allowance) with t >= 2.5, one
     entry WEEK as one unit, and positive in both halves of the history.
Research only: a pass is a proposal for the owner, not a change to the live rule.

    python -m tradedesk_lab.drift_timing
"""

from __future__ import annotations

import glob
import json
import math
from datetime import datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import earnings_drift_variants as edv
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT, ROOT

DB = ROOT / "data" / "tradedesk.duckdb"
FILINGS = ROOT / "data" / "m14_m18" / "filings"
OUT_DIR = OUTPUT / "drift_timing"
RT = 0.00564
TARGET = 0.10
LATE_ENTRY = 0.0015
FILED_FROM, FILED_TO = time(9, 15), time(14, 45)
PASS = {"a_min_pairs": 30, "a_min_t": 2.0, "b_min_gain": 0.003, "b_min_t": 2.5}


def trade(opens: np.ndarray, highs: np.ndarray, closes: np.ndarray, j: int,
          entry: float, first: int, last: int) -> float | None:  # fmt: skip
    """The live exit: sell at +10% (at the open if it gaps past) from session `first` on,
    otherwise at the close of session `last`. Returns net return after the round trip."""
    if not (math.isfinite(entry) and entry > 0) or last >= len(closes):
        return None
    level = entry * (1 + TARGET)
    exit_px = closes[last, j]
    for k in range(first, last + 1):
        if math.isfinite(highs[k, j]) and highs[k, j] >= level:
            gap = opens[k, j]
            exit_px = max(level, gap) if math.isfinite(gap) else level
            break
    if not math.isfinite(exit_px):
        return None
    return float(exit_px / entry - 1 - RT)


def qualifies(p: dict[str, Any], j: int, i0: int, i1: int, turn20: pd.DataFrame,
              vol20: pd.DataFrame, mkt: pd.Series) -> bool:  # fmt: skip
    """The live rule's test on a reaction measured from close(i0) to close(i1)."""
    closes, vol = p["close"], p["volume"]
    liquid = turn20.iat[i1, j] >= edv.MIN_TURNOVER and closes.iat[i1 - 1, j] >= eh.MIN_PRICE
    if i0 < 20 or not liquid:
        return False
    c0, c1 = closes.iat[i0, j], closes.iat[i1, j]
    wv, v20 = vol.iloc[i0 + 1 : i1 + 1, j].max(), vol20.iat[i1, j]
    if not (np.isfinite(c0) and np.isfinite(c1) and c0 > 0 and np.isfinite(wv) and v20 > 0):
        return False
    m = float((1 + mkt.iloc[i0 + 1 : i1 + 1].fillna(0.0)).prod() - 1)
    return bool(c1 / c0 - 1 - m >= 0.05 and wv / v20 >= 2.0)


def week_stats(frame: pd.DataFrame, col: str) -> tuple[float, float, int]:
    weeks = frame.groupby(frame["day"].map(lambda d: d.isocalendar()[:2]))[col].mean()
    n = len(weeks)
    if n < 2 or weeks.std(ddof=1) == 0:
        return float(weeks.mean()) if n else float("nan"), float("nan"), n
    return float(weeks.mean()), float(weeks.mean() / (weeks.std(ddof=1) / math.sqrt(n))), n


def test_b(p: dict[str, Any], arrays: tuple[np.ndarray, ...]) -> dict[str, Any]:
    opens, highs, closes = arrays
    col = {c: j for j, c in enumerate(p["close"].columns)}
    rows = []
    ev = edv.events(p)
    for code, i1 in zip(ev["code"], ev["i1"], strict=True):
        j, i1 = col[code], int(i1)
        last = i1 + edv.HOLD
        base = trade(opens, highs, closes, j, opens[i1 + 1, j], i1 + 1, last)
        late = trade(opens, highs, closes, j, closes[i1, j] * (1 + LATE_ENTRY), i1 + 1, last)
        if base is None or late is None:
            continue
        gap = opens[i1 + 1, j] / closes[i1, j] - 1
        rows.append({"day": p["close"].index[i1].date(), "base": base, "late": late,
                     "gain": late - base, "gap": gap})  # fmt: skip
    df = pd.DataFrame(rows)
    mean, t, weeks = week_stats(df, "gain")
    days = sorted(df["day"])
    cut = days[len(days) // 2]
    halves = [
        week_stats(df[df["day"] < cut], "gain")[0], week_stats(df[df["day"] >= cut], "gain")[0]
    ]  # fmt: skip
    out = {
        "events": len(df), "weeks": weeks, "base_mean_net": float(df["base"].mean()),
        "close_entry_mean_net": float(df["late"].mean()), "mean_gain_week": mean, "t_gain": t,
        "halves_gain": halves, "mean_overnight_gap": float(df["gap"].mean()),
        "share_gapping_up": float((df["gap"] > 0).mean()),
    }  # fmt: skip
    out["passed"] = bool(
        mean >= PASS["b_min_gain"] and t >= PASS["b_min_t"] and all(h > 0 for h in halves)
    )
    return out


def intraday_results(symbols: dict[str, str]) -> list[tuple[str, pd.Timestamp]]:
    """(code, filing day) for results filed 09:15-14:45 on a weekday, earliest per day."""
    seen: set[tuple[str, pd.Timestamp]] = set()
    for path in sorted(glob.glob(str(FILINGS / "*.jsonl"))):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("category") != "results" or r.get("symbol") not in symbols:
                continue
            at = datetime.fromisoformat(r["filed_at"])
            if at.weekday() < 5 and FILED_FROM <= at.time() <= FILED_TO:
                seen.add((symbols[r["symbol"]], pd.Timestamp(at.date())))
    return sorted(seen)


def test_a(p: dict[str, Any], arrays: tuple[np.ndarray, ...]) -> dict[str, Any]:
    opens, highs, closes = arrays
    c = p["close"]
    col = {code: j for j, code in enumerate(c.columns)}
    dates = c.index
    turn20 = (c * p["volume"]).rolling(20).mean().shift(1)
    vol20 = p["volume"].rolling(20).mean().shift(1)
    ok = (turn20 >= eh.MIN_TURNOVER) & (c.shift(1) >= eh.MIN_PRICE)
    mkt = c.pct_change(fill_method=None).where(ok).mean(axis=1)
    rows = []
    for code, d in intraday_results(p["symbols"]):
        if code not in col or d not in dates:
            continue
        j, i_d = col[code], dates.get_loc(d)
        early_ok = qualifies(p, j, i_d - 1, i_d, turn20, vol20, mkt)
        live_ok = i_d + 1 < len(dates) and qualifies(p, j, i_d - 1, i_d + 1, turn20, vol20, mkt)
        early = trade(opens, highs, closes, j, opens[i_d + 1, j], i_d + 1, i_d + edv.HOLD) \
            if early_ok and i_d + edv.HOLD < len(dates) else None  # fmt: skip
        live = trade(opens, highs, closes, j, opens[i_d + 2, j], i_d + 2, i_d + 1 + edv.HOLD) \
            if live_ok and i_d + 1 + edv.HOLD < len(dates) else None  # fmt: skip
        rows.append({"code": code, "day": d.date(), "early_ok": early_ok, "live_ok": live_ok,
                     "early": early, "live": live})  # fmt: skip
    df = pd.DataFrame(rows)
    both = df.dropna(subset=["early", "live"])
    gain = both["early"] - both["live"]
    sd = float(gain.std(ddof=1)) if len(gain) > 1 else 0.0
    t = float(gain.mean() / (sd / math.sqrt(len(gain)))) if sd > 0 else float("nan")
    out = {
        "intraday_results_filings": len(df),
        "qualify_on_filing_day": int(df["early_ok"].sum()) if len(df) else 0,
        "qualify_live_way": int(df["live_ok"].sum()) if len(df) else 0,
        "early_mean_net": float(df["early"].dropna().mean()) if len(df) else None,
        "live_mean_net": float(df["live"].dropna().mean()) if len(df) else None,
        "matured_early": int(df["early"].notna().sum()) if len(df) else 0,
        "matured_live": int(df["live"].notna().sum()) if len(df) else 0,
        "pairs": len(both), "mean_paired_gain": float(gain.mean()) if len(gain) else None, "t": t,
    }  # fmt: skip
    out["passed"] = bool(
        len(both) >= PASS["a_min_pairs"] and gain.mean() > 0 and t >= PASS["a_min_t"]
    )
    return out


def main() -> None:
    p = eh.load_panels(DB)
    highs = mr.load(DB)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    arrays = (p["open"].to_numpy(), highs.to_numpy(), p["close"].to_numpy())
    report = {"generated_at": datetime.now(IST).isoformat(), "pass_bar": PASS,
              "a_filing_time": test_a(p, arrays), "b_close_entry": test_b(p, arrays)}  # fmt: skip
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
