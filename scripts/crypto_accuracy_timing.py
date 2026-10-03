"""Activate, run, or inspect crypto's prospective same-coin timing control."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.crypto_accuracy_timing import (  # noqa: E402
    DEFAULT_OUTPUT,
    collect_crypto_timing,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "status"), default="run", nargs="?")
    args = parser.parse_args()
    if args.action == "run":
        state = collect_crypto_timing()
    else:
        path = DEFAULT_OUTPUT / "state.json"
        if not path.exists():
            raise SystemExit("crypto timing control has not run")
        state = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "activation": state["activation"],
                "summary": state["summary"],
                "source_integrity": state.get("source_integrity"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
