"""Calibration, selective-call accuracy, economic outcomes and uncertainty."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from tradedesk.reliability import wilson_lower_bound


def evaluate(df: pd.DataFrame, p: np.ndarray, selected: np.ndarray | None = None) -> dict:
    valid = np.isfinite(p)
    mask = valid if selected is None else valid & selected
    y = df.label.to_numpy(dtype=int)
    n = int(mask.sum())
    net = df.net_r.to_numpy(dtype=float)
    known = mask & np.isfinite(net)
    wins = int(y[mask].sum())
    calibration = []
    for lo in np.arange(0, 1, 0.1):
        m = valid & (p >= lo) & (p <= 1 if lo > 0.89 else p < lo + 0.1)
        if m.any():
            calibration.append(
                {"predicted": float(p[m].mean()), "actual": float(y[m].mean()), "n": int(m.sum())}
            )
    return {
        "n": n,
        "precision": float(wins / n) if n else None,
        "wilson_lower": float(wilson_lower_bound(wins, n)) if n else None,
        "coverage": n / max(int(valid.sum()), 1),
        "resolved_net": int(known.sum()),
        "net_r": float(net[known].mean()) if known.any() else None,
        "net_win_rate": float((net[known] > 0).mean()) if known.any() else None,
        "brier": float(brier_score_loss(y[valid], p[valid])) if valid.any() else None,
        "auc": float(roc_auc_score(y[valid], p[valid]))
        if valid.any() and len(np.unique(y[valid])) == 2
        else None,
        "calibration": calibration,
    }


def select_threshold(
    df: pd.DataFrame, p: np.ndarray, minimum: int = 100
) -> tuple[float, list[dict]]:
    """Precision-oriented preregistered grid; abstain when no positive-net cohort qualifies."""
    grid = []
    for threshold in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        m = evaluate(df, p, p >= threshold)
        grid.append(
            {
                "threshold": threshold,
                **m,
                "eligible": m["resolved_net"] >= minimum and (m["net_r"] or -1) > 0,
            }
        )
    eligible = [m for m in grid if m["eligible"]]
    if not eligible:
        return 1.01, grid
    best = max(eligible, key=lambda m: (m["wilson_lower"], m["net_r"]))
    return float(best["threshold"]), grid


def paired_bootstrap(df: pd.DataFrame, mask: np.ndarray, seed: int = 14) -> dict:
    """Date-block bootstrap: same-day signals are not independent observations."""
    finite = np.isfinite(df.net_r.to_numpy(dtype=float))
    z = df.loc[finite, ["armed_on", "net_r"]].copy()
    z["selected"] = mask[finite]
    z["a"] = z.net_r.where(z.selected, 0)
    agg = z.groupby("armed_on").agg(
        a=("a", "sum"), n=("selected", "sum"), b=("net_r", "sum"), m=("net_r", "size")
    )
    if mask[finite].sum() < 20 or len(agg) < 20:
        return {"net_r_lift_ci95": None, "reason": "fewer than 20 selected outcomes or dates"}
    data = agg.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    lifts = []
    # Resample contiguous ten-session blocks to retain some serial dependence.
    for _ in range(500):
        starts = rng.integers(0, len(data), size=(len(data) + 9) // 10)
        idx = np.concatenate([(np.arange(10) + s) % len(data) for s in starts])[: len(data)]
        a, n, b, m = data[idx].sum(axis=0)
        if n:
            lifts.append(a / n - b / m)
    return {
        "net_r_lift_ci95": np.quantile(lifts, [0.025, 0.975]).tolist(),
        "method": "500 paired circular ten-session block bootstrap samples",
    }
