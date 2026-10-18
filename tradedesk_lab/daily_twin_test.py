"""Do the daily setups' ENTRIES pick stocks that beat the market over the next 5 sessions?

Research only (owner-approved, 2026-10-18). Live quick twins showed trend_pullback's entries
beating the average stock 57% of the time over 5 sessions (190 calls) while its normal exits
lose. This tests that on years of history before anything is trusted:

  every signal the production scan would have armed (backtest/runner.run_backtest, before
  portfolio limits), entry at the NEXT session's open, exit at the close 5 sessions later;
  net of the DELIVERY round trip (production EquityCostModel, Rs 1 lakh position); compared
  with the equal-weight average of every liquid stock bought and sold at the same moments.

Significance is per DAY (mean excess of that day's signals over the market, t-test across
days), so many correlated signals on one strong day cannot fake an edge. Pass bar, fixed in
advance: day-clustered excess > 0 with p < 0.05 / number of setups tested, mean net return
> 0, and positive in every calendar year covered.

Usage (on a copy of data/tradedesk.duckdb, so no job's lock is held):
    python -m tradedesk_lab.daily_twin_test --db <copy> --from 2023-09-01
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradedesk_lab.artifacts import OUTPUT

OUT = OUTPUT / "daily_twin_test"
HOLD = 5
POSITION_INR = 100_000.0


def _normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def run(db: Path, start: date, setups: list[str] | None) -> dict[str, Any]:
    from tradedesk.backtest import BacktestConfig, prepare_market, run_backtest
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.cli import _reference_code, _sector_config, _store
    from tradedesk.config import load_config
    from tradedesk.engine.signals import SetupKind
    from tradedesk.markets import nse_market
    from tradedesk.models import TradeType

    root = Path(".")
    settings = load_config(root)
    mkt = nse_market(settings)
    names = setups or [
        k
        for k, v in settings.setups.setups.items()
        if v.enabled and (not v.markets or "nse" in v.markets)
    ]
    cfg = BacktestConfig(
        setups=[SetupKind(n) for n in names],
        start=start,
        end=datetime.now(IST).date(),
        capital=float(settings.risk.trading_capital),
        risk=settings.risk,
        engine=settings.engine,
        setup_params={k: v.model_dump() for k, v in settings.setups.setups.items()},
        universe_rules=mkt.universe_rules,
        slippage_pct=float(mkt.costs.slippage_pct),
        costs=mkt.costs,
        qty_step=mkt.qty_step,
        min_notional=mkt.min_notional_inr,
        vix_required=mkt.vix_required,
    )
    with _store(db) as store:
        ref = _reference_code(store, root)
        cfg.vix_code = store.index_code(settings.universe.volatility_index)
        cfg.sector_of, cfg.sector_codes = _sector_config(store, root)
        universe = [c for c in store.codes(Interval.D1) if c not in (ref, cfg.vix_code)]
        print(f"preparing {len(universe)} codes from {start}", flush=True)
        md = prepare_market(store, universe, ref, cfg)
    print(f"{len(md.features)} codes, {len(md.calendar)} sessions; scanning", flush=True)
    result = run_backtest(md, cfg)
    print(f"{len(result.signals)} signals armed", flush=True)

    opens = pd.DataFrame({c: f["open"] for c, f in md.features.items()})
    closes = pd.DataFrame({c: f["close"] for c, f in md.features.items()})
    turnover = pd.DataFrame({c: f["close"] * f["volume"] for c, f in md.features.items()})
    for frame in (opens, closes, turnover):
        frame.index = pd.DatetimeIndex(frame.index).tz_convert("Asia/Kolkata").date
    dates = list(opens.index)
    pos = {d: i for i, d in enumerate(dates)}
    liquid = turnover.rolling(20).mean().shift(1) >= float(mkt.universe_rules.min_avg_turnover_inr)
    fwd = closes.shift(-HOLD) / opens - 1  # buy this session's open, sell the close HOLD later
    market = fwd.where(liquid).mean(axis=1)

    cm = mkt.costs
    rows = []
    for ts in result.signals:
        sig = ts.signal
        i = pos.get(sig.armed_on)
        if i is None or i + 1 + HOLD >= len(dates):
            continue
        day = dates[i + 1]
        ret = fwd.at[day, sig.scrip_code] if sig.scrip_code in fwd.columns else np.nan
        mkt_ret = market.get(day, np.nan)
        if not (np.isfinite(ret) and np.isfinite(mkt_ret)):
            continue
        entry = float(opens.at[day, sig.scrip_code])
        qty = max(1.0, math.floor(POSITION_INR / entry))
        rt = cm.round_trip_cost(
            trade_type=TradeType.DELIVERY, qty=qty,
            entry_price=Decimal(str(round(entry, 2))), exit_price=Decimal(str(round(entry * (1 + ret), 2))),  # noqa: E501
        )  # fmt: skip
        cost = float(rt.total) / (qty * entry)
        rows.append({"setup": sig.setup.value, "day": day, "code": sig.scrip_code,
                     "net": ret - cost, "excess": ret - mkt_ret})  # fmt: skip
    trades = pd.DataFrame(rows)
    report = {"generated_at": datetime.now().astimezone().isoformat(), "authority": "research_only",
              "hold_sessions": HOLD, "from": str(start), "results": []}  # fmt: skip
    tested = trades["setup"].nunique() if len(trades) else 1
    alpha = 0.05 / max(1, tested)
    for setup, t in trades.groupby("setup"):
        daily = t.groupby("day")["excess"].mean()
        n_days = len(daily)
        se = daily.std(ddof=1) / math.sqrt(n_days) if n_days > 1 else float("nan")
        z = float(daily.mean() / se) if se and se > 0 else 0.0
        p = _normal_sf(z)
        years = {str(y): {"calls": int(len(g)), "mean_net": float(g["net"].mean()), "beat_rate": float((g["excess"] > 0).mean())}  # noqa: E501
                 for y, g in t.groupby(pd.to_datetime(t["day"]).dt.year)}  # fmt: skip
        passed = bool(
            p < alpha and t["net"].mean() > 0 and all(v["mean_net"] > 0 for v in years.values())
        )
        report["results"].append({
            "setup": setup, "calls": int(len(t)), "days": n_days,
            "mean_net_return": float(t["net"].mean()), "win_rate": float((t["net"] > 0).mean()),
            "beat_market_rate": float((t["excess"] > 0).mean()), "mean_excess": float(t["excess"].mean()),  # noqa: E501
            "day_clustered_z": z, "p": p, "alpha": alpha, "years": years, "passed": passed,
        })  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    for name in (f"{stamp}.json", "latest.json"):
        (OUT / name).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--from", dest="start", default="2023-09-01")
    parser.add_argument("--setup", action="append")
    args = parser.parse_args(argv)
    report = run(args.db, date.fromisoformat(args.start), args.setup)
    for r in sorted(report["results"], key=lambda r: -r["mean_excess"]):
        yrs = " ".join(f"{y}:{v['mean_net']:+.2%}" for y, v in r["years"].items())
        print(
            f"{r['setup']:44s} calls={r['calls']:6d} net={r['mean_net_return']:+.2%} "
            f"beat={r['beat_market_rate']:.0%} excess={r['mean_excess']:+.2%} z={r['day_clustered_z']:+.2f} "  # noqa: E501
            f"p={r['p']:.4f} [{yrs}] {'PASS' if r['passed'] else 'fail'}"
        )


if __name__ == "__main__":
    main()
