"""Activate, collect, or inspect the M8 forward-only accuracy shadow cohort."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.accuracy_prospective_shadow import activate, collect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("activate", "collect", "status"))
    args = parser.parse_args()
    state_path = ROOT / "data/m14_m18/accuracy_prospective_shadow/state.json"
    if args.action == "activate":
        state = activate()
    elif args.action == "collect":
        state = collect()
    else:
        if not state_path.exists():
            raise SystemExit("M8 prospective shadow is not activated")
        state = json.loads(state_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {"activation": state["activation"], "summary": state["summary"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
