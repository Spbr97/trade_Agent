"""Refresh the read-only, market-separated missed-leader audit.

Examples:
    uv run python scripts/missed_leader_audit.py --market all
    uv run python scripts/missed_leader_audit.py --market crypto --sessions 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradedesk.leader_discovery import run_missed_leader_audit  # noqa: E402

SOURCES = {
    "nse": (
        Path("data/tradedesk.duckdb"),
        Path("data/reports/nse_signal_tracking.jsonl"),
    ),
    "bse": (
        Path("data/bse.duckdb"),
        Path("data/reports/bse_signal_tracking.jsonl"),
    ),
    "crypto": (
        Path("data/crypto.duckdb"),
        Path("data/reports/crypto_signal_tracking.jsonl"),
    ),
}


def main(*, market: str, sessions: int) -> None:
    markets = list(SOURCES) if market == "all" else [market]
    for name in markets:
        db_path, tracker_path = SOURCES[name]
        report = run_missed_leader_audit(
            market=name,
            db_path=db_path,
            tracker_path=tracker_path,
            session_count=sessions,
        )
        latest = report["latest_mature_audit"]
        candidate = latest.get("candidate_recall")
        qualified = latest.get("qualified_recall")
        candidate_text = "not available" if candidate is None else f"{candidate:.1%}"
        qualified_text = "not available" if qualified is None else f"{qualified:.1%}"
        print(
            f"{name.upper()} {latest['session']}: {latest['leader_count']} hindsight "
            f"leaders / {latest['universe_count']} liquid instruments; "
            f"candidate recall={candidate_text}, qualified recall={qualified_text}; "
            f"status={report['status']} (diagnostic only)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=[*SOURCES, "all"], default="all")
    parser.add_argument("--sessions", type=int, default=60)
    args = parser.parse_args()
    main(market=args.market, sessions=args.sessions)
