"""Config/geometry tuning proposals (Phase 1) for a setup the rolling-failure monitor has
flagged.

Earlier drafts of this module tried to reuse `scripts/entry_search.py`'s simplified
(stop_atr, target_r, max_hold) grid - but that model doesn't fit how the three live setups
actually exit: each uses a partial-target-plus-ATR-trailing-stop (`ExitPlan`), not a fixed
target/deadline pair. Re-deriving a parallel, mismatched exit model would silently misreport
what a parameter change actually does. Instead, this reuses the REAL event-driven backtester
(`backtest/runner.py::run_backtest` + `backtest/reports.py::walk_forward`) that already
simulates each setup's own exit logic exactly - the same primitive `tradedesk backtest
--split ...` already exercises - varying one of that setup's own named `config/setups.yaml`
parameters at a time.

One parameter at a time, not a combined sweep: each parameter gets its own independent
proposal, so a human approves/rejects one change at a time (matching the project's "one
decision packet per proposed change" requirement) rather than a bundle where it's unclear
which part of a multi-parameter change actually helped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from tradedesk_lab.harness.gauntlet import GauntletReport
from tradedesk_lab.harness.run_self_review_candidate import candidate_passed, run_candidate_gauntlet
from tradedesk_lab.harness.spec import KillCriteria

from tradedesk.backtest.reports import Metrics, walk_forward
from tradedesk.backtest.runner import BacktestConfig, MarketData, run_backtest
from tradedesk.engine.signals import SetupKind
from tradedesk.proposals import ConfigPatch

# At least as strict as a human-authored NSE/BSE setup must clear
# (config/setups.yaml's own eligibility.min_trades) - a self-proposed fix is never held to a
# lower sample-size bar than the existing setups it's trying to replace.
DEFAULT_KILL_CRITERIA = KillCriteria(
    min_net_expectancy_r=0.0, min_sample_size=500, max_drawdown_pct=0.30, max_losing_streak=10
)


@dataclass(frozen=True)
class CandidatePatch:
    patch: ConfigPatch
    gauntlet_report: GauntletReport

# Verified against each setup's own `params.get(...)` calls this session (nr7_breakout.py,
# base_breakout.py, trend_pullback.py) - not guessed. Extend this registry, not the search
# logic below, to cover a new tunable parameter or a new setup.
TUNABLE_PARAMS: dict[SetupKind, dict[str, list[Any]]] = {
    SetupKind.NR7_BREAKOUT: {
        "partial_at_r": [1.5, 2.0, 2.5, 3.0],
        "trail_atr_multiple": [1.5, 2.0, 2.5, 3.0],
        "min_range_atr": [0.10, 0.15, 0.20],
    },
    SetupKind.BASE_BREAKOUT: {
        "partial_at_r": [1.5, 2.0, 2.5, 3.0],
        "max_stop_distance_atr": [1.5, 2.0, 2.5, 3.0],
    },
    SetupKind.TREND_PULLBACK: {
        "partial_at_r": [1.5, 2.0, 2.5, 3.0],
    },
}

MIN_TRAIN_TRADES = 20


def _setup_metrics(
    report_overall: Metrics, by_setup: dict[str, Metrics], setup_kind: SetupKind
) -> Metrics:
    return by_setup.get(setup_kind.value, report_overall)


def _with_param(
    base_cfg: BacktestConfig, setup_kind: SetupKind, param_name: str, value: Any
) -> BacktestConfig:
    base_params = dict(base_cfg.setup_params.get(setup_kind.value, {}))
    trial_params = {**base_params, param_name: value}
    return replace(
        base_cfg, setup_params={**base_cfg.setup_params, setup_kind.value: trial_params}
    )


def propose_config_patches(
    setup_kind: SetupKind,
    market: str,
    md: MarketData,
    base_cfg: BacktestConfig,
    *,
    split: date,
    min_train_trades: int = MIN_TRAIN_TRADES,
    kill_criteria: KillCriteria = DEFAULT_KILL_CRITERIA,
) -> list[CandidatePatch]:
    """For each of this setup's registered tunable parameters, searches its small grid
    (holding every other parameter at the current config value), picks the TRAIN-half best
    by expectancy_r, and only KEEPS it as a local candidate if that exact same backtest run's
    TEST half (from `split` onward, never re-derived or re-picked) is also
    expectancy-positive. Every surviving candidate is then run through the full harness
    gauntlet (`run_candidate_gauntlet` - random-entry benchmark, walk-forward, kill criteria,
    plus this feature's own required random-timing margin) and only returned if it passes
    that too. A parameter with no value beating the current one, whose best TRAIN cell
    doesn't clear `min_train_trades`, whose TEST half isn't positive, or whose gauntlet run
    fails, produces no proposal at all - silence is a valid, honest outcome here, not a
    failure to report.
    """

    base_params = dict(base_cfg.setup_params.get(setup_kind.value, {}))
    grid = TUNABLE_PARAMS.get(setup_kind, {})
    results: list[CandidatePatch] = []

    for param_name, values in grid.items():
        current = base_params.get(param_name)
        best: tuple[Any, float] | None = None
        for value in values:
            if value == current:
                continue
            trial_cfg = _with_param(base_cfg, setup_kind, param_name, value)
            result = run_backtest(md, trial_cfg)
            train, _test = walk_forward(result, split)
            train_metrics = _setup_metrics(train.overall, train.by_setup, setup_kind)
            if train_metrics.trades < min_train_trades:
                continue
            if best is None or train_metrics.expectancy_r > best[1]:
                best = (value, train_metrics.expectancy_r)
        if best is None:
            continue

        value, _train_expectancy = best
        trial_cfg = _with_param(base_cfg, setup_kind, param_name, value)
        result = run_backtest(md, trial_cfg)
        _train, test = walk_forward(result, split)
        test_metrics = _setup_metrics(test.overall, test.by_setup, setup_kind)
        if test_metrics.trades == 0 or test_metrics.expectancy_r <= 0:
            continue

        strategy_name = f"self_review:{setup_kind.value}:{market}:{param_name}"
        report, beats_random_margin = run_candidate_gauntlet(
            setup_kind, strategy_name, md, trial_cfg, kill_criteria=kill_criteria
        )
        if not candidate_passed(report, beats_random_margin):
            continue

        results.append(
            CandidatePatch(
                patch=ConfigPatch(
                    setup=setup_kind.value,
                    market=market,
                    path=f"setups.{setup_kind.value}.{param_name}",
                    old_value=current,
                    new_value=value,
                ),
                gauntlet_report=report,
            )
        )
    return results
