"""Seal, build or inspect the frozen M18-A selection-readiness dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.selection_readiness import (  # noqa: E402
    MARKETS,
    build_selection_readiness,
    load_selection_readiness_status,
    seal_selection_readiness_manifest,
)


def main(*, command: str, market: str) -> None:
    markets = list(MARKETS) if market == "all" else [market]
    for name in markets:
        if command == "manifest":
            result = seal_selection_readiness_manifest(name)
            print(
                f"{name.upper()}: M18-A manifest sealed; "
                f"sessions={sum(len(value) for value in result['split'].values())}; "
                f"candidates={result['input_binding']['m17_acquisition_rows']}; "
                "no paths or outcomes opened"
            )
        elif command == "build":
            result = build_selection_readiness(name)
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        else:
            print(
                json.dumps(
                    load_selection_readiness_status(name),
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["manifest", "build", "status"])
    parser.add_argument("--market", choices=[*MARKETS, "all"], default="all")
    arguments = parser.parse_args()
    main(command=arguments.command, market=arguments.market)
