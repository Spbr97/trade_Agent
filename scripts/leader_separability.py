"""Run the frozen M15 market-specific leader-separability experiment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.leader_separability import DB_PATHS, run_leader_separability  # noqa: E402


def main(*, market: str) -> None:
    markets = list(DB_PATHS) if market == "all" else [market]
    for name in markets:
        report = run_leader_separability(market=name)
        locked = report["locked_test"]
        top_one = locked["model"]["top_1"]
        execution = report["executable_replay"]["top_1"]
        print(
            f"{name.upper()}: {report['status']}; model={report['model_bundle']['name']}; "
            f"leader top-1={top_one['precision']:.1%}, "
            f"executable accuracy={execution['strict_accuracy']:.1%}, "
            f"mean net={execution['mean_net_r']:.3f}R; "
            f"gates={sum(report['gates'].values())}/{len(report['gates'])}; research only"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=[*DB_PATHS, "all"], default="all")
    arguments = parser.parse_args()
    main(market=arguments.market)
