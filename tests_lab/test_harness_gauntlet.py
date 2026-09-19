"""End-to-end gauntlet tests against synthetic, known-correct fixtures - mirroring
tests/backtest/test_null_baseline.py's "planted edge on a flat instrument" pattern, since
`run_gauntlet`'s first stage IS that same null test. A planted edge must clear every stage and
the kill criteria; a setup indistinguishable from random must stop at stage 1 with nothing
past it computed."""

from __future__ import annotations

import numpy as np
import pytest
from tradedesk_lab.harness.gauntlet import run_gauntlet
from tradedesk_lab.harness.spec import KillCriteria
from tradedesk_lab.harness.trade import HarnessTrade


def _flat_bars(n: int, price: float = 100.0):
    o = np.full(n, price)
    h = np.full(n, price + 0.5)
    low_ = np.full(n, price - 0.5)
    c = np.full(n, price)
    return o, h, low_, c


def _trades(
    gross_r: float, net_r: float, *, days: int = 10, per_day: int = 6
) -> list[HarnessTrade]:
    """Several trades per distinct day - `tradedesk_lab.validation.walk_forward` requires
    at least 30 training rows before it will produce a fold at all, calibrated for the M14
    lab's dense full-universe scans; one trade per day (as null_baseline's own tests use)
    isn't enough rows for this stage specifically."""
    trades = []
    for d in range(days):
        for k in range(per_day):
            trades.append(
                HarnessTrade(
                    scrip_code="X",
                    entry_at=f"2026-01-{d + 1:02d}T{10 + k:02d}:00:00+05:30",
                    exit_at=f"2026-01-{d + 1:02d}T{11 + k:02d}:00:00+05:30",
                    entry_bar=10 + 5 * (d * per_day + k),
                    window_start=0,
                    window_end=999,
                    entry=100.0,
                    stop=99.0,
                    target=102.0,
                    gross_r=gross_r,
                    net_r=net_r,
                )
            )
    return trades


def _planted_edge_trades() -> list[HarnessTrade]:
    """Every trade hits its target immediately (gross_r=2.0); a random entry on this same
    flat instrument can only time out at 0R - the exact shape null_baseline's own tests use
    to prove a real edge is detectable."""
    return _trades(gross_r=2.0, net_r=1.8)


def _random_like_trades() -> list[HarnessTrade]:
    """Real trades ARE just random draws (gross_r=0.0, matching a timeout) - no edge here,
    and the gate must say so at stage 1."""
    return _trades(gross_r=0.0, net_r=-0.1)


def test_no_trades_stops_immediately():
    report = run_gauntlet("empty", [], {}, KillCriteria(0.0, 1, 1.0, 100))
    assert report.stopped_at == "no_trades"


def test_a_setup_indistinguishable_from_random_stops_at_stage_one():
    bars = {"X": _flat_bars(1000)}
    report = run_gauntlet(
        "random_like",
        _random_like_trades(),
        bars,
        KillCriteria(
            min_net_expectancy_r=0.0, min_sample_size=1, max_drawdown_pct=1.0, max_losing_streak=100
        ),
        n_null_cohorts=200,
    )
    assert report.stopped_at == "random_entry_benchmark"
    assert not report.stages["random_entry_benchmark"]["passes"]
    # Nothing past stage 1 should have been computed.
    assert "in_sample" not in report.stages
    assert report.kill_criteria is None


def test_a_planted_edge_clears_every_stage_and_the_kill_criteria():
    bars = {"X": _flat_bars(1000)}
    criteria = KillCriteria(
        min_net_expectancy_r=1.0, min_sample_size=10, max_drawdown_pct=0.5, max_losing_streak=5
    )
    report = run_gauntlet(
        "planted_edge",
        _planted_edge_trades(),
        bars,
        criteria,
        n_null_cohorts=500,
        n_monte_carlo=200,
    )
    assert report.stages["random_entry_benchmark"]["passes"]
    assert report.stages["in_sample"]["net_expectancy_r"] == pytest.approx(1.8)
    assert report.stages["walk_forward"]  # at least one fold produced
    assert all((f["test_expectancy_r"] or 0) > 0 for f in report.stages["walk_forward"])
    assert "monte_carlo" in report.stages
    assert report.kill_criteria is not None
    assert report.kill_criteria["passed"]
    assert report.stopped_at is None


def test_kill_criteria_failure_is_the_stop_reason_when_everything_else_passes():
    bars = {"X": _flat_bars(1000)}
    # Same planted edge, but an unreasonably high sample-size bar nothing here can clear.
    criteria = KillCriteria(
        min_net_expectancy_r=1.0, min_sample_size=10_000, max_drawdown_pct=0.5, max_losing_streak=5
    )
    report = run_gauntlet(
        "planted_edge_too_thin",
        _planted_edge_trades(),
        bars,
        criteria,
        n_null_cohorts=300,
        n_monte_carlo=100,
    )
    assert report.stopped_at == "kill_criteria"
    assert not report.kill_criteria["passed"]
    assert any("sample_size" in f for f in report.kill_criteria["failures"])
