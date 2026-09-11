"""Scoring and grading (PLAN.md 6.8).

Score 0-100 from: trend quality, RS rank, sector strength, pattern quality, room to
overhead supply, net reward:risk after costs, regime, and the setup's own track record.
Grades: A >= 80 (alerts with sound/Telegram), B 65-79 (dashboard), C < 65 (logged, still
paper-traded). The prediction layer (M11) can only lower a grade; a benched setup never
alerts. Every component is returned so the trade card can explain the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tradedesk.engine.signals import Signal


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class TrackRecord:
    """Rolling paper-book stats for a setup (M9 fills this; backtest results seed it)."""

    trades: int = 0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    benched: bool = False


@dataclass
class ScoreInputs:
    signal: Signal
    trend_strength: float  # 0-1: e.g. ADX and EMA alignment
    rs_percentile: float | None
    sector_percentile: float | None
    pattern_quality: float  # 0-1 from the detector's geometry
    room_r: float | None  # distance to overhead supply in R; None = blue sky
    net_rr_t2: float | None  # net reward:risk to the final target after costs
    regime: str | None
    track: TrackRecord = TrackRecord()


@dataclass
class Score:
    total: int
    grade: Grade
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    benched: bool = False

    @property
    def alertable(self) -> bool:
        return not self.benched and self.grade in (Grade.A, Grade.B)


WEIGHTS: dict[str, float] = {
    "trend": 15,
    "rs": 15,
    "sector": 10,
    "pattern": 20,
    "room": 10,
    "net_rr": 15,
    "regime": 10,
    "track": 5,
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_signal(x: ScoreInputs) -> Score:
    c: dict[str, float] = {}
    notes: list[str] = []

    c["trend"] = _clamp(x.trend_strength)
    c["rs"] = _clamp((x.rs_percentile or 50.0) / 100.0)
    c["sector"] = _clamp((x.sector_percentile if x.sector_percentile is not None else 50.0) / 100.0)
    c["pattern"] = _clamp(x.pattern_quality)
    # Room: full marks when the first overhead level is >= 3R away (or blue sky).
    c["room"] = 1.0 if x.room_r is None else _clamp(x.room_r / 3.0)
    if x.room_r is not None and x.room_r < 2.0:
        notes.append(f"overhead supply only {x.room_r:.1f}R away")
    # Net R:R: 2R after costs is the floor (score 0.5), 4R+ is full marks.
    if x.net_rr_t2 is None:
        c["net_rr"] = 0.5
    else:
        c["net_rr"] = _clamp((x.net_rr_t2 - 1.0) / 3.0)
        if x.net_rr_t2 < 2.0:
            notes.append(f"net R:R {x.net_rr_t2:.2f} < 2")
    c["regime"] = {"risk_on": 1.0, "neutral": 0.5, "risk_off": 0.0}.get(x.regime or "", 0.5)
    # Track record: neutral until 30 trades; then scaled on expectancy in [-0.5R, +0.5R].
    if x.track.trades < 30:
        c["track"] = 0.5
    else:
        c["track"] = _clamp(0.5 + x.track.expectancy_r)
        notes.append(f"last {x.track.trades} paper trades: {x.track.expectancy_r:+.2f}R")

    total = round(sum(WEIGHTS[k] * v for k, v in c.items()))
    grade = Grade.A if total >= 80 else Grade.B if total >= 65 else Grade.C
    if x.regime == "neutral" and grade is Grade.B:
        notes.append("neutral regime: A-grade only")
    return Score(
        total=total,
        grade=grade,
        components={k: round(v, 3) for k, v in c.items()},
        notes=notes,
        benched=x.track.benched,
    )


# ------------------------------------------------ helpers to derive inputs


def trend_strength(last: Any) -> float:
    """0-1 from ADX (25 -> 0.5, 40+ -> 1) and EMA stacking/slopes."""
    adx = float(last.get("adx14", 0.0) or 0.0)
    adx_part = _clamp((adx - 10.0) / 30.0)
    stack = float(last["ema20"] > last["ema50"] > last["ema200"]) if "ema200" in last else 0.5
    slopes = sum(float(last.get(f"ema{n}_slope", 0.0) > 0) for n in (20, 50)) / 2
    return round(0.5 * adx_part + 0.25 * stack + 0.25 * slopes, 3)


def pattern_quality(sig: Signal) -> float:
    """0-1 from the geometry the setup attached to the signal."""
    g = sig.geometry
    if "base" in g:
        b = g["base"]
        tight = _clamp(1.0 - b["depth_pct"] / 0.15)  # 0% deep -> 1, 15% -> 0
        contraction = _clamp(1.5 - b["contraction_ratio"])  # ratio 0.5 -> 1, 1.5 -> 0
        dryup = _clamp(1.5 - b["volume_dryup"])
        return round(0.4 * tight + 0.3 * contraction + 0.3 * dryup, 3)
    if "pullback" in g:
        p = g["pullback"]
        touch = _clamp(1.0 - abs(p["distance_to_ema_pct"]) / 0.02)
        vol = _clamp(1.5 - p["volume_ratio"])
        return round(0.5 * touch + 0.5 * vol, 3)
    if "squeeze" in g:
        s = g["squeeze"]
        near = _clamp(1.0 - s["pct_from_high20"] / 0.05)
        both = 1.0 if (s["nr7"] and s["inside_day"]) else 0.7
        return round(0.6 * near + 0.4 * both, 3)
    return 0.5


def room_in_r(sig: Signal) -> float | None:
    o = sig.geometry.get("overhead")
    if not o or o.get("nearest_level") is None:
        return None
    return (float(o["nearest_level"]) - sig.trigger) / sig.risk_per_share
