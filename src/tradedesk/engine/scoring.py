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


BENCH_WINDOW = 30
"""Resolved paper trades needed before the track-record component means anything. Lives here
rather than in journal/stats.py because engine/ must not import from journal/ - stats.py
already imports TrackRecord from this module, so it re-exports this for its own use."""


@dataclass(frozen=True)
class EligibilityPolicy:
    """The evidence a setup must show before it may alert at all (SDD sections 18 and 23).

    Defaults are the SDD's recommended initial configuration. They are deliberately strict:
    on the numbers measured 2026-09-13 no setup in this project clears them, and the correct
    output is then NO TRADE rather than a lowered bar - SDD section 25, "reduce or stop
    signals rather than lowering the standards just to produce trades". Loosening any of
    these is a visible edit to config/setups.yaml, never a silent drift.

    `must_beat_random_by_r` is not in the SDD and is the stronger test: a win rate alone
    cannot distinguish a real edge from one that merely tracks a rising market. The live
    setups were measured 0.21-0.26R WORSE than random entry timing on the same stocks and
    window while still posting a plausible-looking win rate, so beating a matched random
    baseline is the criterion that actually catches that failure."""

    min_score: int = 85
    min_trades: int = 500
    min_oos_trades: int = 100
    min_win_rate: float = 0.80
    min_expectancy_r: float = 0.0
    must_beat_random_by_r: float = 0.10


DEFAULT_POLICY = EligibilityPolicy()


def eligibility(
    *,
    trades: int,
    oos_trades: int,
    win_rate: float,
    expectancy_r: float,
    random_baseline_r: float | None,
    policy: EligibilityPolicy = DEFAULT_POLICY,
) -> tuple[bool, tuple[str, ...]]:
    """Does this setup have enough PROVEN evidence to be allowed to alert?

    Fails closed on purpose: every unknown is a reason, and a setup nothing has measured is
    ineligible rather than implicitly fine (SDD sections 18 and 23 - the default output of
    the whole system is NO TRADE). Returns (eligible, reasons) so a caller can render
    exactly why something is not being traded.

    Pure policy, no data source: the journal supplies paper-book numbers and the backtest
    harness supplies the random baseline, but the RULE lives here beside the policy it
    applies, so every caller gates identically."""
    reasons: list[str] = []
    if trades < policy.min_trades:
        reasons.append(f"only {trades} resolved trades, need {policy.min_trades}")
    if oos_trades < policy.min_oos_trades:
        reasons.append(f"only {oos_trades} out-of-sample trades, need {policy.min_oos_trades}")
    if win_rate < policy.min_win_rate:
        reasons.append(f"win rate {win_rate:.1%} < required {policy.min_win_rate:.1%}")
    if expectancy_r < policy.min_expectancy_r:
        reasons.append(
            f"expectancy {expectancy_r:+.3f}R < required {policy.min_expectancy_r:+.3f}R"
        )
    if random_baseline_r is None:
        reasons.append("never measured against a random-timing baseline")
    elif expectancy_r - random_baseline_r < policy.must_beat_random_by_r:
        reasons.append(
            f"beats random by only {expectancy_r - random_baseline_r:+.3f}R, "
            f"need {policy.must_beat_random_by_r:+.3f}R"
        )
    return (not reasons), tuple(reasons)


@dataclass(frozen=True)
class TrackRecord:
    """Rolling paper-book stats for a setup (M9 fills this; backtest results seed it).

    `eligible` defaults to FALSE: a setup with no evidence must not be tradeable. Before
    2026-09-13 the only gate was `benched`, which required >= 30 resolved paper trades
    before it could ever fire - so with an empty paper book every setup was alertable on
    zero evidence, the same fail-open shape as the VIX-missing-reads-calm and
    `enabled: false`-never-honored bugs already recorded in CLAUDE.md."""

    trades: int = 0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    benched: bool = False
    oos_trades: int = 0
    random_baseline_r: float | None = None
    eligible: bool = False
    ineligibility_reasons: tuple[str, ...] = ()


def no_evidence(policy: EligibilityPolicy = DEFAULT_POLICY) -> TrackRecord:
    """The track record for a setup nothing has ever measured: ineligible WITH REASONS
    rather than a bare default, so a signal that cannot be traded says why."""
    ok, reasons = eligibility(
        trades=0, oos_trades=0, win_rate=0.0, expectancy_r=0.0,
        random_baseline_r=None, policy=policy,
    )  # fmt: skip
    return TrackRecord(eligible=ok, ineligibility_reasons=reasons)


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
    eligible: bool = False
    ineligibility_reasons: tuple[str, ...] = ()
    min_score: int = DEFAULT_POLICY.min_score

    @property
    def alertable(self) -> bool:
        """NO TRADE is the default answer (SDD section 1). A signal only alerts when the
        setup has PROVEN itself eligible, is not benched, and clears the score floor - the
        grade alone is no longer sufficient, because a high score computed from no track
        record is not evidence of anything."""
        return (
            self.eligible
            and not self.benched
            and self.grade in (Grade.A, Grade.B)
            and self.total >= self.min_score
        )

    @property
    def no_trade_reasons(self) -> tuple[str, ...]:
        """Why this is NO TRADE, for the trade card (SDD section 17: a NO-TRADE output with
        its reasons is a successful outcome, not a failure)."""
        if self.alertable:
            return ()
        out = list(self.ineligibility_reasons)
        if self.benched:
            out.append("setup benched (negative rolling paper expectancy)")
        if self.grade is Grade.C:
            out.append(f"grade C (score {self.total})")
        elif self.total < self.min_score:
            out.append(f"score {self.total} < required {self.min_score}")
        return tuple(out)


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


def score_signal(x: ScoreInputs, policy: EligibilityPolicy = DEFAULT_POLICY) -> Score:
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
    # Track record: ZERO until there are enough resolved trades to mean anything, then
    # scaled on expectancy in [-0.5R, +0.5R]. This used to award a free neutral 0.5 below
    # 30 trades, which let a setup with no evidence at all score as if it were average -
    # the scoring half of the same fail-open TrackRecord.eligible closes.
    if x.track.trades < BENCH_WINDOW:
        c["track"] = 0.0
        notes.append(f"no track record yet ({x.track.trades} resolved trades)")
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
        eligible=x.track.eligible,
        ineligibility_reasons=x.track.ineligibility_reasons,
        min_score=policy.min_score,
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
