"""Build or inspect crypto's independent accuracy-program state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.crypto_accuracy_program import (  # noqa: E402
    DEFAULT_OUTPUT,
    save_crypto_accuracy_state,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "status"), default="run", nargs="?")
    args = parser.parse_args()
    path = DEFAULT_OUTPUT / "state.json"
    if args.action == "run":
        path = save_crypto_accuracy_state()
    elif not path.exists():
        raise SystemExit("crypto accuracy checkpoint C0 has not run")
    state = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "checkpoint": state["checkpoint"],
                "status": state["status"],
                "live_forward": state["live_forward"],
                "historical_backfill": state["historical_backfill"],
                "qualification": state["qualification"],
                "next_checkpoint": state["next_checkpoint"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
