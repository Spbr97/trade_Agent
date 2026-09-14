"""Agent reliability: how much should you actually trust a hit rate, given how few times
it has actually been tested - and one number, tracked daily, for whether that trust is
rising as more real evidence accumulates.

Why not just show win rate
---------------------------
A symbol with 1 resolved call and a win is NOT "100% reliable" - that is exactly the kind
of small-sample overclaiming this project has caught and corrected repeatedly (the
base_breakout "sweet spot", the 150-code cross-sectional smoke test, the momentum train
half). A raw win rate on a handful of calls is noise wearing a percentage sign.

What this module reports instead is the Wilson score interval's LOWER bound - a standard,
conservative way to turn (wins, n) into one number that is naturally low when n is small
(so it is honest about not knowing yet) and rises toward the raw win rate as n grows and
the estimate firms up. It is deliberately not the point estimate: a coin that won 1/1
should not read as more reliable than one that won 60/100, and the Wilson lower bound gets
that right where a naive win-rate ranking would not.

Verified with structural properties (`tests/unit/test_reliability.py`), not a memorised
textbook constant this environment cannot look up to check: monotonic in wins, monotonic
in n at a fixed rate, always in [0, 1], and converges to the raw win rate as n grows large
(the interval narrows to a point at the true value in the limit, which is the actual
mathematical guarantee a confidence interval makes - checking convergence IS checking
correctness here, not a weaker substitute for it).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

Z_95 = 1.959963984540054  # two-sided 95% normal critical value

HISTORY_PATH = Path("data/reports/agent_reliability_history.jsonl")


def wilson_lower_bound(wins: int, n: int, z: float = Z_95) -> float | None:
    """The lower bound of the Wilson score confidence interval for a binomial proportion.
    None when n == 0 - there is nothing to bound. See the module docstring for why this,
    not the raw win rate, is what "confidence" means here."""
    if n <= 0:
        return None
    if wins < 0 or wins > n:
        raise ValueError(f"wins={wins} must be between 0 and n={n}")
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return max(0.0, min(1.0, (centre - margin) / denom))


@dataclass(frozen=True)
class SymbolConfidence:
    symbol: str
    n: int
    wins: int
    win_rate: float
    confidence: float  # Wilson lower bound - the number to actually trust


def symbol_confidence(outcomes: dict[str, list[bool]]) -> list[SymbolConfidence]:
    """`outcomes`: symbol -> list of True/False (won/lost) for every resolved call on that
    symbol. Sorted by confidence descending, so the symbols actually worth trusting surface
    first rather than whichever happened to log the most calls."""
    out = []
    for symbol, results in outcomes.items():
        n = len(results)
        if n == 0:
            continue
        wins = sum(1 for r in results if r)
        conf = wilson_lower_bound(wins, n)
        assert conf is not None  # n > 0 guaranteed above
        out.append(SymbolConfidence(symbol, n, wins, wins / n, conf))
    return sorted(out, key=lambda s: s.confidence, reverse=True)


def overall_reliability(wins: int, n: int) -> dict[str, Any]:
    """The single top-line number: Wilson lower bound pooled across every REAL resolved
    call the agent has actually made (NSE paper book + crypto/BSE calls tagged
    source="live") - never the historical backfill, which is real market history but not
    something "the agent" did. Forward research candidates (research_tracker.py) are also
    excluded: they are explicitly built to never alert or represent a call you could have
    acted on, so folding them into "agent reliability" would overstate what the label means."""
    conf = wilson_lower_bound(wins, n)
    return {
        "n": n, "wins": wins,
        "win_rate": wins / n if n > 0 else None,
        "confidence": conf,
    }


def append_reliability_history(record: dict[str, Any], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": date.today().isoformat(), **record}, default=str) + "\n")


def load_reliability_history(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]  # noqa: E501
