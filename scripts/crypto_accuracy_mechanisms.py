"""Run, inspect, or verify crypto's frozen C2 mechanism race."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.crypto_accuracy_mechanisms import (  # noqa: E402
    DEFAULT_OUTPUT,
    run_crypto_accuracy_mechanisms,
    verify_crypto_accuracy_mechanisms,
)


def _compact(state: dict) -> dict:
    return {
        key: state.get(key)
        for key in (
            "version",
            "id",
            "status",
            "c1_dataset",
            "c1_readiness",
            "trial_counts",
            "best_trial",
            "mechanisms",
            "source_integrity",
            "first_look_latched",
            "baseline_improved",
            "eligible_for_live",
            "detail",
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "verify", "status"), nargs="?", default="run")
    args = parser.parse_args()
    if args.action == "run":
        result = _compact(run_crypto_accuracy_mechanisms())
    elif args.action == "verify":
        result = verify_crypto_accuracy_mechanisms()
    else:
        path = DEFAULT_OUTPUT / "state.json"
        if not path.exists():
            raise SystemExit("crypto C2 mechanism race has not run")
        result = _compact(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
