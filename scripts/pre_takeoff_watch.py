"""Collect or inspect forward pre-takeoff watch candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.pre_takeoff_watch import (  # noqa: E402
    collect_crypto_pre_takeoff_watch,
    load_pre_takeoff_watch,
)


def main(*, command: str, market: str) -> None:
    if command == "collect" and market != "crypto":
        raise SystemExit(
            "live collection is currently available for crypto; "
            "NSE/BSE wait for market data"
        )
    result = (
        asyncio.run(collect_crypto_pre_takeoff_watch())
        if command == "collect"
        else load_pre_takeoff_watch(market)
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "status"])
    parser.add_argument("--market", choices=["nse", "bse", "crypto"], default="crypto")
    arguments = parser.parse_args()
    main(command=arguments.command, market=arguments.market)
