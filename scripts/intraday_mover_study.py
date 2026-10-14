"""Scheduled entry point: the intraday mover study (tradedesk_lab/intraday_mover_study.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.intraday_mover_study import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
