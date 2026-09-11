"""Calibration drift monitor (PLAN.md 10.5).

Among live signals scored near p, about p should succeed. Buckets are checked once they
hold enough resolved signals; a well-populated bucket that drifts beyond tolerance pauses
the prediction layer (never the setups).
"""

from __future__ import annotations

from dataclasses import dataclass

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
