"""Phase 4's parameter-sensitivity sweep: perturb a strategy's own numeric parameters +/-20%
and check the result forms a plateau of acceptable outcomes rather than an isolated spike -
the template's own definition of overfitting ("A spike is overfitting")."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SensitivityResult:
    parameter: str
    base_value: float
    base_expectancy_r: float
    low_value: float
    low_expectancy_r: float
    high_value: float
    high_expectancy_r: float

    @property
    def is_plateau(self) -> bool:
        """A plateau means the sign of the result doesn't flip across the +/-20% band: all
        three net-positive, or all three net-negative/zero. One positive spike surrounded by
        negatives (or vice versa) is exactly the overfitting shape this exists to catch -
        the base case being positive is not by itself enough if a small nudge either way
        erases it."""
        values = (self.low_expectancy_r, self.base_expectancy_r, self.high_expectancy_r)
        return all(v > 0 for v in values) or all(v <= 0 for v in values)


def sweep_parameter(
    parameter: str,
    base_value: float,
    run_with_value: Callable[[float], float],
    *,
    pct: float = 0.20,
) -> SensitivityResult:
    """`run_with_value` re-runs the strategy at one parameter value and returns its net
    expectancy in R - the caller owns re-deriving trades at each value; this only frames the
    -20%/base/+20% comparison and the plateau check."""
    low, high = base_value * (1 - pct), base_value * (1 + pct)
    return SensitivityResult(
        parameter=parameter,
        base_value=base_value,
        base_expectancy_r=run_with_value(base_value),
        low_value=low,
        low_expectancy_r=run_with_value(low),
        high_value=high,
        high_expectancy_r=run_with_value(high),
    )
