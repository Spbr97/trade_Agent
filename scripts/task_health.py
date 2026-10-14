"""Scheduled entry point: hourly task/pipeline health check (tradedesk/task_health.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.task_health import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:] or ["check"])
