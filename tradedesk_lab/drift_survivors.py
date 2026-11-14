"""Drift without survivorship: does the edge hold once delisted and renamed stocks are put back?

WHY (2026-11-14). Every drift figure (1,605 calls 2016-2026, +1.19% a call after charges, +0.83%
over the market; drift_core's PASS) comes from INDstocks history, which holds only the stocks
listed today. A company that jumped on results and later collapsed, was suspended, merged away or
fell into the trade-to-trade series is missing, and so are its calls; the equal-weight market the
calls are measured against is made of survivors too. The owner's Quant Desk plan (2026-11-14) asked
for survivorship-aware data; this is the test of drift on it. Research only: no order, no live rule
changes unless the consequences below say so.

DATA. NSE's daily bhavcopy (tradedesk_lab/bhavcopy.py), every mainboard equity row (EQ, BE, BZ) of
every session, delisted stocks included, renamed to the latest symbol by NSE's symbolchange list
(date-aware), split- and bonus-adjusted from the corporate_actions table plus the deep-ratio guard.
Three panels, each run through drift_months.build_calls unchanged (same reaction, volume, liquidity
and price filters, +10% or 20th-close exit, 0.564% round trip, skip mark):
  A  survivors   INDstocks only, exactly as every earlier study (reproduces its calls);
  B  + renames   A, with each results event renamed to the symbol its company trades under today,
                 so a company that changed its symbol keeps its earlier events;
  C  everything  B, plus a column for every bhavcopy stock that today's INDstocks list leaves out;
                 they also enter the equal-weight market. A stock that stops trading during a call
                 is sold at its last traded close (a merger's shares carry roughly that value; a
                 suspension that never reopened is a known overstatement and is counted).
Per call: net = the live exit's return after the round trip; excess = net minus the equal-weight
liquid market from the reaction close to the exit close (the panel's own market); t of the mean
excess over entry weeks (calls averaged within a week first).

DATA GATE (before any verdict). For every stock in both sources, bhavcopy's daily close-to-close
return must agree with INDstocks' within 0.5 percentage points on at least 98% of shared stock-days.
If not, the run reports the disagreement and gives no verdict.

BAR (written 2026-11-14, before any bhavcopy figure was computed). Panel C passes only if all of:
  1. mean net per call > 0 after the 0.564% round trip;
  2. mean excess over the market > 0 with t >= 2.0 over entry weeks;
  3. mean excess > 0 in both halves (split at the middle call by entry session);
  4. the ten-slot Rs 1 lakh book (drift_months.book: marked calls, a held stock and a full book
     skipped) makes total profit above total loss.
CONSEQUENCES fixed now. If C's mean net per call differs from A's by 0.2 percentage points or more
either way, earnings_drift.HISTORY is restated from panel C. If C fails, the owner is told that
drift's history was a survivorship effect and real money should not follow it; the paper record
continues. Nothing is tuned after the run: no other filter, exit, gate or panel is tried.
Reported, not judged: 2020 and 2022 (the plan's stress years), calls only in C and how they did,
calls sold at a last traded price.

    python -m tradedesk_lab.drift_survivors
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab import bhavcopy as bc
from tradedesk_lab import drift_months as dm
from tradedesk_lab import drift_timing as dt
from tradedesk_lab import edge_hunt as eh
from tradedesk_lab import multiday_rotation as mr
from tradedesk_lab.artifacts import OUTPUT

OUT_DIR = OUTPUT / "drift_survivors"
PREFIX = "BHAV_"
AGREE_WITHIN = 0.005
AGREE_SHARE = 0.98
MIN_T = 2.0
RESTATE_GAP = 0.002
STRESS_YEARS = (2020, 2022)


def canonical_names(frame: pd.DataFrame, changes: Sequence[tuple[str, str, date]],
                    symbol: str = "symbol", day: str = "day") -> pd.Series:  # fmt: skip
    """bhavcopy.canonical for every row, computed once per (symbol, day) of a renamed symbol."""

    olds = {old for old, _, _ in changes}
    names = frame[symbol].copy()
    hit = frame[symbol].isin(olds)
    if hit.any():
        pairs = frame.loc[hit, [symbol, day]]
        cache: dict[tuple[str, date], str] = {}
        out = []
        for s, d in zip(pairs[symbol], pd.to_datetime(pairs[day]).dt.date, strict=True):
            key = (s, d)
            if key not in cache:
                cache[key] = bc.canonical(s, d, changes)
            out.append(cache[key])
        names.loc[hit] = out
    return names


def canonical_actions(
    actions: Mapping[str, Mapping[date, float]], changes: Sequence[tuple[str, str, date]]
) -> dict[str, dict[date, float]]:
    """Corporate actions keyed by the latest symbol of the company that had them."""

    out: dict[str, dict[date, float]] = {}
    for symbol, by_day in actions.items():
        for ex, factor in by_day.items():
            name = bc.canonical(symbol, ex, changes)
            out.setdefault(name, {})[ex] = out.get(name, {}).get(ex, 1.0) * factor
    return out


def added_panels(rows: pd.DataFrame, keep: set[str], index: pd.Index,
                 actions: Mapping[str, Mapping[date, float]]) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:  # noqa: E501  # fmt: skip
    """Adjusted open/high/close/volume panels on `index` for the canonical names in `keep`.
    Closes are carried forward over days a stock did not trade and after its last session, so
    a call open when trading stopped is sold at the last traded close; opens and highs are not,
    so no call starts or reaches its target on a day without trading."""

    sub = bc.one_row_a_day(rows[rows["name"].isin(keep)])
    parts, notes = [], {}
    for name, stock in sub.groupby("name", sort=True):
        adjusted, changed = bc.adjust(stock, actions.get(name, {}))
        if changed:
            notes[name] = changed
        parts.append(adjusted)
    frame = pd.concat(parts, ignore_index=True) if parts else sub.iloc[:0]
    frame["code"] = PREFIX + frame["name"]
    closes = frame.pivot_table(index="day", columns="code", values="close", aggfunc="last")
    panels = {  # every panel on the close panel's columns: callers index them by position
        k: frame.pivot_table(index="day", columns="code", values=k, aggfunc="last", dropna=False)
        .reindex(index=index, columns=closes.columns)
        for k in ("open", "high", "close", "volume")
    }
    traded = panels["close"].notna()
    panels["close"] = panels["close"].ffill()
    return panels, {"adjusted": notes, "traded": traded}


def agreement(rows: pd.DataFrame, closes: pd.DataFrame, symbols: Mapping[str, str]) -> dict[str, Any]:  # noqa: E501
    """How often bhavcopy's daily return matches INDstocks' for the stocks in both."""

    eq = bc.one_row_a_day(rows[rows["name"].isin(set(symbols))])
    mine = eq.pivot_table(index="day", columns="name", values="close", aggfunc="last")
    theirs = closes[[symbols[n] for n in mine.columns if symbols[n] in closes.columns]]
    theirs.columns = [n for n in mine.columns if symbols[n] in closes.columns]
    mine = mine[theirs.columns].reindex(theirs.index)
    a = mine.pct_change(fill_method=None)
    b = theirs.pct_change(fill_method=None)
    both = a.notna() & b.notna()
    diff = (a - b).abs()[both]
    shared = int(both.to_numpy().sum())
    close_enough = int((diff <= AGREE_WITHIN).to_numpy().sum())
    return {"stocks": int(theirs.shape[1]), "stock_days": shared,
            "share_within": close_enough / shared if shared else 0.0}  # fmt: skip


def market_index(p: Mapping[str, Any]) -> np.ndarray:
    c = p["close"]
    turn = (c * p["volume"]).rolling(20).mean().shift(1)
    liquid = (turn >= eh.MIN_TURNOVER) & (c.shift(1) >= eh.MIN_PRICE)
    market = c.pct_change(fill_method=None).where(liquid).mean(axis=1).fillna(0.0)
    return (1 + market).cumprod().to_numpy()


def with_excess(calls: pd.DataFrame, index: np.ndarray) -> pd.DataFrame:
    """Each call's excess over the market from its reaction close to its exit close."""

    out = calls.copy()
    start = out["entry_i"].to_numpy() - 1
    out["excess"] = out["net"].to_numpy() - (index[out["exit_i"].to_numpy()] / index[start] - 1)
    return out


