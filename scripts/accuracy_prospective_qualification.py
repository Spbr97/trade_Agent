"""Run or inspect the atomic NSE prospective qualification review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.accuracy_prospective_qualification import (  # noqa: E402
    DEFAULT_OUTPUT,
    run_qualification,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "status"), default="run", nargs="?")
    args = parser.parse_args()
    path = DEFAULT_OUTPUT / "latest.json"
    if args.action == "run":
        report = run_qualification()
    else:
        if not path.exists():
            raise SystemExit("NSE prospective qualification has not run")
        report = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
