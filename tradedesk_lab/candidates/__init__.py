"""Self-review Phase 3: sandboxed authoring of genuinely new detection code.

Mirrors the M14-M18 isolation contract `tradedesk_lab/` already proves elsewhere in this
project: nothing here ever wires a candidate INTO production (no edit to
`src.tradedesk.setups.REGISTRY`, no `engine.engine.scan_day` call), all output is confined
to `data/m14_m18/`, and every authoring attempt is recorded in `tradedesk_lab/registry.py`'s
SQLite before it is ever shown to a human. `rules.py`'s improvement variants do call a
production setup's own `arm()` read-only, to re-test that exact pattern under a new
filter/exit - reading production, never changing it.

A candidate here is never a real `SetupKind` - that enum is a fixed production type
(`engine/signals.py::SetupKind`), and adding a member to it, or constructing a real
`Signal` (a pydantic model requiring one), before a human approves the candidate, would
be touching production ahead of approval. Candidates instead implement `LabSetup`
(`base.py`) and return a `LabSignal` - a plain, duck-typed stand-in that satisfies the
same attributes `backtest/fills.py::evaluate_entry`/`evaluate_exit` actually read
(`trigger`, `stop`, `t1`, `t2`, `atr`, `chased_atr_mult`, `exit_plan`, `valid_sessions`),
so those exact tested functions can resolve a lab candidate's trades - reused directly,
not re-derived - without ever constructing a real, enum-typed `Signal`.
"""

from __future__ import annotations

from typing import Any

from .mean_reversion_v1 import MeanReversionV1
from .rules import ImprovedSetup, RuleCandidate, improvement_candidates, rule_candidates

# The hand-written candidates. The self-review replacement search draws from
# `candidate_space()` below, which includes these plus the generated rule grammar.
CANDIDATE_LIBRARY = [MeanReversionV1()]


def candidate_space(
    failing_setups: list[str], setup_params: dict[str, dict[str, Any]]
) -> list[Any]:
    """Everything the replacement search may test for one market, in priority order:
    new-method rules first (research-backed ones leading, per `rules.TRIGGERS` order), then
    the hand-written library, then improvement variants of the failing setups last - a new
    filter or exit on a pattern that already loses to random entry timing is the least
    likely place to find an edge. `setup_params` is config/setups.yaml's per-setup blocks
    at search time."""

    space: list[Any] = list(rule_candidates())
    space.extend(CANDIDATE_LIBRARY)
    for setup in failing_setups:
        space.extend(improvement_candidates(setup, setup_params.get(setup, {})))
    return space


__all__ = [
    "CANDIDATE_LIBRARY",
    "ImprovedSetup",
    "MeanReversionV1",
    "RuleCandidate",
    "candidate_space",
]
