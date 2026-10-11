"""Run or inspect the frozen M19 pre-takeoff selector experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.pre_takeoff_selector import (  # noqa: E402
    ALL_MARKETS,
    load_pre_takeoff_status,
    run_pre_takeoff_selector,
)


def main(*, command: str, market: str) -> None:
    markets = list(ALL_MARKETS) if market == "all" else [market]
    for name in markets:
        result = (
            run_pre_takeoff_selector(name)
            if command == "run"
            else load_pre_takeoff_status(name)
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status"])
    parser.add_argument("--market", choices=[*ALL_MARKETS, "all"], default="all")
    arguments = parser.parse_args()
    main(command=arguments.command, market=arguments.market)
