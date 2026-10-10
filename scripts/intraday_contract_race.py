"""Seal, acquire, evaluate or inspect the frozen M17 intraday contract race."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.broker.coindcx.rest import CoinDcxClient  # noqa: E402
from tradedesk.cli import _with_client  # noqa: E402
from tradedesk.intraday_contract_race import (  # noqa: E402
    DB_PATHS,
    acquire_manifest_paths,
    build_acquisition_manifest,
    load_intraday_contract_status,
    run_intraday_contract_race,
)


async def _acquire(markets: list[str]) -> None:
    equities = [market for market in markets if market in {"nse", "bse"}]
    if equities:

        async def use_equity_client(client: Any) -> None:
            for market in equities:
                result = await acquire_manifest_paths(market=market, client=client)
                print(json.dumps(result, sort_keys=True, default=str))

        await _with_client(use_equity_client)
    if "crypto" in markets:
        async with CoinDcxClient() as client:
            result = await acquire_manifest_paths(market="crypto", client=client)
            print(json.dumps(result, sort_keys=True, default=str))


def main(*, command: str, market: str) -> None:
    markets = list(DB_PATHS) if market == "all" else [market]
    if command == "acquire":
        asyncio.run(_acquire(markets))
        return
    for name in markets:
        if command == "manifest":
            result = build_acquisition_manifest(market=name)
            print(
                f"{name.upper()}: manifest sealed; "
                f"rows={result['acquisition_rows']['rows']}; "
                f"windows={result['acquisition_rows']['sealed_windows']}; "
                "no outcomes opened"
            )
        elif command == "run":
            result = run_intraday_contract_race(market=name)
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        else:
            print(
                json.dumps(
                    load_intraday_contract_status(name),
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["manifest", "acquire", "run", "status"])
    parser.add_argument("--market", choices=[*DB_PATHS, "all"], default="all")
    arguments = parser.parse_args()
    main(command=arguments.command, market=arguments.market)

