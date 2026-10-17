"""Scheduled entry point: the multi-day hold study (tradedesk_lab/multiday_hold_study.py),
run for every market. One market failing does not stop the others; the exit code reports it."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.multiday_hold_study import main  # noqa: E402

if __name__ == "__main__":
    failed = []
    for market in sys.argv[1:] or ["nse", "bse", "crypto"]:
        try:
            main(["--market", market])
        except Exception:
            traceback.print_exc()
            failed.append(market)
    sys.exit(1 if failed else 0)
