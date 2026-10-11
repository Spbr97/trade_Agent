"""Collect or inspect forward pre-takeoff watch candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk_lab.accuracy_collection_health import (  # noqa: E402
    DEFAULT_OUTPUT as COLLECTION_HEALTH_OUTPUT,
)
from tradedesk_lab.accuracy_collection_health import _pipeline_lock  # noqa: E402

from tradedesk.cli import _with_client  # noqa: E402
from tradedesk.pre_takeoff_watch import (  # noqa: E402
    collect_crypto_pre_takeoff_watch,
    collect_equity_pre_takeoff_watch,
    load_pre_takeoff_watch,
)


async def _collect_equities(markets: tuple[str, ...]) -> dict[str, Any]:
    async def use_client(client: Any) -> dict[str, Any]:
        reports: dict[str, Any] = {}
        for name in markets:
            reports[name] = await collect_equity_pre_takeoff_watch(name, client=client)
        return reports

    return await _with_client(use_client)


def main(*, command: str, market: str) -> None:
    if command == "status":
        result = (
            {name: load_pre_takeoff_watch(name) for name in ("nse", "bse")}
            if market == "equity"
            else load_pre_takeoff_watch(market)
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return
    if market == "crypto":
        result = asyncio.run(collect_crypto_pre_takeoff_watch())
    else:
        markets = ("nse", "bse") if market == "equity" else (market,)
        lock_path = COLLECTION_HEALTH_OUTPUT / "equity.pipeline.lock"
        with _pipeline_lock(lock_path):
            result = asyncio.run(_collect_equities(markets))
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "status"])
    parser.add_argument(
        "--market", choices=["nse", "bse", "crypto", "equity"], default="crypto"
    )
    arguments = parser.parse_args()
    main(command=arguments.command, market=arguments.market)
