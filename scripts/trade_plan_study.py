"""Do opening-range-breakout trade plans on model-ranked movers beat random entry after costs?

Usage: python scripts/trade_plan_study.py <nse|bse> <output_dir>

Long-only intraday plans. Entry: a completed 15-minute bar closes above the first-two-bar
(09:15-09:45) high, entered at the next bar's open plus slippage, never after 13:30. Stop:
k x daily ATR below entry. Target: m x risk, else exit at the last bar. A bar touching both
stop and target counts as the stop. Costs: the real intraday cost model. Benchmark: random
entry bars on the same stock-days under the same stop/target rule. The variant is chosen on
the first 60% of days and judged on the rest; a plan that does not win there stays NO TRADE.
"""

from __future__ import annotations

import itertools
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.config import load_config  # noqa: E402
from tradedesk.leader_discovery import _connect_read_only  # noqa: E402
from tradedesk.markets.market import bse_market, nse_market  # noqa: E402
from tradedesk.models import TradeType  # noqa: E402
from tradedesk.potential_movers import (  # noqa: E402
    LIST_SIZE,
    _read_daily,
    build_panel,
    read_results_dates,
)
from tradedesk.potential_movers_model import EVENT_FEATURES, FEATURES, _fit  # noqa: E402

RISK_RUPEES = 500.0
VARIANTS = list(itertools.product((0.5, 1.0), (1.0, 2.0, None)))  # (k x ATR stop, R target)
ENTRY_AFTER_BARS = 2
LAST_ENTRY_MINUTE = 13 * 60 + 30


def simulate(
    g: pd.DataFrame,
    entry_idx: int,
    k: float,
    target_r: float | None,
    atr_abs: float,
    cm: Any,
    slip: float,
) -> float | None:
    """Net R of entering at bar `entry_idx` open; None if the plan is unusable."""

    if entry_idx >= len(g) - 1 or atr_abs <= 0:
        return None
    entry = float(g.open.iloc[entry_idx]) * (1 + slip)
    risk = k * atr_abs
    stop = entry - risk
    target = entry + target_r * risk if target_r else None
    exit_price = float(g.close.iloc[-1])
    for i in range(entry_idx, len(g)):
        low, high = float(g.low.iloc[i]), float(g.high.iloc[i])
        if low <= stop:
            exit_price = stop
            break
        if target is not None and high >= target:
            exit_price = target
            break
    exit_price *= 1 - slip
    qty = max(1.0, round(RISK_RUPEES / risk))
    cost = cm.round_trip_cost(
        trade_type=TradeType.INTRADAY,
        qty=qty,
        entry_price=Decimal(str(round(entry, 2))),
        exit_price=Decimal(str(round(exit_price, 2))),
    )
    return ((exit_price - entry) * qty - float(cost.total)) / (risk * qty)


