"""Nightly: paper-record the convergence rule forward (tradedesk_lab/convergence_forward.py).
Research only - it never alerts. Runs after the short trial's filings refresh.

    python scripts/convergence_forward.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.convergence_forward import run  # noqa: E402

if __name__ == "__main__":
    run()
