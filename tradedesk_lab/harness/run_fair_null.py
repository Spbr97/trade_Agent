"""Compare the lab's original random-entry benchmark with the fair one for a rule candidate.

    python -m tradedesk_lab.harness.run_fair_null --market nse \
        --candidate r_ema20_bounce_strong_trend_trend_trail

Simulates the candidate exactly as the replacement search does (same frames, costs, sizing),
then reports three random baselines side by side:

- original: `run_null_baseline`, random entries inside each trade's own window, force-closed
  at its exit, fixed t2 target (biased against random for daily trades - see fair_null.py);
- fair, filter days: random entries on days the candidate's own filter holds, same exit;
  the margin over this is what the TRIGGER adds;
- fair, any day: random entries on any day, same exit; the margin over this is what the
  trigger and filter add together.

Writes data/m14_m18/fair_null/<market>/<candidate>.json. Research only.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk.backtest.null_baseline import RealisedTrade, run_null_baseline
from tradedesk.broker.indstocks.models import IST
from tradedesk.config import load_config
from tradedesk.self_review.detector_authoring import load_real_daily_frames, market_bundle
from tradedesk_lab.artifacts import OUTPUT, ROOT
from tradedesk_lab.candidates.rules import _FILTER_FNS, evaluate, rule_candidates
from tradedesk_lab.harness.fair_null import (
    DEFAULT_COHORTS,
    FairTrade,
    point_in_time_members,
    run_fair_null,
)
from tradedesk_lab.harness.run_lab_candidate import simulate_single_setup, to_harness_trades

DBS = {"nse": ROOT / "data" / "tradedesk.duckdb", "bse": ROOT / "data" / "bse.duckdb"}
OUT_DIR = OUTPUT / "fair_null"


def compare(
    market: str,
    candidate_name: str,
    *,
    db: Path | None = None,
    max_codes: int = 100,
    n_cohorts: int = DEFAULT_COHORTS,
    equity: float = 1_000_000.0,
    risk_pct: float = 0.005,
    pit_top: int | None = None,
) -> dict[str, Any]:
    candidate = next((c for c in rule_candidates() if c.name == candidate_name), None)
    if candidate is None:
        raise SystemExit(f"unknown rule candidate {candidate_name!r}")
    settings = load_config(ROOT)
    mkt = market_bundle(market, settings)
    frames, symbols = load_real_daily_frames(
        db or DBS[market], max_codes=max_codes,
        exclude=frozenset(mkt.universe_rules.exclude_codes),
    )  # fmt: skip
    trades = simulate_single_setup(
        candidate, frames, symbols, costs_model=mkt.costs, equity=equity, risk_pct=risk_pct,
        qty_step=mkt.qty_step,
    )  # fmt: skip
    if not trades:
        raise SystemExit("the candidate made no trades on this history")

    harness_trades, bars_by_code = to_harness_trades(trades, frames)
    original = run_null_baseline(
        [
            RealisedTrade(
                t.scrip_code,
                t.entry_bar,
                t.window_start,
                t.window_end,
                t.entry,
                t.stop,
                t.target,
                t.gross_r,
            )  # fmt: skip
            for t in harness_trades
        ],
        bars_by_code,
        n_cohorts=1000,
    )

    positions = {code: {d: i for i, d in enumerate(f.index.date)} for code, f in frames.items()}
    pit = point_in_time_members(frames, pit_top) if pit_top else None
    fair_trades = []
    for tr in trades:
        bar = positions.get(tr.scrip_code, {}).get(tr.entry_date)
        risk = tr.position.initial_risk
        if bar is None or risk <= 0:
            continue
        if pit is not None and not pit[tr.scrip_code][bar]:
            continue  # judge only trades taken while the stock was in the day's universe
        fair_trades.append(
            FairTrade(
                tr.scrip_code,
                bar,
                tr.position.signal,
                gross_r=tr.r_multiple + tr.costs / risk,
                net_r=tr.r_multiple,
            )  # fmt: skip
        )
    filter_days = {
        code: evaluate(_FILTER_FNS[candidate.filter], f).fillna(False).to_numpy(bool)
        for code, f in frames.items()
    }
    any_day = None
    if pit is not None:
        filter_days = {code: m & pit[code] for code, m in filter_days.items()}
        any_day = pit
    fair_filter = run_fair_null(fair_trades, frames, eligible=filter_days, n_cohorts=n_cohorts,
                                cost_model=mkt.costs)  # fmt: skip
    fair_any = run_fair_null(
        fair_trades, frames, eligible=any_day, n_cohorts=n_cohorts, cost_model=mkt.costs
    )

    report = {
        "generated_at": datetime.now(IST).isoformat(),
        "market": market,
        "candidate": candidate_name,
        "n_trades": len(trades),
        "n_trades_judged": len(fair_trades),
        "point_in_time_top": pit_top,
        "max_codes": max_codes,
        "original": {
            "setup_mean_gross_r": original.setup_mean_gross_r,
            "null_mean_gross_r": original.null_mean_gross_r,
            "margin_gross_r": original.setup_mean_gross_r - original.null_mean_gross_r,
            "p_value": original.p_value,
        },
        "fair_filter_days": fair_filter.to_dict(),
        "fair_any_day": fair_any.to_dict(),
    }
    suffix = f"_pit{pit_top}" if pit_top else ""
    out = OUT_DIR / market / f"{candidate_name}{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=float))
    report["path"] = str(out)
    return report


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--market", choices=sorted(DBS), required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--max-codes", type=int, default=100)
    ap.add_argument("--cohorts", type=int, default=DEFAULT_COHORTS)
    ap.add_argument("--pit-top", type=int, help="judge only point-in-time top-N liquid stocks")
    a = ap.parse_args(argv)
    r = compare(
        a.market, a.candidate, db=a.db, max_codes=a.max_codes, n_cohorts=a.cohorts,
        pit_top=a.pit_top,
    )  # fmt: skip
    o, ff, fa = r["original"], r["fair_filter_days"], r["fair_any_day"]
    print(f"{r['candidate']} on {r['market']}: {r['n_trades']} trades,"
          f" {r['n_trades_judged']} judged")  # fmt: skip
    print(f"  setup gross {o['setup_mean_gross_r']:+.3f}R (all trades),"
          f" {ff['setup_mean_gross_r']:+.3f}R (judged)")  # fmt: skip
    print(f"  original null  {o['null_mean_gross_r']:+.3f}R  margin {o['margin_gross_r']:+.3f}R"
          f"  p={o['p_value']:.4f}")  # fmt: skip
    for label, x in (("fair filter-day", ff), ("fair any-day", fa)):
        print(f"  {label:15s} {x['null_mean_gross_r']:+.3f}R  margin {x['margin_gross_r']:+.3f}R"
              f"  p={x['p_value']:.5f}  (pool median {x['median_pool_size']:.0f})")  # fmt: skip
    print(f"  report: {r['path']}")


if __name__ == "__main__":
    main()
