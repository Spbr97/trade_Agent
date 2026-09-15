"""Calibration drift monitor (PLAN.md 10.5).

Among live signals scored near p, about p should succeed. Buckets are checked once they
hold enough resolved signals; a well-populated bucket that drifts beyond tolerance pauses
the prediction layer (never the setups).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

# Module-level default paths (not just function-default arguments) so callers - notably
# dashboard/app.py's /api/ml-calibration - can reference them by name and a test can
# monkeypatch the module attribute, the same pattern reliability_sources.py already uses for
# NSE_JOURNAL/CRYPTO_LOG. Mirrors the defaults `tradedesk ml check-drift` already uses.
SHADOW_LOG_PATH = Path("data/models/shadow.jsonl")
NSE_JOURNAL_PATH = Path("data/journal.sqlite")
# Day-by-day calibration trend (2026-09-15 request: "day by day and no stale data") - same
# append-once-per-day JSONL pattern as reliability.py's agent_reliability_history.jsonl, so
# "is the model's calibration actually improving" is a real trend to look at, not just
# today's snapshot re-computed fresh on every dashboard load (which is already always fresh -
# see log_calibration_snapshot's idempotent-per-day guard for why re-running it is safe).
CALIBRATION_HISTORY_PATH = Path("data/reports/ml_calibration_history.jsonl")


@dataclass(frozen=True)
class DriftReport:
    buckets: list[tuple[float, float, float, int]]  # (lo, mean predicted, realised, n)
    paused: bool
    reason: str


def drift_check(
    predictions: pd.DataFrame,
    outcomes: dict[str, int],
    *,
    min_bucket: int = 30,
    tolerance: float = 0.15,
) -> DriftReport:
    """`predictions`: shadow log rows; `outcomes`: signal id -> label (1/0) once known."""
    if predictions.empty:
        return DriftReport([], False, "no predictions")
    df = predictions[predictions["signal_id"].isin(outcomes)].copy()
    if df.empty:
        return DriftReport([], False, "no resolved predictions")
    df["y"] = df["signal_id"].map(outcomes).astype(int)
    edges = [0.0, 0.3, 0.45, 0.6, 0.75, 1.01]
    buckets: list[tuple[float, float, float, int]] = []
    paused = False
    reason = "calibration within tolerance"
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (df["p"] >= lo) & (df["p"] < hi)
        n = int(m.sum())
        if n == 0:
            continue
        mp, rr = float(df.loc[m, "p"].mean()), float(df.loc[m, "y"].mean())
        buckets.append((lo, mp, rr, n))
        if n >= min_bucket and abs(mp - rr) > tolerance:
            paused = True
            reason = f"bucket {lo:.2f}+: predicted {mp:.2f}, realised {rr:.2f} over {n} signals"
    return DriftReport(buckets, paused, reason)


def check_and_flag_drift(
    shadow_log: Path,
    outcomes: dict[str, int],
    *,
    review_path: Path | None = None,
    min_bucket: int = 30,
    tolerance: float = 0.15,
) -> DriftReport:
    """Closes a real gap: drift_check() above is a pure function nothing in production ever
    calls - it gets computed and the result goes nowhere. Mirrors signal_tracker.py's
    flag_setup_failures() exactly (rule-based, no LLM, one review_queue item per issue,
    deduped by title so re-running daily doesn't spam the queue) rather than auto-flipping
    config/ml.yaml - a human decides whether to actually pause the model, same as every
    other self-analysis surface in this project (CLAUDE.md: Claude/automation output is
    advisory, it never changes a live config value on its own).

    `outcomes` is signal_id -> resolved 1/0 label, supplied by the caller (e.g. from the
    paper book/journal for NSE) - this does not invent a second outcome-resolution
    mechanism; it only wires the existing drift_check() to the existing review queue.
    `review_path` overrides review_queue.QUEUE's default - tests must pass a tmp_path
    (production data/reviews/queue.jsonl must never be touched by pytest, per the
    claude/weekly_review.py lesson documented in signal_tracker.py)."""
    from tradedesk.prediction.predict import read_shadow
    from tradedesk.review_queue import QUEUE, add_item, load_queue

    path = review_path or QUEUE
    predictions = read_shadow(shadow_log)
    report = drift_check(predictions, outcomes, min_bucket=min_bucket, tolerance=tolerance)
    if report.paused:
        title = "ML prediction layer calibration drift"
        existing_titles = {i.title for i in load_queue(path).values()}
        if title not in existing_titles:
            add_item(
                "ml",
                title,
                report.reason,
                "review config/ml.yaml: consider setting enabled=false or extending shadow "
                "mode until recalibrated",
                path=path,
            )
    return report


# ------------------------------------------------------------- day-by-day history


def report_as_dict(report: DriftReport) -> dict[str, Any]:
    return {
        "paused": report.paused,
        "reason": report.reason,
        "buckets": [
            {"lo": lo, "predicted": mp, "realised": rr, "n": n} for lo, mp, rr, n in report.buckets
        ],
    }


def append_calibration_history(record: dict[str, Any], path: Path = CALIBRATION_HISTORY_PATH) -> None:  # noqa: E501
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": date.today().isoformat(), **record}, default=str) + "\n")


def load_calibration_history(path: Path = CALIBRATION_HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]  # noqa: E501


def log_daily_calibration_snapshot(
    *,
    shadow_log: Path = SHADOW_LOG_PATH,
    journal_path: Path = NSE_JOURNAL_PATH,
    history_path: Path = CALIBRATION_HISTORY_PATH,
) -> dict[str, Any] | None:
    """Append one calibration snapshot IF one hasn't already been logged today - idempotent
    regardless of how many times this gets called, so it's safe to call from a scheduled job
    without a separate lock (mirrors reliability_sources.py::log_daily_reliability_snapshot).
    Returns the logged record, or None if today's snapshot already existed."""
    from tradedesk.prediction.predict import read_shadow

    today = date.today().isoformat()
    if any(row.get("date") == today for row in load_calibration_history(history_path)):
        return None
    predictions = read_shadow(shadow_log)
    outcomes: dict[str, int] = {}
    if journal_path.exists():
        from tradedesk.journal import Journal

        with Journal(journal_path) as jn:
            outcomes = {
                row["signal_id"]: (1 if row["r_multiple"] > 0 else 0)
                for row in jn.trades(source="paper")
            }
    report = drift_check(predictions, outcomes)
    record = {
        "n_logged": int(len(predictions)),
        "n_resolved_checked": sum(n for _, _, _, n in report.buckets),
        **report_as_dict(report),
    }
    append_calibration_history(record, history_path)
    return record
