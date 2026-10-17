"""Multi-day hold study: does any forward research rule make money after costs when simply
held for 1, 3, 5 or 10 sessions - and does it beat a random pick held the same way?

Why: the end goal is any stock that makes money over any hold, not intraday picks. The
research tracker (src/tradedesk/research_tracker.py) already logs every candidate rule and
a matched random control forward, one session at a time, since 2026-09-13. It grades each
call with one geometry only (3xATR stop, 2R target, 10-session timeout), so a rule that is
right on a 3-day horizon but gives it back by day 10 is invisible there. This study reads
the SAME forward calls and asks the hold-length question directly.

Rules of the read:
  * Forward-only. No new rules, no backfill: the calls were logged before their outcomes
    existed, so nothing here is re-mined. Adding horizons is four looks at the same calls,
    not new hypotheses; the report says so rather than hiding it.
  * Entry is the open of the session after the arming close (the tracker's convention).
    Exit is the close of the h-th session from entry (h=1 means same-day open to close).
    No stop, so the number is the plain hold return, before any exit cleverness.
  * Costs are the delivery round trip plus slippage on a Rs 50k position
    (mover_study.cost_fraction); crypto uses CoinDCX's real fees and TDS.
  * Statistics are day-clustered: one value per arming day (the mean of that day's picks),
    t across days. Same-day picks move together, so a per-pick t overstates the evidence.
  * Holds overlap (a 10-session hold spans the next nine arming days), so even the
    day-clustered t is optimistic for h > 1. Read it as a screen, not a verdict.
  * `vs_control` is the per-day mean of a rule minus the random control on the same days.

Books (added 2026-10-17, once the user confirmed CoinDCX futures and NSE index futures):
  * NSE/BSE `long`: buy the stock, delivery costs.
  * NSE/BSE `hedged`: buy the stock and short NIFTY 50 futures over the same window, so the
    market's move cancels. Costs are delivery plus the futures round trip. Real hedging is
    lumpy: one NIFTY lot is 65 units, about Rs 15 lakh of index, so the long basket must be
    that size for the hedge to be whole. The study ignores that; a trader cannot.
  * crypto `spot_long`: CoinDCX spot, real fees and 1% TDS.
  * crypto `fut_long` / `fut_short`: the spot price move as a stand-in for the perpetual,
    CoinDCX futures taker fees plus GST, slippage, and an ASSUMED funding charge against
    either side. CoinDCX's funding history is not in this repo, and sources disagree on
    whether TDS applies to futures; both are flagged in the report until verified.

Research only: nothing here alerts, sizes or trades. Output: data/m14_m18/multiday_hold_study.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.models import TradeType
from tradedesk_lab import mover_study as daily
from tradedesk_lab.artifacts import OUTPUT

VERSION = "multiday-hold-study-v1"
OUTPUT_ROOT = OUTPUT / "multiday_hold_study"
HOLDS = (1, 3, 5, 10)
CONTROL = "random_eligible"
MIN_DAYS_FOR_T = 5
INDEX_NAME = "NIFTY 50"
# NIFTY futures round trip as a fraction of notional: STT 0.05% on the sell leg (Budget 2026,
# from 1 Apr 2026), exchange/SEBI/stamp/GST ~0.006%, ~0.01% slippage a leg on the most liquid
# contract in India. Approximate; brokerage is negligible at one lot's notional.
INDEX_FUTURES_ROUND_TRIP = 0.0008
# CoinDCX futures: 0.075% taker a leg (regular tier, its own fee page) plus 18% GST on the
# fee, plus the crypto market's 0.05% slippage a leg.
CRYPTO_FUTURES_ROUND_TRIP = 2 * 0.00075 * 1.18 + 2 * 0.0005
# ASSUMED: 0.01% per 8 hours, the common perpetual baseline, charged against either side so
# the estimate is conservative. Replace with CoinDCX's real funding history once fetched.
CRYPTO_FUNDING_PER_DAY = 0.0003


def books_for(market: str, delivery_cost: float) -> dict[str, Any]:
    """book -> function(returns frame) -> net return per row."""

    if market == "crypto":
        def fut(sign: int) -> Any:
            return lambda r: sign * r["gross"] - CRYPTO_FUTURES_ROUND_TRIP - (
                CRYPTO_FUNDING_PER_DAY * r["hold"]
            )

        return {
            "spot_long": lambda r: r["gross"] - delivery_cost,
            "fut_long": fut(1),
            "fut_short": fut(-1),
        }
    return {
        "long": lambda r: r["gross"] - delivery_cost,
        "hedged": lambda r: r["gross"] - r["index_gross"] - delivery_cost
        - INDEX_FUTURES_ROUND_TRIP,
    }


def hold_returns(
    calls: Iterable[Mapping[str, Any]],
    bars: Mapping[str, pd.DataFrame],
    holds: Sequence[int] = HOLDS,
    index: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per (call, hold) whose exit close exists. `bars[code]` is a daily frame with
    `open`/`close` and an IST `session` date column in ascending order. With `index`, also
    the index's open-to-close return over the same entry and exit sessions (NaN if the
    index has no bar on either)."""

    index_by_day = None if index is None else index.set_index("session")

    out: list[dict[str, Any]] = []
    for call in calls:
        frame = bars.get(str(call["scrip_code"]))
        if frame is None or frame.empty:
            continue
        armed = date.fromisoformat(str(call["armed_on"]))
        after = np.flatnonzero(frame["session"].to_numpy() > armed)
        if not len(after):
            continue
        i0 = int(after[0])
        entry = float(frame["open"].iloc[i0])
        if not entry > 0:
            continue
        for hold in holds:
            last = i0 + hold - 1
            if last >= len(frame):
                continue
            index_gross = float("nan")
            if index_by_day is not None:
                start, end = frame["session"].iloc[i0], frame["session"].iloc[last]
                if start in index_by_day.index and end in index_by_day.index:
                    index_gross = float(index_by_day.at[end, "close"]) / float(
                        index_by_day.at[start, "open"]
                    ) - 1.0
            out.append(
                {
                    "rule": call["rule"],
                    "scrip_code": call["scrip_code"],
                    "armed_on": armed.isoformat(),
                    "hold": hold,
                    "gross": float(frame["close"].iloc[last]) / entry - 1.0,
                    "index_gross": index_gross,
                }
            )
    columns = ["rule", "scrip_code", "armed_on", "hold", "gross", "index_gross"]
    return pd.DataFrame(out, columns=columns)