def t_by_week(values: pd.Series, days: pd.Series) -> float | None:
    weekly = values.groupby(pd.to_datetime(days).dt.to_period("W")).mean()
    if len(weekly) < 2:
        return None
    se = float(weekly.std(ddof=1)) / math.sqrt(len(weekly))
    return float(weekly.mean()) / se if se > 0 else None


def summary(calls: pd.DataFrame, sessions: pd.DatetimeIndex) -> dict[str, Any]:
    if calls.empty:
        return {"calls": 0}
    days = pd.Series(sessions[calls["entry_i"].to_numpy()], index=calls.index)
    order = calls.assign(_day=days).sort_values("entry_i", kind="stable")
    half = len(order) // 2
    first, second = order.iloc[:half], order.iloc[half:]
    years = order.groupby(order["_day"].dt.year)
    return {
        "calls": int(len(order)),
        "mean_net": float(order["net"].mean()),
        "win_rate": float((order["net"] > 0).mean()),
        "mean_excess": float(order["excess"].mean()),
        "t_excess_weeks": t_by_week(order["excess"], order["_day"]),
        "first_half_excess": float(first["excess"].mean()) if len(first) else None,
        "second_half_excess": float(second["excess"].mean()) if len(second) else None,
        "split_day": str(order["_day"].iloc[half].date()) if len(order) > 1 else None,
        "by_year": {str(y): {"calls": int(len(g)), "mean_net": round(float(g["net"].mean()), 5),
                             "mean_excess": round(float(g["excess"].mean()), 5)}
                    for y, g in years},  # fmt: skip
    }


