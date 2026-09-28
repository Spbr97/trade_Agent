"""The self-review loop (2026-09-27): proposes fixes for setups the rolling-failure monitor
flags, validates each proposal against the real backtest engine and the full harness gauntlet,
and applies a change only after one explicit human approval per proposal.

Nothing here ever writes a config/code change on its own - see `apply.py`'s own docstring for
the exact guard. Every module in this package is production-side orchestration; the sandboxed
authoring of genuinely new detector code lives in `tradedesk_lab/candidates/` instead,
mirroring that package's existing isolation contract.

Several submodules (`config_tuning.py`, `retire_replace.py`, `detector_authoring.py`,
`apply.py`, `orchestrate.py`) import `tradedesk_lab` at module level - a top-level package
outside `src/`, not part of the installed `tradedesk` package. `python -m`/pytest add the
repo root to `sys.path` automatically; the `tradedesk` console script does not (same gap
CLAUDE.md documents for the dashboard), which only surfaces the first time one of these
modules loads outside a test run - e.g. `tradedesk review self-review-run` for real. Fixed
once, here, since this package's `__init__` always runs before any of its submodules do.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
