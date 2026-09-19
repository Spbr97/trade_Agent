"""Phase 4's validation gauntlet, run in the template's stated order, stopping at the first
failure - entirely from primitives this project already has tested. Stage -> module:

1. Random-entry benchmark -> `tradedesk.backtest.null_baseline.run_null_baseline` (existing).
2. In-sample run -> the trades as given (existing metrics).
3. Walk-forward -> `tradedesk_lab.validation.purge`/`walk_forward` (existing, generic
   date-based splitting with an embargo) instead of new rolling-window code.
4. Parameter sensitivity -> `sensitivity.sweep_parameter` (new).
5. Monte Carlo trade-reorder -> `monte_carlo.monte_carlo_trade_reorder` (new).
6. Regime split -> `regime_split.split_trades_by_regime` (new).

Kill criteria (`spec.KillCriteria`) are checked once, at the end, against the REALISED
(non-reshuffled) trade sequence - Monte Carlo/sensitivity inform the report but do not
themselves gate pass/fail, since the template's kill-criteria table is about the strategy's
own measured numbers, not a resampled distribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.backtest.null_baseline import RealisedTrade, run_null_baseline
from tradedesk.backtest.reports import longest_losing_streak, max_drawdown
from tradedesk_lab.harness.monte_carlo import monte_carlo_trade_reorder
from tradedesk_lab.harness.sensitivity import SensitivityResult
from tradedesk_lab.harness.spec import KillCriteria, evaluate
from tradedesk_lab.harness.trade import HarnessTrade
from tradedesk_lab.validation import walk_forward as _cpcv_walk_forward


@dataclass
class GauntletReport:
    strategy_name: str
    stopped_at: str | None  # the first failing stage, or None if every ran stage passed
    stages: dict[str, Any] = field(default_factory=dict)
    kill_criteria: dict[str, Any] | None = None


def _equity_from_r(
    r_multiples: list[float], *, starting_capital: float, risk_pct: float
) -> pd.Series:
    pnl = np.asarray(r_multiples, dtype=float) * risk_pct * starting_capital
    return pd.Series(np.concatenate(([starting_capital], starting_capital + np.cumsum(pnl))))


def run_gauntlet(
    strategy_name: str,
    trades: list[HarnessTrade],
    bars_by_code: dict[str, tuple[Any, Any, Any, Any]],
    kill_criteria: KillCriteria,
    *,
    n_null_cohorts: int = 1000,
    n_monte_carlo: int = 2000,
    sensitivity: list[SensitivityResult] | None = None,
    starting_capital: float = 100_000.0,
    risk_per_trade_pct: float = 0.0025,
    regime_labels: list[str] | None = None,
    walk_forward_splits: int = 3,
    walk_forward_embargo_days: int = 1,
    seed: int = 20260101,
) -> GauntletReport:
    report = GauntletReport(strategy_name=strategy_name, stopped_at=None)
    if not trades:
        report.stopped_at = "no_trades"
        return report
    net_rs = [t.net_r for t in trades]

    # 1. Random-entry benchmark
    realised = [
        RealisedTrade(
            t.scrip_code,
            t.entry_bar,
            t.window_start,
            t.window_end,
            t.entry,
            t.stop,
            t.target,
            t.gross_r,
        )
        for t in trades
    ]
    null_result = run_null_baseline(realised, bars_by_code, n_cohorts=n_null_cohorts, seed=seed)
    report.stages["random_entry_benchmark"] = {
        "setup_mean_gross_r": null_result.setup_mean_gross_r,
        "null_mean_gross_r": null_result.null_mean_gross_r,
        "p_value": null_result.p_value,
        "passes": null_result.passes,
    }
    if not null_result.passes:
        report.stopped_at = "random_entry_benchmark"
        return report

    # 2. In-sample run
    expectancy_r = float(np.mean(net_rs))
    report.stages["in_sample"] = {"trades": len(trades), "net_expectancy_r": expectancy_r}
    if expectancy_r <= 0:
        report.stopped_at = "in_sample"
        return report

    # 3. Walk-forward (reuses tradedesk_lab.validation's purge/embargo machinery, which
    # expects tz-NAIVE dates - tz-aware Timestamps compare incorrectly against the naive
    # numpy.datetime64 `calendar[...].to_datetime64()` produces internally in `purge`).
    df = pd.DataFrame(
        {
            "armed_on": [pd.Timestamp(t.entry_at).tz_localize(None).normalize() for t in trades],
            "label_end_date": [
                pd.Timestamp(t.exit_at).tz_localize(None).normalize() for t in trades
            ],
        }
    )
    calendar = pd.DatetimeIndex(sorted(set(df["armed_on"]).union(df["label_end_date"])))
    folds = _cpcv_walk_forward(
        df, calendar, splits=walk_forward_splits, embargo=walk_forward_embargo_days
    )
    fold_results = [
        {
            "train_expectancy_r": float(np.mean([net_rs[i] for i in fold.train]))
            if len(fold.train)
            else None,
            "test_expectancy_r": float(np.mean([net_rs[i] for i in fold.test]))
            if len(fold.test)
            else None,
            "test_n": int(len(fold.test)),
        }
        for fold in folds
    ]
    report.stages["walk_forward"] = fold_results
    if not folds or not any((f["test_expectancy_r"] or 0.0) > 0 for f in fold_results):
        report.stopped_at = "walk_forward"
        return report

    # 4. Parameter sensitivity
    if sensitivity:
        report.stages["parameter_sensitivity"] = [
            {
                "parameter": s.parameter,
                "is_plateau": s.is_plateau,
                "low": s.low_expectancy_r,
                "base": s.base_expectancy_r,
                "high": s.high_expectancy_r,
            }
            for s in sensitivity
        ]
        if not all(s.is_plateau for s in sensitivity):
            report.stopped_at = "parameter_sensitivity"
            return report

    # 5. Monte Carlo trade-reorder
    mc = monte_carlo_trade_reorder(
        net_rs,
        starting_capital=starting_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        n_cohorts=n_monte_carlo,
        seed=seed,
    )
    report.stages["monte_carlo"] = {
        "max_drawdown_pct": mc.max_drawdown_pct,
        "losing_streak": mc.losing_streak,
    }

    # 6. Regime split
    if regime_labels:
        from tradedesk_lab.harness.regime_split import split_trades_by_regime

        report.stages["regime_split"] = split_trades_by_regime(regime_labels, net_rs)

    # Kill criteria, against the REALISED (chronological, non-reshuffled) sequence.
    equity = _equity_from_r(net_rs, starting_capital=starting_capital, risk_pct=risk_per_trade_pct)
    result = evaluate(
        kill_criteria,
        net_expectancy_r=expectancy_r,
        sample_size=len(trades),
        max_drawdown_pct=max_drawdown(equity),
        losing_streak=longest_losing_streak(net_rs),
    )
    report.kill_criteria = {
        "passed": result.passed,
        "failures": list(result.failures),
        "measured": result.measured,
    }
    if not result.passed:
        report.stopped_at = "kill_criteria"
    return report
