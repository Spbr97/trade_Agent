"""Nightly: write the owner's one-number board for the Scoreboard tab (tradedesk/money_board.py).
Reads the paper records only; it never alerts or places an order.

    python scripts/money_board.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.money_board import run  # noqa: E402

if __name__ == "__main__":
    for row in run()["rows"]:
        print(row["name"], round(row["rupees"]), row["verdict"], flush=True)
