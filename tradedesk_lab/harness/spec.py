"""Phase 0 formalised as data. A rule set's mechanical definition is not re-invented here -
it's any object already implementing this project's own setup protocol
(`arm(df, ctx, params) -> Signal | None`, `tradedesk.setups.base`/`tradedesk.setups.intraday.base`)
plus a matching fill function - "mechanical or it doesn't count" is a rule this project
already enforces everywhere else. What was genuinely missing is `KillCriteria`: the
template's kill-criteria table, as an enforced dataclass instead of a markdown table a human
has to remember to check, with "no re-tuning to rescue it" made structural by reporting every
failing criterion at once rather than stopping at the first.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KillCriteria:
    """Fill in before Phase 1, per the template. All four are required - there is no
    "optional" kill criterion, since a missing one is exactly how a bad rule set survives."""

    min_net_expectancy_r: float
    min_sample_size: int
    max_drawdown_pct: float
    max_losing_streak: int


@dataclass(frozen=True)
class KillCriteriaResult:
    passed: bool
    failures: tuple[str, ...]
    measured: dict[str, float]


def evaluate(
    criteria: KillCriteria,
    *,
    net_expectancy_r: float,
    sample_size: int,
    max_drawdown_pct: float,
    losing_streak: int,
) -> KillCriteriaResult:
    """Reports every criterion that failed, not just the first, so a caller can't quietly
    patch one number and re-run past a miss that's still there elsewhere."""
    failures: list[str] = []
    if sample_size < criteria.min_sample_size:
        failures.append(f"sample_size {sample_size} < required {criteria.min_sample_size}")
    if net_expectancy_r < criteria.min_net_expectancy_r:
        failures.append(
            f"net_expectancy_r {net_expectancy_r:.4f} < required "
            f"{criteria.min_net_expectancy_r:.4f}"
        )
    if max_drawdown_pct > criteria.max_drawdown_pct:
        failures.append(
            f"max_drawdown_pct {max_drawdown_pct:.1%} > allowed {criteria.max_drawdown_pct:.1%}"
        )
    if losing_streak > criteria.max_losing_streak:
        failures.append(f"losing_streak {losing_streak} > allowed {criteria.max_losing_streak}")
    return KillCriteriaResult(
        passed=not failures,
        failures=tuple(failures),
        measured={
            "net_expectancy_r": net_expectancy_r,
            "sample_size": float(sample_size),
            "max_drawdown_pct": max_drawdown_pct,
            "losing_streak": float(losing_streak),
        },
    )
