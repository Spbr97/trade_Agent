"""Run the frozen BSE quick-profit transport/prospective checkpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradedesk_lab.artifacts import ROOT  # noqa: E402
from tradedesk_lab.bse_accuracy_quick_profit import (  # noqa: E402
    DEFAULT_EVIDENCE,
    refresh_bse_quick_profit,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EVIDENCE,
    )
    args = parser.parse_args()
    result = refresh_bse_quick_profit(root=ROOT, output=args.output)
    output = args.output
    summary = {
        "status": result["status"],
        "live": result["live"],
        "baseline_improved": result["baseline_improved"],
        "prospective_sessions": result["readiness"]["prospective_source_sessions"],
        "rules_sample_ready": result["readiness"]["rules_sample_ready"],
        "output": str(output),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
