"""Run the frozen anticipatory-entry and quick-profit geometry experiment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradedesk_lab.accuracy_geometry import run_accuracy_geometry, save_accuracy_geometry
from tradedesk_lab.clean_dataset import load_prepared


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=Path("data/models/accuracy-geometry")
    )
    parser.add_argument(
        "--workers", type=int, default=min(6, os.cpu_count() or 1)
    )
    args = parser.parse_args()
    result = run_accuracy_geometry(
        load_prepared(),
        progress=lambda completed, total: print(
            f"Geometry progress: {completed}/{total}", flush=True
        ),
        workers=max(1, args.workers),
    )
    path = save_accuracy_geometry(result, args.out)
    best = max(
        result["candidates"],
        key=lambda row: (
            row["wilson_lower_bound"], row["observed_success"], row["expectancy_r"]
        ),
    )
    print(
        json.dumps(
            {
                "artifact": str(path),
                "status": result["status"],
                "development_rows": result["development"]["rows"],
                "geometries": len(result["candidates"]),
                "best": best,
                "nominee": result["nominee"],
                "locked_test": result["locked_test"]["status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
