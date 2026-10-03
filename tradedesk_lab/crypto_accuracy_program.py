"""Independent accuracy program and baseline accounting for crypto.

Crypto evidence is deliberately never pooled with NSE or BSE.  The live tracker log is
also split by source: historical backfill is development evidence, while rows emitted by
actual scheduled agent runs are forward monitoring evidence.  This module is reporting-
only and cannot alter scanning, scoring, alerts, sizing, management, or orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradedesk.reliability import wilson_lower_bound
from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json

VERSION = "crypto-accuracy-program-v1"
DEFAULT_LOG = ROOT / "data/reports/crypto_signal_tracking.jsonl"
DEFAULT_UNIVERSE = ROOT / "data/reports/crypto_universe_latest.json"
DEFAULT_OUTPUT = OUTPUT / "crypto_accuracy_program"


@dataclass(frozen=True)
class CryptoAccuracyProtocol:
    market: str = "crypto"
    target_accuracy: float = 0.80
    minimum_wilson_lower: float = 0.70
    minimum_prospective_calls: int = 100
    minimum_prospective_sessions: int = 30
    minimum_random_advantage_r: float = 0.10
    evidence_policy: str = "backfill_development_and_live_forward_never_pooled"
    economics_policy: str = "report_gross_after_cost_and_after_tax_separately"
    authority: str = "research_only_no_live_or_order_authority"


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        row.setdefault("source", "backfill")
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("outcome") is not None]
    wins = sum(row.get("label") == 1 for row in resolved)
    returns = [
        float(row["r_multiple"])
        for row in resolved
        if row.get("r_multiple") is not None
    ]
    sessions = {row.get("armed_on") for row in rows if row.get("armed_on")}
    return {
        "calls": len(rows),
        "resolved_calls": len(resolved),
        "pending_calls": len(rows) - len(resolved),
        "wins": wins,
        "accuracy": wins / len(resolved) if resolved else None,
        "wilson_lower_bound": wilson_lower_bound(wins, len(resolved)),
        "gross_expectancy_r": sum(returns) / len(returns) if returns else None,
        "sessions": len(sessions),
        "first_session": min(sessions) if sessions else None,
        "last_session": max(sessions) if sessions else None,
    }


def _by_setup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("setup") or "unknown"), []).append(row)
    return [
        {"setup": setup, **_summary(group)}
        for setup, group in sorted(grouped.items())
    ]


def build_crypto_accuracy_state(
    log_path: Path = DEFAULT_LOG,
    universe_path: Path = DEFAULT_UNIVERSE,
) -> dict[str, Any]:
    """Create C0's source-separated baseline without changing either source artifact."""
    rows = _load_rows(log_path)
    live = [row for row in rows if row["source"] == "live"]
    backfill = [row for row in rows if row["source"] == "backfill"]
    other = [row for row in rows if row["source"] not in {"live", "backfill"}]
    tradeable_live = [row for row in live if not row.get("rejected_for")]
    universe: dict[str, Any] = {}
    if universe_path.exists():
        universe = json.loads(universe_path.read_text(encoding="utf-8"))
    protocol = CryptoAccuracyProtocol()
    return {
        "version": VERSION,
        "checkpoint": "C0",
        "status": "baseline_frozen_research_only",
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": asdict(protocol),
        "source_integrity": {
            "log_path": str(log_path),
            "log_sha256": _sha256(log_path),
            "universe_path": str(universe_path),
            "universe_sha256": _sha256(universe_path),
            "unknown_source_rows": len(other),
            "evidence_mixed": False,
        },
        "universe": {
            key: universe.get(key)
            for key in (
                "session",
                "active_inr_pairs",
                "scanned_pairs",
                "pairs_with_closed_session",
                "fetch_errors",
            )
        },
        "live_forward": {
            **_summary(live),
            "by_setup": _by_setup(live),
            "currently_tradeable": _summary(tradeable_live),
            "evidence_class": "forward_agent_evaluated_monitoring",
        },
        "historical_backfill": {
            **_summary(backfill),
            "by_setup": _by_setup(backfill),
            "evidence_class": "development_only",
        },
        "qualification": {
            "eligible_for_live": False,
            "accuracy_improvement_established": False,
            "blockers": [
                "no frozen crypto challenger has passed locked historical evaluation",
                "no crypto challenger has passed matched random-selection and timing controls",
                "no crypto challenger has 100 resolved prospective calls across 30 sessions",
                "after-cost and reporting-only after-tax economics are not attached "
                "to this tracker baseline",
            ],
        },
        "next_checkpoint": "C1_point_in_time_dataset_and_label_integrity",
    }


def save_crypto_accuracy_state(
    output: Path = DEFAULT_OUTPUT,
    log_path: Path = DEFAULT_LOG,
    universe_path: Path = DEFAULT_UNIVERSE,
) -> Path:
    state = build_crypto_accuracy_state(log_path, universe_path)
    path = output / "state.json"
    write_json(path, state)
    return path
