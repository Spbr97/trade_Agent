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

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class KillCriteria:
    """Fill in before Phase 1, per the template. All four are required - there is no
    "optional" kill criterion, since a missing one is exactly how a bad rule set survives."""

    min_net_expectancy_r: float
    min_sample_size: int
    max_drawdown_pct: float
    max_losing_streak: int


def chance_streak_limit(sample_size: int, loss_rate: float, tail: float = 0.01) -> int:
    """Longest losing run that luck alone would exceed only `tail` (1%) of the time, for
    independent trades with this loss rate over this many trades. Expected number of losing
    runs of length >= L is about n*(1-q)*q^L, so solve n*(1-q)*q^L = tail for L.

    A fixed cap of 10 rejects almost any trend rule once the sample is large (a 39% win
    rate over ~2,000 trades has an expected longest run near 15) while a genuinely broken
    rule still trips it. Independence is an assumption - clustered same-day losses are what
    the drawdown criterion, which is NOT scaled, still guards."""
    if sample_size < 1 or not 0.0 < loss_rate < 1.0:
        return 0
    return math.ceil(math.log(sample_size * (1.0 - loss_rate) / tail) / math.log(1.0 / loss_rate))


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
    loss_rate: float | None = None,
) -> KillCriteriaResult:
    """Reports every criterion that failed, not just the first, so a caller can't quietly
    patch one number and re-run past a miss that's still there elsewhere.

    With `loss_rate` given, the allowed losing streak is the LARGER of the configured cap
    and what chance alone produces at this sample size (`chance_streak_limit`), so the cap
    can only loosen for a big sample, never tighten below what was configured."""
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
    allowed_streak = criteria.max_losing_streak
    if loss_rate is not None:
        allowed_streak = max(allowed_streak, chance_streak_limit(sample_size, loss_rate))
    if losing_streak > allowed_streak:
        failures.append(f"losing_streak {losing_streak} > allowed {allowed_streak}")
    return KillCriteriaResult(
        passed=not failures,
        failures=tuple(failures),
        measured={
            "net_expectancy_r": net_expectancy_r,
            "sample_size": float(sample_size),
            "max_drawdown_pct": max_drawdown_pct,
            "losing_streak": float(losing_streak),
            "allowed_losing_streak": float(allowed_streak),
        },
    )
