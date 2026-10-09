"""Run, collect, or inspect the frozen M16 execution-aligned experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.execution_aligned_selector import (  # noqa: E402
    DB_PATHS,
    collect_execution_aligned_prospective,
    load_execution_aligned_status,
    run_execution_aligned_development,
)


def main(*, command: str, market: str) -> None:
    markets = list(DB_PATHS) if market == "all" else [market]
    for name in markets:
        if command == "develop":
            result = run_execution_aligned_development(market=name)
            validation = result["validation"]["metrics"]
            diagnostic = result["consumed_diagnostic"]["metrics"]
            print(
                f"{name.upper()}: {result['status']}; "
                f"threshold={result['selected_threshold']}; "
                f"validation={validation['strict_accuracy']} (n={validation['selected']}); "
                f"diagnostic={diagnostic['strict_accuracy']} "
                f"(n={diagnostic['selected']}); "
                f"gates={sum(result['development_gates'].values())}/"
                f"{len(result['development_gates'])}; no live authority"
            )
        elif command == "collect":
            result = collect_execution_aligned_prospective(market=name)
            print(json.dumps(result, sort_keys=True, default=str))
        else:
            print(
                json.dumps(
                    load_execution_aligned_status(name),
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["develop", "collect", "status"])
    parser.add_argument("--market", choices=[*DB_PATHS, "all"], default="all")
    arguments = parser.parse_args()
    main(command=arguments.command, market=arguments.market)