def _day_t(values: pd.Series) -> float | None:
    if len(values) < MIN_DAYS_FOR_T:
        return None
    sd = float(values.std(ddof=1))
    return float(values.mean() / (sd / np.sqrt(len(values)))) if sd > 0 else None


def summarize(
    returns: pd.DataFrame, cost: float | Any, control: str = CONTROL
) -> list[dict[str, Any]]:
    """Per (rule, hold): picks, days, mean gross/net per pick, share of picks green after
    costs, day-clustered mean and t of net, and the same-day difference to the control.
    `cost` is a flat round-trip fraction, or a function of the returns frame giving net."""

    if returns.empty:
        return []
    net = cost(returns) if callable(cost) else returns["gross"] - cost
    frame = returns.assign(net=net).dropna(subset=["net"])
    if frame.empty:
        return []
    daily_net = frame.groupby(["rule", "hold", "armed_on"])["net"].mean()
    rows: list[dict[str, Any]] = []
    for (rule, hold), group in frame.groupby(["rule", "hold"], sort=True):
        per_day = daily_net.loc[(rule, hold)]
        row: dict[str, Any] = {
            "rule": rule,
            "hold": int(hold),
            "picks": int(len(group)),
            "days": int(len(per_day)),
            "gross_mean": float(group["gross"].mean()),
            "net_mean": float(group["net"].mean()),
            "green_share": float((group["net"] > 0).mean()),
            "day_net_mean": float(per_day.mean()),
            "day_net_t": _day_t(per_day),
            "vs_control_mean": None,
            "vs_control_t": None,
            "shared_days": 0,
        }
        if rule != control and (control, hold) in daily_net.index:
            ctrl = daily_net.loc[(control, hold)]
            diff = (per_day - ctrl).dropna()
            row["shared_days"] = int(len(diff))
            if len(diff):
                row["vs_control_mean"] = float(diff.mean())
                row["vs_control_t"] = _day_t(diff)
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------ run


