"""After each close: the hedged straddle's forward paper trial, paper only
(tradedesk_lab/hedged_straddle.py). Replays the day's saved option minutes; it reads files and
never places an order.

    python scripts/hedged_straddle_forward.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.hedged_straddle import forward  # noqa: E402

if __name__ == "__main__":
    report = forward()
    print(f"{report['new_rows']} new rows, unfinished {report['unfinished_sessions']}", flush=True)
    for name, book in report["books"].items():
        print(name, book["sessions"], "sessions", round(book["net_inr"]), book["fails"], flush=True)
