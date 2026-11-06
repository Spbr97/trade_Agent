"""Nightly: paper-record shorting strong results at the open (results_short_forward.py).
Research only - it never alerts. Runs after the day's prices are stored.

    python scripts/results_short_forward.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.results_short_forward import run  # noqa: E402

if __name__ == "__main__":
    run()
