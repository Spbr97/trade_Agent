"""Daily: update every market's quick twins, then decide each setup's profit verdict
(src/tradedesk/profit_gate.py) and write the status the scan reads plus the dashboard
scoreboard. Run after the evening trackers (server timer profit-gate.timer).

    python scripts/profit_gate.py
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk import profit_gate, quick_twins  # noqa: E402
from tradedesk.config import load_config  # noqa: E402

MARKETS = ("nse", "bse", "crypto")


def rosters(root: Path) -> dict[str, list[str]]:
    setups = load_config(root).setups.setups
    return {
        m: sorted(
            name
            for name, cfg in setups.items()
            if cfg.enabled
            and (not cfg.markets or m in cfg.markets)
            and m not in cfg.retired_markets
        )  # fmt: skip
        for m in MARKETS
    }


def main() -> int:
    failed = []
    for market in MARKETS:
        try:
            print(f"{market}: {quick_twins.update(market, root=ROOT)} new twins")
        except Exception:  # noqa: BLE001 - one market's store being busy must not stop the gate
            traceback.print_exc()
            failed.append(f"twins:{market}")
    verdicts = profit_gate.evaluate(rosters(ROOT), root=ROOT)
    twins = quick_twins.summaries(MARKETS, root=ROOT)
    try:
        before = json.loads((ROOT / profit_gate.STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    status = profit_gate.write(verdicts, root=ROOT, twins=twins)
    changes = [
        f"{m.upper()} {setup}: {row['status'].upper()} - {row['reason']}"
        for m, rows in status["markets"].items()
        for setup, row in rows.items()
        if (before.get("markets", {}).get(m, {}).get(setup) or {}).get("status") != row["status"]
        and row["status"] != "collecting"
    ]
    if changes:
        from tradedesk.task_health import alert

        alert("tradedesk profit verdicts changed", "\n".join(changes)[:900])
    for market, rows in status["markets"].items():
        alerting = {s: r for s, r in rows.items() if r["size_factor"] > 0}
        print(f"{market}: {len(rows)} setups judged; may alert: {json.dumps(alerting)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
