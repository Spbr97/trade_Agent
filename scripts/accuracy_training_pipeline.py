"""Build an auditable cohort and actually execute the frozen accuracy-model race."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradedesk_lab.accuracy_race_dataset import (
    FEATURE_VERSION,
    RULE_SCORE_VERSION,
    build_accuracy_race_frame,
)
from tradedesk_lab.clean_dataset import FEATURES, load_prepared, prepare_clean

from tradedesk.prediction.race import AccuracyRaceProtocol, run_accuracy_race, save_accuracy_race


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rebuild-clean",
        action="store_true",
        help="Rebuild the causal/economic cohort instead of loading the verified snapshot",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/models/accuracy-race"),
    )
    parser.add_argument(
        "--dataset-out",
        type=Path,
        default=Path("data/reports/accuracy_race_causal.csv"),
    )
    args = parser.parse_args()

    dataset = prepare_clean() if args.rebuild_clean else load_prepared()
    frame = build_accuracy_race_frame(dataset)
    args.dataset_out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.dataset_out, index=False)

    result = run_accuracy_race(
        frame,
        protocol=AccuracyRaceProtocol(version="accuracy-race-v2-causal"),
        feature_names=FEATURES,
        feature_version=FEATURE_VERSION,
    )
    result["dataset_source"] = dataset.manifest
    result["rule_score_version"] = RULE_SCORE_VERSION
    result["dataset_csv"] = str(args.dataset_out)
    path = save_accuracy_race(result, args.out)
    print(
        json.dumps(
            {
                "artifact": str(path),
                "status": result["status"],
                "rows": result["cohort"]["rows"],
                "features": result["cohort"]["feature_count"],
                "models_evaluated": [row["kind"] for row in result["candidates"]],
                "nominee": result["nominee"],
                "locked_test": result["locked_test"]["status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
