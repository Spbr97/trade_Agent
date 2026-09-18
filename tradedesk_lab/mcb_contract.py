"""Frozen research contract for Momentum Compression Breakout v1.

This contract is intentionally separate from production setup configuration. Changing a
field creates a new experiment contract; it never changes the live scanner implicitly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class McbContract:
    strategy_version: str = "MCB_v1"
    feature_version: str = "mcb-point-in-time-v1"
    label_version: str = "mcb-intraday-net-target-v1"
    daily_compression_sessions: int = 5
    daily_prior_momentum_sessions: int = 20
    daily_prior_momentum_min: float = 0.05
    daily_volume_contraction_max: float = 0.80
    daily_compression_atr_max: float = 2.50
    daily_breakout_proximity_max: float = 0.02
    daily_max_extension_atr: float = 2.50
    daily_liquidity_sessions: int = 20
    daily_min_median_turnover_inr: float = 50_000_000.0
    opening_range_bars: int = 3
    earliest_decision_time: str = "09:30"
    latest_decision_time: str = "11:00"
    rvol_min: float = 1.30
    bullish_body_ratio_min: float = 0.60
    remaining_atr_min: float = 0.30
    stop_atr: float = 0.50
    target_r: float = 2.0
    chased_atr: float = 0.50
    rvol_lookback_sessions: int = 20
    rvol_min_sessions: int = 5
    horizons_bars: tuple[tuple[str, int | None], ...] = (
        ("15m", 3),
        ("30m", 6),
        ("60m", 12),
        ("eod", None),
    )

    def __post_init__(self) -> None:
        if self.daily_compression_sessions < 3:
            raise ValueError("daily compression needs at least three sessions")
        if self.opening_range_bars < 1:
            raise ValueError("opening range must contain at least one completed bar")
        if self.daily_liquidity_sessions < 5 or self.daily_min_median_turnover_inr <= 0:
            raise ValueError("daily liquidity gate is invalid")
        if not 0 < self.target_r or not 0 < self.stop_atr:
            raise ValueError("target and stop distances must be positive")
        if not 0 <= self.remaining_atr_min <= 1:
            raise ValueError("remaining ATR threshold must be a fraction")
        if self.earliest_decision_time >= self.latest_decision_time:
            raise ValueError("decision window is invalid")

    def to_dict(self) -> dict:
        value = asdict(self)
        value["horizons_bars"] = [list(item) for item in self.horizons_bars]
        return value

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_MCB_CONTRACT = McbContract()
