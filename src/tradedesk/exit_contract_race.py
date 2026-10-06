"""Paired quick-profit versus swing evidence on identical sealed entries."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from tradedesk.engine.scoring import wilson_lower_bound

RACE_VERSION = "exit-contract-race-v1"
QUICK_VERSION = "quick-profit-v1"
SWING_VERSION = "swing-v1"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _base_signal_id(signal_id: str) -> str:
    for version in (QUICK_VERSION, SWING_VERSION):
        suffix = f"::{version}"
        if signal_id.endswith(suffix):
            return signal_id[: -len(suffix)]
    return signal_id


def _score(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    labels = [int(row["label"]) for row in rows]
    wins = sum(labels)

    def mean(field: str) -> float | None:
        values = [number for row in rows if (number := _finite(row.get(field))) is not None]
        return float(np.mean(values)) if values else None

    maximum_losing_streak = 0
    streak = 0
    for label in labels:
        streak = 0 if label else streak + 1
        maximum_losing_streak = max(maximum_losing_streak, streak)
    sessions = {str(row.get("armed_on")) for row in rows}
    return {
        "n": len(rows),
        "wins": wins,
        "strict_accuracy": wins / len(rows) if rows else None,
        "wilson_95_low": wilson_lower_bound(wins, len(rows)) if rows else None,
        "mean_gross_r": mean("gross_r"),
        "mean_net_r": mean("net_r"),
        "mean_after_tax_r": mean("after_tax_r"),
        "mean_holding_sessions": mean("holding_sessions"),
        "maximum_losing_streak": maximum_losing_streak,
        "sessions": len(sessions),
        "calls_per_session": len(rows) / len(sessions) if sessions else 0.0,
    }


def evaluate_exit_contract_race(
    records: Iterable[Mapping[str, Any]], *, market: str
) -> dict[str, Any]:
    """Evaluate only complete pairs whose prediction geometry is identical."""

    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    relevant = 0
    for raw in records:
        row = dict(raw)
        if row.get("market") != market or row.get("contract_version") not in {
            QUICK_VERSION,
            SWING_VERSION,
        }:
            continue
        relevant += 1
        grouped.setdefault(_base_signal_id(str(row.get("signal_id"))), {})[
            str(row["contract_version"])
        ] = row
    quick_rows: list[Mapping[str, Any]] = []
    swing_rows: list[Mapping[str, Any]] = []
    exclusions: dict[str, int] = {
        "missing_pair": 0,
        "entry_geometry_mismatch": 0,
        "not_both_mature_valid": 0,
        "evidence_class_mismatch": 0,
    }
    for pair in grouped.values():
        if QUICK_VERSION not in pair or SWING_VERSION not in pair:
            exclusions["missing_pair"] += 1
            continue
        quick, swing = pair[QUICK_VERSION], pair[SWING_VERSION]
        geometry = ("market", "symbol", "setup", "armed_on", "entry", "stop", "source")
        if any(quick.get(field) != swing.get(field) for field in geometry):
            exclusions["entry_geometry_mismatch"] += 1
            continue
        if quick.get("evidence_class") != swing.get("evidence_class"):
            exclusions["evidence_class_mismatch"] += 1
            continue
        if any(
            row.get("outcome_state") != "resolved_call" or row.get("label") not in {0, 1}
            for row in (quick, swing)
        ):
            exclusions["not_both_mature_valid"] += 1
            continue
        quick_rows.append(quick)
        swing_rows.append(swing)
    quick_score, swing_score = _score(quick_rows), _score(swing_rows)
    breakdowns: dict[str, Any] = {}
    for field in ("setup", "regime"):
        values = sorted(
            {
                str(row.get(field) or "unavailable")
                for row in [*quick_rows, *swing_rows]
            }
        )
        breakdowns[field] = {
            value: {
                "quick_profit": _score(
                    [row for row in quick_rows if str(row.get(field) or "unavailable") == value]
                ),
                "swing": _score(
                    [row for row in swing_rows if str(row.get(field) or "unavailable") == value]
                ),
            }
            for value in values
        }
    accuracy_delta = (
        float(quick_score["strict_accuracy"] - swing_score["strict_accuracy"])
        if quick_score["strict_accuracy"] is not None
        and swing_score["strict_accuracy"] is not None
        else None
    )
    net_delta = (
        float(quick_score["mean_net_r"] - swing_score["mean_net_r"])
        if quick_score["mean_net_r"] is not None and swing_score["mean_net_r"] is not None
        else None
    )
    quick_candidate = bool(
        quick_rows
        and accuracy_delta is not None
        and accuracy_delta > 0
        and quick_score["mean_net_r"] is not None
        and quick_score["mean_net_r"] > 0
        and net_delta is not None
        and net_delta >= 0
    )
    return {
        "version": RACE_VERSION,
        "market": market,
        "status": "available" if quick_rows else "insufficient_paired_evidence",
        "source_contract_rows": relevant,
        "paired_mature_entries": len(quick_rows),
        "exclusions": {key: value for key, value in exclusions.items() if value},
        "quick_profit": quick_score,
        "swing": swing_score,
        "breakdowns": breakdowns,
        "paired_deltas_quick_minus_swing": {
            "strict_accuracy": accuracy_delta,
            "mean_net_r": net_delta,
        },
        "quick_profit_candidate_for_further_evaluation": quick_candidate,
        "higher_hit_rate_alone_is_never_a_pass": True,
        "entries_are_paired": True,
        "active_model_changed": False,
        "promotion_authorized": False,
    }