def book_result(calls: pd.DataFrame) -> dict[str, Any]:
    book = dm.book(calls)
    inr = book["net"].to_numpy() * dm.AMOUNT
    by_year = pd.Series(inr).groupby(pd.to_datetime(book["exit_day"]).dt.year.to_numpy()).sum()
    return {
        "trades": int(len(book)), "profit_inr": round(float(inr[inr > 0].sum())),
        "loss_inr": round(float(inr[inr < 0].sum())), "net_inr": round(float(inr.sum())),
        "by_year_inr": {str(k): round(float(v)) for k, v in by_year.items()},
    }  # fmt: skip


def judge(c: Mapping[str, Any], book: Mapping[str, Any]) -> dict[str, Any]:
    t = c.get("t_excess_weeks")
    parts = {
        "net_positive": c.get("mean_net", 0) > 0,
        "excess_t": c.get("mean_excess", 0) > 0 and t is not None and t >= MIN_T,
        "both_halves": (c.get("first_half_excess") or 0) > 0 and (c.get("second_half_excess") or 0) > 0,  # noqa: E501
        "book_profit_beats_loss": book["profit_inr"] > -book["loss_inr"],
    }
    return {"parts": parts, "passed": all(parts.values())}


def run() -> dict[str, Any]:
    p = eh.load_panels(dt.DB)
    highs = mr.load(dt.DB)["high"].reindex(index=p["close"].index, columns=p["close"].columns)
    index = p["close"].index
    changes = bc.read_changes(bc.STORE / "symbolchange.csv")
    rows = bc.read_rows()
    rows["name"] = canonical_names(rows, changes)
    rows = rows[(rows["day"] >= index[0]) & (rows["day"] <= index[-1])]

    gate = agreement(rows, p["close"], p["symbols"])
    report: dict[str, Any] = {"generated_at": datetime.now(IST).isoformat(), "data_gate": gate,
                              "bhavcopy_days": int(rows["day"].nunique()),
                              "bhavcopy_stocks": int(rows["name"].nunique())}  # fmt: skip
    gate["passed"] = gate["share_within"] >= AGREE_SHARE

    events = p["events"].copy()
    events["symbol"] = canonical_names(events, changes, "symbol", "event_date")
    renamed = int((events["symbol"] != p["events"]["symbol"]).sum())

    missing = set(rows["name"].unique()) - set(p["symbols"])
    extra, info = added_panels(rows, missing, index,
                               canonical_actions(bc.actions_from_db(), changes))  # fmt: skip
    traded = info["traded"]
    base = {k: p[k].reindex(columns=p["close"].columns) for k in ("open", "volume")}
    panels: dict[str, dict[str, Any]] = {
        "A_survivors": dict(p),
        "B_renames": {**p, "events": events},
        "C_everything": {
            **p, "events": events,
            "symbols": {**{n: PREFIX + n for n in missing}, **p["symbols"]},
            "close": pd.concat([p["close"], extra["close"]], axis=1),
            **{k: pd.concat([base[k], extra[k]], axis=1) for k in ("open", "volume")},
        },
    }  # fmt: skip
    highs_c = pd.concat([highs, extra["high"]], axis=1)
    assert list(highs_c.columns) == list(panels["C_everything"]["close"].columns)
    assert list(panels["C_everything"]["open"].columns) == list(highs_c.columns)
    out: dict[str, Any] = {}
    calls_of: dict[str, pd.DataFrame] = {}
    for name, panel in panels.items():
        h = highs_c if name == "C_everything" else highs
        calls = with_excess(dm.build_calls(panel, h), market_index(panel))
        calls_of[name] = calls
        out[name] = {**summary(calls, index), "book": book_result(calls)}
    c_calls = calls_of["C_everything"]
    only_c = c_calls[c_calls["code"].str.startswith(PREFIX)]
    last_price = [
        bool(not traded.iat[x, traded.columns.get_loc(code)])
        for code, x in zip(only_c["code"], only_c["exit_i"], strict=True)
    ]
    report.update({
        "renamed_events": renamed, "added_stocks": len(missing),
        "adjusted_stocks": len(info["adjusted"]), "panels": out,
        "only_in_C": {**summary(only_c, index), "sold_at_last_traded_close": int(sum(last_price)),
                      "codes": sorted(set(only_c["code"]))[:200]},
        "stress_years": {str(y): {k: out[k]["by_year"].get(str(y)) for k in out}
                         for y in STRESS_YEARS},
    })  # fmt: skip
    if gate["passed"]:
        verdict = judge(out["C_everything"], out["C_everything"]["book"])
        gap = out["A_survivors"]["mean_net"] - out["C_everything"]["mean_net"]
        report.update(verdict=verdict, survivorship_gap_net=gap,
                      restate_history=abs(gap) >= RESTATE_GAP)
    else:
        report["verdict"] = "NO VERDICT: bhavcopy disagrees with INDstocks (data gate)"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, default=str), "utf-8")
    (OUT_DIR / "adjustments.json").write_text(json.dumps(info["adjusted"], indent=1), "utf-8")
    return report


def main() -> None:
    report = run()
    brief = {k: v for k, v in report.items() if k not in ("panels", "only_in_C")}
    brief["panels"] = {k: {x: v.get(x) for x in ("calls", "mean_net", "mean_excess",
                                                  "t_excess_weeks", "first_half_excess",
                                                  "second_half_excess", "book")}
                       for k, v in report["panels"].items()}  # fmt: skip
    brief["only_in_C"] = {k: v for k, v in report["only_in_C"].items() if k != "by_year"}
    print(json.dumps(brief, indent=1, default=str))


if __name__ == "__main__":
    main()
