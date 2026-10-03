"""Run or inspect M10's prospective matched-random selection control."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.accuracy_prospective_control import (  # noqa: E402
    DEFAULT_OUTPUT,
    collect_control,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "status"), default="run", nargs="?")
    args = parser.parse_args()
    if args.action == "run":
        state = collect_control()
    else:
        path = DEFAULT_OUTPUT / "state.json"
        if not path.exists():
            raise SystemExit("M10 control has not run")
        state = json.loads(path.read_text(encoding="utf-8"))
    registration_keys = ("version", "registered_at", "seed", "n_cohorts", "scope")
    print(
        json.dumps(
            {
                "registration": {key: state[key] for key in registration_keys},
                "summary": state["summary"],
                "outcome_parity": state.get("outcome_parity"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