def main() -> None:
    market, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    settings = load_config(ROOT)
    cm = (nse_market if market == "nse" else bse_market)(settings).costs
    slip = float(cm.slippage_pct)
    db = ROOT / {"nse": "data/tradedesk.duckdb", "bse": "data/bse.duckdb"}[market]
    frame, _ = _read_daily(market, None)
    panel = build_panel(frame, market, read_results_dates(market))
    con = _connect_read_only(db)
    m15 = con.execute(
        "select scrip_code, ts, open, high, low, close from candles "
        "where interval='15minute' order by scrip_code, ts"
    ).df()
    con.close()
    m15["t"] = pd.to_datetime(m15.ts, unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    m15["date"] = m15.t.dt.date
    m15["hm"] = m15.t.dt.hour * 60 + m15.t.dt.minute
    sessions = sorted(m15.date.unique())
    cutoff = sessions[0] - pd.Timedelta(days=10)
    known = panel[panel.next_ret.notna() & (panel.date < cutoff)]
    cols = FEATURES + EVENT_FEATURES
    model = _fit(known, cols)
    atr_by = {(r.date, r.code): r.atr_pct * r.close for r in panel.itertuples()}
    rng = np.random.default_rng(7)
    rows = []
    for d in sessions[1:]:
        prior = panel[panel.date < d]
        if prior.empty:
            continue
        pdte = prior.date.max()
        cand = panel[panel.date == pdte].dropna(subset=cols)
        if cand.empty:
            continue
        cand = cand.assign(p=model.predict_proba(cand[cols])[:, 1])
        cand = cand.sort_values("p", ascending=False)
        rank = {c: i for i, c in enumerate(cand.code)}
        day = m15[m15.date == d]
        for code, g in day.groupby("scrip_code"):
            if code not in rank:
                continue
            g = g.sort_values("t").reset_index(drop=True)
            if len(g) < 20 or g.hm.iloc[0] != 555:
                continue
            atr_abs = atr_by.get((pdte, code))
            if not atr_abs or atr_abs != atr_abs:
                continue
            or_high = g.high.iloc[:ENTRY_AFTER_BARS].max()
            trig = next(
                (
                    i
                    for i in range(ENTRY_AFTER_BARS, len(g) - 2)
                    if g.hm.iloc[i] < LAST_ENTRY_MINUTE and g.close.iloc[i] > or_high
                ),
                None,
            )
            rand_idx = [
                int(x)
                for x in rng.integers(ENTRY_AFTER_BARS + 1, 17, size=4)
                if x < len(g) - 1
            ]
            for vi, (k, m) in enumerate(VARIANTS):
                plan = (
                    simulate(g, trig + 1, k, m, atr_abs, cm, slip) if trig is not None else None
                )
                rnd = [simulate(g, i, k, m, atr_abs, cm, slip) for i in rand_idx]
                rnd = [x for x in rnd if x is not None]
                rows.append(
                    {
                        "date": d,
                        "code": code,
                        "rank": rank[code],
                        "v": vi,
                        "plan": plan,
                        "rnd": float(np.mean(rnd)) if rnd else np.nan,
                    }
                )
    df = pd.DataFrame(rows)
    df.to_csv(out / f"plans_{market}.csv", index=False)
    days = sorted(df.date.unique())
    split = days[int(len(days) * 0.6)]
    pools = {
        "top25": df["rank"] < LIST_SIZE,
        "top100": df["rank"] < 100,
        "all_liquid": df["rank"] >= 0,
    }
    report: dict[str, Any] = {
        "market": market,
        "sessions": len(days),
        "split_date": str(split),
        "pools": {},
    }
    alpha = 0.05 / (len(VARIANTS) * len(pools))
    for pname, mask in pools.items():
        sub = df[mask]
        entries: dict[int, dict[str, Any]] = {}
        for vi in range(len(VARIANTS)):
            for part, part_df in (
                ("choose", sub[sub.date < split]),
                ("test", sub[sub.date >= split]),
            ):
                s = part_df[(part_df.v == vi) & part_df.plan.notna()]
                entries.setdefault(vi, {})[part] = {
                    "n": len(s),
                    "net_r": float(s.plan.mean()) if len(s) else None,
                    "random_net_r": float(s.rnd.mean()) if len(s) else None,
                    "win_rate": float((s.plan > 0).mean()) if len(s) else None,
                }

        def chosen_score(v: int, entries: dict[int, dict[str, Any]] = entries) -> float:
            c = entries[v]["choose"]
            return c["net_r"] if c["n"] >= 30 else -9.0

        best = max(entries, key=chosen_score)
        t = sub[(sub.date >= split) & (sub.v == best) & sub.plan.notna()]
        p = 1.0
        if len(t) >= 30:
            diff = (t.plan - t.rnd).to_numpy()
            boot = [rng.choice(diff, len(diff)).mean() for _ in range(2000)]
            p = float(np.mean(np.array(boot) <= 0))
        report["pools"][pname] = {
            "chosen_variant": {"k_atr": VARIANTS[best][0], "target_r": VARIANTS[best][1]},
            "choose": entries[best]["choose"],
            "test": entries[best]["test"],
            "p_beats_random": p,
            "bonferroni_alpha": alpha,
        }
    (out / f"report_{market}.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
