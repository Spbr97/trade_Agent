"""Scheduled entry point: fetch recent NSE exchange filings (tradedesk/filings.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.filings import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:] or ["fetch", "--days", "2"])
