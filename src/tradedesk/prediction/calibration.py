"""Calibration drift monitor (PLAN.md 10.5).

Among live signals scored near p, about p should succeed. Buckets are checked once they
hold enough resolved signals; a well-populated bucket that drifts beyond tolerance pauses
the prediction layer (never the setups).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


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
