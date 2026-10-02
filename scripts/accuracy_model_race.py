"""Run the frozen Milestone-3 accuracy challenger race against an explicit CSV cohort."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tradedesk.prediction.race import run_accuracy_race, save_accuracy_race


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path, default=Path("data/models/accuracy-race"),
    )
    args = parser.parse_args()
    result = run_accuracy_race(pd.read_csv(args.dataset))
    path = save_accuracy_race(result, args.out)
    print(json.dumps({"artifact": str(path), **result}, indent=2, default=str))
    return 0 if result["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
