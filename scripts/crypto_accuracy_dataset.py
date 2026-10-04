"""Build, inspect, or verify crypto's frozen C1 accuracy dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.crypto_accuracy_dataset import (  # noqa: E402
    DEFAULT_OUTPUT,
    materialize_crypto_dataset,
    verify_crypto_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "verify", "status"), nargs="?", default="build")
    args = parser.parse_args()
    if args.action == "build":
        state = materialize_crypto_dataset()
        result = {
            "id": state["id"],
            "status": state["status"],
            "membership": state["membership"],
            "coverage_summary": state["coverage_summary"],
            "source_integrity": state["source_integrity"],
            "eligible_for_live": state["eligible_for_live"],
        }
    elif args.action == "verify":
        result = verify_crypto_dataset()
    else:
        path = DEFAULT_OUTPUT / "state.json"
        if not path.exists():
            raise SystemExit("crypto C1 dataset has not been materialized")
        state = json.loads(path.read_text(encoding="utf-8"))
        result = {
            "id": state["id"],
            "status": state["status"],
            "membership": state["membership"],
            "coverage_summary": state["coverage_summary"],
            "source_integrity": state["source_integrity"],
            "eligible_for_live": state["eligible_for_live"],
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
