"""Nightly: paper-record the results-pop fade forward (tradedesk_lab/results_reaction.py).
Research only - it never alerts. Runs after tradedesk-intraday-study-nightly stores the day's
15-minute bars.

    python scripts/results_fade_forward.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.results_reaction import run_fade_forward  # noqa: E402

if __name__ == "__main__":
    run_fade_forward()
