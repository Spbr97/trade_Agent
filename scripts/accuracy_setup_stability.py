"""Run Milestone 7's frozen setup-specific stability experiment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradedesk_lab.accuracy_setup_stability import (
    run_setup_stability,
    save_setup_stability,
)
from tradedesk_lab.clean_dataset import load_prepared


def main() -> None:
    result = run_setup_stability(load_prepared())
    path = save_setup_stability(result, Path("data/models/accuracy-setup-stability"))
    print(
        json.dumps(
            {
                "artifact": str(path),
                "status": result["status"],
                "hypotheses": [
                    {
                        "name": row["hypothesis"]["name"],
                        "stability_pass": row["stability_pass"],
                        "aggregate": row["aggregate"],
                        "selector_status": row["selector"]["status"],
                        "operating_point": row["selector"].get("operating_point"),
                    }
                    for row in result["hypotheses"]
                ],
                "nominee": result["nominee"],
                "locked_test": result["locked_test"]["status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
