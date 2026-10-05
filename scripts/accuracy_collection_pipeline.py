"""Run one ordered accuracy-evidence collection pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.accuracy_collection_health import run_collection_pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("nse", "bse", "crypto"), required=True)
    args = parser.parse_args()
    report = run_collection_pipeline(args.market)
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "market": report["market"],
                "status": report["status"],
                "failed_stage": report["failed_stage"],
                "source": report["source"],
                "baseline_improved": False,
                "eligible_for_live": False,
            },
            indent=2,
        )
    )
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