def _frame(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    frame = raw[["open", "close"]].copy()
    frame["session"] = pd.DatetimeIndex(raw.index).tz_convert("Asia/Kolkata").date
    return frame.reset_index(drop=True)


def _load_bars(market: str, codes: Iterable[str]) -> dict[str, pd.DataFrame]:
    from tradedesk.analysis import db_for
    from tradedesk.data.candle_store import CandleStore

    bars: dict[str, pd.DataFrame] = {}
    with CandleStore(db_for(market)) as store:
        for code in sorted(set(codes)):
            frame = _frame(store.load(code, Interval.D1))
            if frame is not None:
                bars[code] = frame
    return bars


def _load_index() -> pd.DataFrame | None:
    """NIFTY 50 daily bars from the NSE store; the hedge for NSE and BSE baskets alike."""

    from tradedesk.analysis import db_for
    from tradedesk.data.candle_store import CandleStore

    with CandleStore(db_for("nse")) as store:
        code = store.index_code(INDEX_NAME)
        return _frame(store.load(code, Interval.D1)) if code else None


def run(market: str, *, root: Path | None = None) -> dict[str, Any]:
    from tradedesk import research_tracker

    root = root or OUTPUT_ROOT / market
    calls = [vars(call) for call in research_tracker.load_log(
        research_tracker.log_path_for(market)
    ).values()]
    bars = _load_bars(market, (str(call["scrip_code"]) for call in calls))
    index = None if market == "crypto" else _load_index()
    returns = hold_returns(calls, bars, index=index)
    cost = daily.cost_fraction(market, TradeType.DELIVERY)
    rows = [
        {"book": book, **row}
        for book, net in books_for(market, cost).items()
        for row in summarize(returns, net)
    ]
    armed = sorted({str(call["armed_on"]) for call in calls})
    report = {
        "version": VERSION,
        "market": market,
        "generated_at": datetime.now(IST).isoformat(),
        "calls": len(calls),
        "first_armed": armed[0] if armed else None,
        "last_armed": armed[-1] if armed else None,
        "holds": list(HOLDS),
        "round_trip_cost": cost,
        "index_futures_round_trip": None if market == "crypto" else INDEX_FUTURES_ROUND_TRIP,
        "crypto_futures_round_trip": CRYPTO_FUTURES_ROUND_TRIP if market == "crypto" else None,
        "crypto_funding_per_day_assumed": CRYPTO_FUNDING_PER_DAY if market == "crypto" else None,
        "control": CONTROL,
        "rows": rows,
        "caveats": [
            "Rules were chosen on 2023-2026 data; these calls are forward-only since the "
            "tracker started, so the evidence is new but the rule choice is not.",
            "Four holds are four looks at the same calls; a lone 'win' at one hold is weak.",
            "Day-clustered t still ignores overlap between holds longer than one session.",
            "hedged needs about Rs 15 lakh of stocks per NIFTY lot (65 units) to be a whole "
            "hedge; smaller baskets are under- or over-hedged.",
            "Crypto futures use the spot move as a stand-in for the perpetual, an assumed "
            "funding charge, and no TDS; verify TDS and funding on CoinDCX before relying on "
            "the futures books.",
        ],
        "research_only": True,
    }
    root.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (root / "latest.json").write_text(text, encoding="utf-8")
    (root / f"{datetime.now(IST).date().isoformat()}.json").write_text(text, encoding="utf-8")
    return report


def render(report: Mapping[str, Any]) -> str:
    def pct(value: Any) -> str:
        return "-" if value is None else f"{100 * value:+.2f}%"

    def tval(value: Any) -> str:
        return "-" if value is None else f"{value:+.2f}"

    lines = [
        f"# Multi-day hold study ({report['market']}) - calls armed "
        f"{report['first_armed']} to {report['last_armed']}",
        f"round-trip cost {100 * report['round_trip_cost']:.2f}%; day-clustered t; "
        "research only",
        "",
        f"{'book':9} {'rule':28} {'hold':>4} {'picks':>5} {'days':>4} {'net/pick':>9} "
        f"{'green':>6} {'day t':>6} {'vs ctrl':>8} {'t':>6}",
    ]
    for row in report["rows"]:
        lines.append(
            f"{row['book']:9} {row['rule']:28} {row['hold']:>4} {row['picks']:>5} {row['days']:>4} "
            f"{pct(row['net_mean']):>9} {100 * row['green_share']:>5.0f}% "
            f"{tval(row['day_net_t']):>6} {pct(row['vs_control_mean']):>8} "
            f"{tval(row['vs_control_t']):>6}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--market", choices=("nse", "bse", "crypto"), default="nse")
    args = parser.parse_args(argv)
    print(render(run(args.market)))


if __name__ == "__main__":
    main()
