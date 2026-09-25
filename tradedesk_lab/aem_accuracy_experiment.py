"""Bounded causal information-quality experiment for the frozen AEM baseline."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from tradedesk_lab.aem_report import summarize_aem
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

MINIMUM_DEVELOPMENT_RESOLVED = 150
MINIMUM_DEVELOPMENT_ACTIVE_SESSIONS = 30
MINIMUM_HOLDOUT_RESOLVED = 100
MINIMUM_HOLDOUT_ACTIVE_SESSIONS = 20

# Frozen before looking at the chronological holdout. Every field exists at the
# recorded decision time; no outcome, later bar, or instrument identity is used.
SPECS: tuple[dict[str, Any], ...] = (
    {"id": "room_1p2", "conditions": (("intraday_resistance_distance", ">=", 0.012),)},
    {"id": "room_1p6", "conditions": (("intraday_resistance_distance", ">=", 0.016),)},
    {"id": "rvol_0p8", "conditions": (("intraday_tod_rvol", ">=", 0.8),)},
    {"id": "rvol_1p2", "conditions": (("intraday_tod_rvol", ">=", 1.2),)},
    {"id": "positive_vwap_slope", "conditions": (("intraday_vwap_slope", ">", 0.0),)},
    {"id": "above_vwap", "conditions": (("signal_minus_vwap", ">=", 0.0),)},
    {"id": "extension_le_1p0", "conditions": (("daily_extension_atr", "<=", 1.0),)},
    {"id": "extension_le_1p5", "conditions": (("daily_extension_atr", "<=", 1.5),)},
    {"id": "body_nonnegative", "conditions": (("intraday_body_ratio", ">=", 0.0),)},
    {"id": "body_0p3", "conditions": (("intraday_body_ratio", ">=", 0.3),)},
    {
        "id": "quality_bundle",
        "conditions": (
            ("intraday_resistance_distance", ">=", 0.012),
            ("intraday_tod_rvol", ">=", 0.8),
            ("intraday_vwap_slope", ">", 0.0),
            ("signal_minus_vwap", ">=", 0.0),
            ("daily_extension_atr", "<=", 1.5),
        ),
    },
)


def _hash_events(events: pd.DataFrame) -> str:
    return hashlib.sha256(
        pd.util.hash_pandas_object(events, index=True).values.tobytes()
    ).hexdigest()


def _condition_mask(frame: pd.DataFrame, conditions: tuple[tuple[str, str, float], ...]):
    mask = pd.Series(True, index=frame.index)
    for column, operator, threshold in conditions:
        values = pd.to_numeric(frame[column], errors="coerce")
        if operator == ">=":
            mask &= values >= threshold
        elif operator == ">":
            mask &= values > threshold
        elif operator == "<=":
            mask &= values <= threshold
        else:  # pragma: no cover - the frozen registry is module-owned
            raise ValueError(f"unsupported condition operator: {operator}")
    return mask


def _prepare(events: pd.DataFrame) -> pd.DataFrame:
    required = {
        "event_id",
        "session_date",
        "decision",
        "status",
        "strict_success",
        "target_hit",
        "net_pnl",
        "net_r",
        "gross_r",
        "daily_extension_atr",
        "daily_median_turnover_inr",
        "intraday_resistance_distance",
        "intraday_tod_rvol",
        "intraday_vwap",
        "intraday_vwap_slope",
        "intraday_signal_price",
        "intraday_body_ratio",
    }
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"AEM experiment is missing {sorted(missing)}")
    frame = events.copy()
    if frame.event_id.isna().any() or frame.event_id.duplicated().any():
        raise ValueError("AEM experiment event identifiers must be present and unique")
    frame["session_date"] = pd.to_datetime(frame.session_date).dt.date.astype(str)
    frame["signal_minus_vwap"] = pd.to_numeric(
        frame.intraday_signal_price, errors="coerce"
    ) - pd.to_numeric(frame.intraday_vwap, errors="coerce")
    return frame


def _metrics(frame: pd.DataFrame, dates: list[str]) -> dict[str, Any]:
    result = summarize_aem(frame, dates)
    overall = result["overall"]
    sessions = result["session_coverage"]
    return {
        "calls_issued": overall["trade_decisions"],
        "resolved_trades": overall["resolved_trades"],
        "unfilled_decisions": overall["unfilled_decisions"],
        "unresolved_decisions": overall["unresolved_decisions"],
        "strict_successes": overall["strict_successes"],
        "strict_success_rate": overall["strict_success_rate"],
        "wilson95_lower": overall["strict_success_wilson95"]["lower"],
        "mean_net_r": overall["mean_net_r"],
        "active_sessions": sessions["active_sessions"],
        "active_session_coverage": (
            sessions["active_sessions"] / sessions["evaluation_sessions"]
            if sessions["evaluation_sessions"]
            else None
        ),
        "zero_call_sessions": sessions["zero_trade_decision_sessions"],
        "sessions_at_least_70pct": sessions["active_sessions_at_least_70pct"],
        "sessions_at_least_80pct": sessions["active_sessions_at_least_80pct"],
    }


def _selected(frame: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    trades = frame.decision == "TRADE"
    return frame.loc[~trades | _condition_mask(frame, spec["conditions"])].copy()


def _failure_taxonomy(frame: pd.DataFrame) -> dict[str, Any]:
    losses = frame.loc[(frame.status == "resolved") & ~frame.strict_success.astype(bool)].copy()
    turnover_floor = float(pd.to_numeric(frame.daily_median_turnover_inr).median())
    flags = {
        "insufficient_remaining_room": pd.to_numeric(losses.intraday_resistance_distance) < 0.012,
        "extended_daily_move": pd.to_numeric(losses.daily_extension_atr) > 1.5,
        "weak_same_time_volume": pd.to_numeric(losses.intraday_tod_rvol) < 0.8,
        "weak_vwap_context": (pd.to_numeric(losses.intraday_vwap_slope) <= 0)
        | (pd.to_numeric(losses.intraday_signal_price) < pd.to_numeric(losses.intraday_vwap)),
        "weak_impulse_body": pd.to_numeric(losses.intraday_body_ratio) < 0.3,
        "lower_development_liquidity": pd.to_numeric(losses.daily_median_turnover_inr)
        < turnover_floor,
        "cost_drag": (pd.to_numeric(losses.gross_r) > 0) & (pd.to_numeric(losses.net_r) <= 0),
    }
    overlap = {name: int(mask.sum()) for name, mask in flags.items()}
    primary = {name: 0 for name in flags}
    primary["unclassified_by_registered_taxonomy"] = 0
    for index in losses.index:
        match = next((name for name, mask in flags.items() if bool(mask.loc[index])), None)
        primary[match or "unclassified_by_registered_taxonomy"] += 1
    return {
        "resolved_strict_failures": len(losses),
        "development_turnover_median_inr": turnover_floor,
        "overlapping_counts": overlap,
        "primary_counts": primary,
        "missing_context": [
            "intraday benchmark alignment",
            "intraday sector alignment",
            "historical point-in-time sector membership",
        ],
    }


def evaluate_accuracy_experiment(events: pd.DataFrame, evaluation_dates: list[str]) -> dict:
    """Select on the first 80 sessions and open the final 40 exactly once."""

    dates = sorted(dict.fromkeys(evaluation_dates))
    if len(dates) != 120:
        raise ValueError("information-quality v1 requires the frozen 120-session cohort")
    development_dates, holdout_dates = dates[:80], dates[80:]
    frame = _prepare(events)
    development = frame.loc[frame.session_date.isin(development_dates)]
    holdout = frame.loc[frame.session_date.isin(holdout_dates)]
    if not len(development) or not len(holdout):
        raise ValueError("both chronological partitions must contain events")

    baseline_development = _metrics(development, development_dates)
    trials = []
    for spec in SPECS:
        metrics = _metrics(_selected(development, spec), development_dates)
        eligible = (
            metrics["resolved_trades"] >= MINIMUM_DEVELOPMENT_RESOLVED
            and metrics["active_sessions"] >= MINIMUM_DEVELOPMENT_ACTIVE_SESSIONS
            and metrics["unresolved_decisions"] == 0
        )
        trials.append(
            {
                "id": spec["id"],
                "conditions": [list(condition) for condition in spec["conditions"]],
                "eligible_for_selection": eligible,
                "metrics": metrics,
            }
        )
    eligible = [trial for trial in trials if trial["eligible_for_selection"]]
    if not eligible:
        raise ValueError("no preregistered selector met development sample gates")
    winner = max(
        eligible,
        key=lambda trial: (
            trial["metrics"]["wilson95_lower"],
            trial["metrics"]["mean_net_r"],
            trial["metrics"]["active_session_coverage"],
        ),
    )
    winning_spec = next(spec for spec in SPECS if spec["id"] == winner["id"])
    baseline_holdout = _metrics(holdout, holdout_dates)
    challenger_holdout = _metrics(_selected(holdout, winning_spec), holdout_dates)
    deltas = {
        key: challenger_holdout[key] - baseline_holdout[key]
        for key in (
            "strict_success_rate",
            "wilson95_lower",
            "mean_net_r",
            "active_session_coverage",
            "sessions_at_least_70pct",
            "sessions_at_least_80pct",
        )
    }
    checks = {
        "minimum_holdout_resolved": challenger_holdout["resolved_trades"]
        >= MINIMUM_HOLDOUT_RESOLVED,
        "minimum_holdout_active_sessions": challenger_holdout["active_sessions"]
        >= MINIMUM_HOLDOUT_ACTIVE_SESSIONS,
        "no_unresolved_decisions": challenger_holdout["unresolved_decisions"] == 0,
        "strict_accuracy_improves_5pp": deltas["strict_success_rate"] >= 0.05,
        "wilson_lower_improves": deltas["wilson95_lower"] > 0,
        "positive_after_cost_mean_net_r": challenger_holdout["mean_net_r"] > 0,
        "at_least_half_session_coverage": challenger_holdout["active_session_coverage"] >= 0.5,
    }
    return {
        "version": "aem-information-quality-v1",
        "status": "historical_diagnostic_only",
        "eligible_for_live": False,
        "evidence_class": "chronological_holdout_from_previously_inspected_baseline",
        "split": {
            "development_sessions": len(development_dates),
            "development_from": development_dates[0],
            "development_through": development_dates[-1],
            "holdout_sessions": len(holdout_dates),
            "holdout_from": holdout_dates[0],
            "holdout_through": holdout_dates[-1],
            "holdout_opened_once": True,
        },
        "failure_taxonomy": _failure_taxonomy(development),
        "development_baseline": baseline_development,
        "development_trials": trials,
        "selected_specification": winner,
        "holdout_baseline": baseline_holdout,
        "holdout_challenger": challenger_holdout,
        "holdout_deltas": deltas,
        "promotion_checks": checks,
        "passed": all(checks.values()),
        "limitations": [
            "The chronological holdout is later but belongs to an already inspected baseline.",
            "This is development evidence, not fresh prospective or live evidence.",
            "Market and sector alignment remain unavailable in the frozen event artifact.",
            "A selector filters existing calls; it does not prove detection of "
            "omitted opportunities.",
        ],
    }


def run_accuracy_experiment(
    root: Path = ROOT, output: Path = OUTPUT, *, dataset_id: str | None = None
) -> dict:
    root, output = Path(root).resolve(), Path(output).resolve()
    if dataset_id is None:
        dataset_id = json.loads((output / "aem_staged/latest.json").read_text())["id"]
    folder = output / "aem_staged/datasets" / dataset_id
    manifest_path, events_path = folder / "manifest.json", folder / "events.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = pd.read_csv(events_path, float_precision="round_trip")
    if manifest.get("id") != dataset_id or _hash_events(events) != manifest["dataset_sha256"]:
        raise ValueError("frozen AEM dataset identity or event population changed")
    dates = [row["session_date"] for row in manifest["diagnostics"]["by_session"]]
    result = evaluate_accuracy_experiment(events, dates)
    run_id = uuid4().hex
    target = output / "aem_accuracy_experiments/runs" / run_id
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset_id,
        "source_manifest_sha256": digest(manifest_path),
        "source_events_csv_sha256": digest(events_path),
        "experiment_code_sha256": digest(Path(__file__)),
        **result,
    }
    write_json(target / "report.json", report)
    write_json(
        output / "aem_accuracy_experiments/latest.json",
        {"id": run_id, "dataset_id": dataset_id, "path": str(target / "report.json")},
    )
    return report
