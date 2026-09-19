"""Frozen contract for anticipatory early-momentum research.

AEM is intentionally separate from MCB: it seeks a short move toward nearby resistance
before a formal breakout, then exits quickly. It is research-only and cannot alter the
production scanner.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AemContract:
    strategy_version: str = "AEM_v1"
    feature_version: str = "aem-point-in-time-v1"
    label_version: str = "aem-quick-net-target-v1"
    minimum_daily_sessions: int = 60
    resistance_lookback_sessions: int = 10
    maximum_resistance_distance: float = 0.06
    maximum_extension_atr: float = 2.50
    liquidity_sessions: int = 20
    minimum_median_turnover_inr: float = 50_000_000.0
    opening_range_bars: int = 5
    earliest_decision_time: str = "09:20"
    latest_decision_time: str = "11:00"
    rvol_min: float = 1.20
    bullish_body_ratio_min: float = 0.40
    target_pct: float = 0.008
    stop_pct: float = 0.006
    max_hold_minutes: int = 90
    max_chase_pct: float = 0.004
    impulse_lookback_bars: int = 3
    vwap_hold_tolerance: float = 0.002
    momentum_entry_resistance_distance: float = 0.015
    pullback_market_distance_from_vwap: float = 0.0025
    pullback_limit_wait_minutes: int = 15
    resistance_tolerance: float = 0.002
    rvol_lookback_sessions: int = 20
    rvol_min_sessions: int = 5

    def __post_init__(self) -> None:
        if self.minimum_daily_sessions < 50:
            raise ValueError("daily history floor is too short")
        if self.resistance_lookback_sessions < 5:
            raise ValueError("resistance lookback is too short")
        if not 0 < self.target_pct < self.maximum_resistance_distance:
            raise ValueError("quick target must fit inside the anticipation zone")
        if not 0 < self.stop_pct < 0.05 or self.max_hold_minutes < 1:
            raise ValueError("exit geometry is invalid")
        if self.impulse_lookback_bars < 2:
            raise ValueError("impulse lookback is too short")
        if not 0 <= self.vwap_hold_tolerance <= 0.01:
            raise ValueError("VWAP hold tolerance is invalid")
        if not 0 < self.momentum_entry_resistance_distance < self.maximum_resistance_distance:
            raise ValueError("momentum entry boundary is invalid")
        if not 0 < self.pullback_market_distance_from_vwap < 0.02:
            raise ValueError("pullback market distance is invalid")
        if self.pullback_limit_wait_minutes < 1:
            raise ValueError("pullback limit wait is invalid")
        if self.earliest_decision_time >= self.latest_decision_time:
            raise ValueError("decision window is invalid")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_AEM_CONTRACT = AemContract()
