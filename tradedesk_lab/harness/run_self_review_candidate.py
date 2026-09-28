"""Adapts a self-review candidate's real backtest trades into `HarnessTrade`s and runs the
same, unmodified `run_gauntlet` every other candidate in this project must clear - modeled
closely on `run_crypto_momentum.py`, but sourcing trades from the REAL event-driven
backtester (`backtest/runner.py::run_backtest`) instead of a hand-rolled bar loop, since a
self-review candidate is always either an existing production setup (config-tuning,
retire/replace) or one authored to the same `Setup` protocol (new-detector, Phase 3) - the
production backtester already knows how to run any of those correctly.

`Signal.t2` (the final target used for the net reward:risk gate, already a real field on
every setup's armed signal - see `engine/signals.py::Signal`'s own docstring) stands in for
`HarnessTrade.target`, even for a partial-target/ATR-trailing exit: it is the existing "final
target" concept these setups already compute for their own R:R gate, not an invented number -
but it does mean the random-entry benchmark compares against a fixed-target proxy rather than
replaying the trailing-stop mechanics exactly. This is a disclosed approximation, not a
silent one; a stricter version (replaying the real trailing logic under a random entry) is a
worthwhile future improvement, not a blocker for Phase 1's config-tuning path, whose primary
mandatory bar is the same +0.10R-over-random margin `engine/scoring.py::eligibility()` already
requires (checked explicitly here, not only via the gauntlet's own looser random-entry pass).
"""

from __future__ import annotations

from typing import Any

from tradedesk.backtest.portfolio import ClosedTrade
from tradedesk.backtest.runner import BacktestConfig, MarketData, run_backtest
from tradedesk.engine.signals import SetupKind
from tradedesk_lab.harness.gauntlet import GauntletReport, run_gauntlet
from tradedesk_lab.harness.spec import KillCriteria
from tradedesk_lab.harness.trade import HarnessTrade

MUST_BEAT_RANDOM_BY_R = 0.10  # engine/scoring.py::EligibilityPolicy's own default


def _bars_by_code(md: MarketData, codes: set[str]) -> dict[str, tuple[Any, Any, Any, Any]]:
    bars: dict[str, tuple[Any, Any, Any, Any]] = {}
    for code in codes:
        frame = md.features.get(code)
        if frame is None:
            continue
        bars[code] = (
            frame["open"].to_numpy(float),
            frame["high"].to_numpy(float),
            frame["low"].to_numpy(float),
            frame["close"].to_numpy(float),
        )
    return bars


def _to_harness_trade(trade: ClosedTrade, md: MarketData) -> HarnessTrade | None:
    positions = md.pos_by_date.get(trade.scrip_code)
    if positions is None:
        return None
    entry_bar = positions.get(trade.entry_date)
    exit_bar = positions.get(trade.exit_date)
    if entry_bar is None or exit_bar is None:
        return None
    entry_price = trade.position.entry_price
    stop = trade.position.signal.stop
    target = trade.position.signal.t2
    risk_per_share = entry_price - stop
    total_risk = trade.position.initial_risk
    if risk_per_share <= 0 or total_risk <= 0:
        return None
    # r_multiple is NET (after costs, per metrics()'s own use of it for expectancy_r) - back
    # out gross_r by adding the cost-in-R this trade actually paid.
    cost_in_r = trade.costs / total_risk
    return HarnessTrade(
        scrip_code=trade.scrip_code,
        entry_at=trade.entry_date.isoformat(),
        exit_at=trade.exit_date.isoformat(),
        entry_bar=entry_bar,
        window_start=entry_bar,
        window_end=exit_bar,
        entry=entry_price,
        stop=stop,
        target=target,
        gross_r=trade.r_multiple + cost_in_r,
        net_r=trade.r_multiple,
    )


def run_candidate_gauntlet(
    setup_kind: SetupKind,
    strategy_name: str,
    md: MarketData,
    cfg: BacktestConfig,
    *,
    kill_criteria: KillCriteria,
    seed: int = 20260101,
) -> tuple[GauntletReport, bool]:
    """Runs the real backtester once with `cfg` (already carrying whatever candidate
    parameters the caller wants validated) restricted to `setup_kind`, adapts its closed
    trades, and calls `run_gauntlet` unmodified. Returns `(report, beats_random_by_required_margin)`
    - the second value is this feature's own extra check (see module docstring), computed
    from the gauntlet's own random-entry-benchmark stage numbers rather than re-deriving them.
    """

    result = run_backtest(md, cfg)
    trades = [t for t in result.portfolio.closed if t.setup == setup_kind.value]
    codes = {t.scrip_code for t in trades}
    harness_trades = [ht for t in trades if (ht := _to_harness_trade(t, md)) is not None]
    bars_by_code = _bars_by_code(md, codes)

    report = run_gauntlet(
        strategy_name,
        harness_trades,
        bars_by_code,
        kill_criteria,
        seed=seed,
    )

    beats_random = False
    stage = report.stages.get("random_entry_benchmark")
    if stage is not None:
        setup_mean = stage.get("setup_mean_gross_r")
        null_mean = stage.get("null_mean_gross_r")
        if setup_mean is not None and null_mean is not None:
            beats_random = (setup_mean - null_mean) >= MUST_BEAT_RANDOM_BY_R
    return report, beats_random


def candidate_passed(report: GauntletReport, beats_random_margin: bool) -> bool:
    kill = report.kill_criteria or {}
    return report.stopped_at is None and bool(kill.get("passed")) and beats_random_margin
