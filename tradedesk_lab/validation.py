"""M14: label-aware splits; CPCV fold-to-path mapping; DSR and symmetric PBO."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb

import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata


@dataclass(frozen=True)
class Fold:
    train: np.ndarray
    test: np.ndarray
    groups: tuple[int, ...] = ()


def purge(
    df: pd.DataFrame,
    train: np.ndarray,
    test: np.ndarray,
    calendar: pd.DatetimeIndex,
    embargo: int = 10,
) -> np.ndarray:
    """Inclusive [feature time, label-end] overlap plus post-test session embargo.

    Adjacent test observations merge into intervals; gaps between disjoint test blocks
    are preserved. All symbols share the same date split and exchange calendar.
    """
    if not len(test):
        return train
    starts = pd.to_datetime(df["armed_on"]).to_numpy()
    ends = pd.to_datetime(df["label_end_date"]).to_numpy()
    intervals = sorted(zip(starts[test], ends[test], strict=True))
    merged: list[tuple[np.datetime64, np.datetime64]] = []
    for lo, hi in intervals:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    keep = np.ones(len(train), dtype=bool)
    for lo, hi in merged:
        idx = int(calendar.searchsorted(pd.Timestamp(hi), side="right")) + embargo - 1
        embargo_end = calendar[min(max(idx, 0), len(calendar) - 1)].to_datetime64()
        keep &= ~((starts[train] <= hi) & (ends[train] >= lo))
        if embargo:
            keep &= ~((starts[train] > hi) & (starts[train] <= embargo_end))
    return train[keep]


def walk_forward(
    df: pd.DataFrame, calendar: pd.DatetimeIndex, splits: int = 3, embargo: int = 10
) -> list[Fold]:
    dates = pd.to_datetime(df["armed_on"])
    unique = np.sort(dates.unique())
    if len(unique) < splits + 2:
        return []
    blocks = np.array_split(unique, splits + 1)
    result = []
    for block in blocks[1:]:
        test = np.flatnonzero(dates.isin(block))
        cutoff = calendar.searchsorted(pd.Timestamp(block[0])) - embargo
        if cutoff <= 0:
            continue
        train = np.flatnonzero(dates < calendar[cutoff])
        train = purge(df, train, test, calendar, embargo)
        if len(train) >= 30 and len(test):
            result.append(Fold(train, test))
    return result


def cpcv(
    df: pd.DataFrame, calendar: pd.DatetimeIndex, n: int = 6, k: int = 2, embargo: int = 10
) -> tuple[list[Fold], np.ndarray, np.ndarray]:
    """15 folds / 5 full paths for N=6,K=2. A split can contribute to two paths.

    path_map[group, path] names the fold supplying that group's OOS prediction.
    Paths reuse observations, but never reuse a group within the same path.
    """
    dates = pd.to_datetime(df["armed_on"])
    unique = np.sort(dates.unique())
    if not 1 <= k < n or len(unique) < n:
        raise ValueError("Need 1 <= k < n and at least n distinct dates")
    blocks = np.array_split(unique, n)
    group_ids = np.empty(len(df), dtype=int)
    for i, block in enumerate(blocks):
        group_ids[dates.isin(block)] = i
    path_map = np.full((n, comb(n - 1, k - 1)), -1, dtype=int)
    counts = np.zeros(n, dtype=int)
    folds = []
    for held in combinations(range(n), k):
        test = np.flatnonzero(np.isin(group_ids, held))
        train = np.flatnonzero(~np.isin(group_ids, held))
        train = purge(df, train, test, calendar, embargo)
        for group in held:
            path_map[group, counts[group]] = len(folds)
            counts[group] += 1
        folds.append(Fold(train, test, held))
    return folds, path_map, group_ids


def reconstruct(
    predictions: list[np.ndarray], path_map: np.ndarray, group_ids: np.ndarray
) -> np.ndarray:
    output = np.full((len(group_ids), path_map.shape[1]), np.nan)
    for group in range(path_map.shape[0]):
        rows = group_ids == group
        for path in range(path_map.shape[1]):
            output[rows, path] = predictions[path_map[group, path]][rows]
    return output


def deflated_sharpe(
    observed: float, trial_std: float, n_trials: int, n_obs: int, skew: float, kurtosis: float
) -> float | None:
    """Bailey/Lopez de Prado 2014: unannualized SR, Pearson (not excess) kurtosis.

    Raw attempted trial count is a conservative search correction; missing historical
    trials must be disclosed. This is not a posterior probability of future profits.
    """
    if n_obs < 3 or n_trials < 1 or trial_std < 0:
        return None
    reference = 0.0
    if n_trials > 1:
        gamma = 0.5772156649015329
        reference = trial_std * (
            (1 - gamma) * norm.ppf(1 - 1 / n_trials) + gamma * norm.ppf(1 - 1 / (n_trials * np.e))
        )
    variance = 1 - skew * observed + (kurtosis - 1) * observed**2 / 4
    if variance <= 0:
        return None
    return float(norm.cdf((observed - reference) * np.sqrt(n_obs - 1) / np.sqrt(variance)))


def probability_of_overfitting(returns: np.ndarray, partitions: int = 6) -> dict:
    """CSCV: aligned session x strategy returns, equal IS/OOS block counts.

    Median rank ties carry half weight. Three candidates give coarse rank resolution,
    so report candidate count and the full percentile distribution with PBO.
    """
    if returns.ndim != 2 or returns.shape[1] < 2 or len(returns) < partitions * 2:
        return {"pbo": None, "reason": "insufficient aligned candidate returns"}
    if partitions % 2:
        raise ValueError("CSCV needs an even number of partitions")
    if not np.isfinite(returns).all():
        raise ValueError("PBO returns must be finite")
    blocks = np.array_split(np.arange(len(returns)), partitions)
    percentiles = []
    for held in combinations(range(partitions), partitions // 2):
        a = np.concatenate([blocks[i] for i in held])
        b = np.concatenate([blocks[i] for i in range(partitions) if i not in held])

        def sharpes(rows: np.ndarray) -> np.ndarray:
            x = returns[rows]
            sd = x.std(axis=0, ddof=1)
            return np.divide(x.mean(axis=0), sd, out=np.zeros_like(sd), where=sd > 0)

        best = int(np.argmax(sharpes(a)))
        rank = rankdata(sharpes(b), method="average")[best]
        percentiles.append(float(rank / (returns.shape[1] + 1)))
    p = np.array(percentiles)
    return {
        "pbo": float(np.mean((p < 0.5) + 0.5 * (p == 0.5))),
        "n_candidates": returns.shape[1],
        "n_combinations": len(p),
        "oos_rank_percentiles": percentiles,
        "method": "CSCV, session-aligned net returns",
    }
