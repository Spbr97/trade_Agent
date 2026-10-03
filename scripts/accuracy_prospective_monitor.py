"""Run or inspect the M9 evidence-integrity monitor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tradedesk_lab.accuracy_prospective_monitor import (  # noqa: E402
    DEFAULT_OUTPUT,
    read_audit,
    run_monitor,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "status", "verify"), default="run", nargs="?")
    args = parser.parse_args()
    if args.action == "run":
        result = run_monitor()
    elif args.action == "verify":
        events = read_audit(DEFAULT_OUTPUT / "audit.jsonl")
        result = {"status": "valid", "events": len(events), "head": events[-1]["sha256"]}
    else:
        path = DEFAULT_OUTPUT / "latest.json"
        if not path.exists():
            raise SystemExit("M9 monitor has not run")
        result = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
