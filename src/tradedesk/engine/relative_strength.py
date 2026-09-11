"""Relative strength and sector strength (PLAN.md 6.2).

RS score = weighted sum of the stock's 1/3/6-month returns *relative to the benchmark*,
ranked as a percentile (0-100) across the universe on each date. Sector strength is the
same idea for sector indices. Everything is computed row-wise on wide frames
(index = date, columns = scrip codes) so a whole universe is ranked in one pass and the
value on any date uses only closes up to that date.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from tradedesk.config.models import RelativeStrengthConfig


def relative_returns(
    closes: pd.DataFrame, benchmark: pd.Series, lookbacks: Sequence[int]
) -> dict[int, pd.DataFrame]:
    """Per lookback: stock return minus benchmark return over the last n sessions."""
    bench = benchmark.reindex(closes.index)
    out: dict[int, pd.DataFrame] = {}
    for n in lookbacks:
        stock_ret = closes / closes.shift(n) - 1
        bench_ret = bench / bench.shift(n) - 1
        out[n] = stock_ret.sub(bench_ret, axis=0)
    return out


def rs_score(
    closes: pd.DataFrame, benchmark: pd.Series, cfg: RelativeStrengthConfig
) -> pd.DataFrame:
    """Weighted relative return (a fraction, e.g. 0.12 = 12 points ahead of the benchmark)."""
    rel = relative_returns(closes, benchmark, cfg.lookbacks_sessions)
    score = None
    for n, w in zip(cfg.lookbacks_sessions, cfg.weights, strict=True):
        term = rel[n] * float(w)
        score = term if score is None else score + term
    assert score is not None
    return score


def percentile_rank(frame: pd.DataFrame) -> pd.DataFrame:
    """Row-wise percentile rank 0-100 across columns (NaN-aware; the best stock scores 100)."""
    return frame.rank(axis=1, pct=True, na_option="keep") * 100


def rs_rank(
    closes: pd.DataFrame, benchmark: pd.Series, cfg: RelativeStrengthConfig
) -> pd.DataFrame:
    return percentile_rank(rs_score(closes, benchmark, cfg))


def rs_line_new_high(
    closes: pd.DataFrame, benchmark: pd.Series, lookback: int = 63
) -> pd.DataFrame:
    """True where the stock/benchmark ratio is at its highest of the last `lookback` sessions."""
    ratio = closes.div(benchmark.reindex(closes.index), axis=0)
    return ratio >= ratio.rolling(lookback, min_periods=lookback).max()


def sector_strength(
    sector_closes: pd.DataFrame, benchmark: pd.Series, cfg: RelativeStrengthConfig
) -> pd.DataFrame:
    """Percentile rank of each sector index's RS score (columns = sector index codes)."""
    return rs_rank(sector_closes, benchmark, cfg)


def stronger_half(sector_rank_row: pd.Series) -> set[str]:
    """Sectors at or above the median rank on a given date."""
    valid = sector_rank_row.dropna()
    if valid.empty:
        return set()
    return set(valid[valid >= valid.median()].index)
