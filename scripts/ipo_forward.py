"""Nightly: paper-record buying IPOs that open 10-30% up (tradedesk_lab/ipo_forward.py).
Research only - it never alerts. Runs after the day's prices are stored.

    python scripts/ipo_forward.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.ipo_forward import run  # noqa: E402

if __name__ == "__main__":
    run()
