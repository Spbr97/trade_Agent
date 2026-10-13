"""Build the potential-movers watch (research only) for one market from stored daily bars."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.potential_movers import load_potential_movers, run_potential_movers  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status", "preview"])
    parser.add_argument("--market", choices=["nse", "bse", "crypto"], default="nse")
    parser.add_argument("--as-of", help="preview only: YYYY-MM-DD, writes nothing")
    arguments = parser.parse_args()
    if arguments.command == "status":
        print(json.dumps(load_potential_movers(arguments.market), indent=2, default=str))
        return
    as_of = date.fromisoformat(arguments.as_of) if arguments.as_of else None
    if arguments.command == "preview" and as_of is None:
        parser.error("preview needs --as-of")
    report = run_potential_movers(
        arguments.market, as_of=as_of, write_ledger=arguments.command == "run"
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
