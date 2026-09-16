"""M16: display-only projections; preserve levels and all rejection reasons."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    TRADE = "TRADE"
    WATCHLIST = "WATCHLIST"
    NO_TRADE = "NO_TRADE"
    HOLD = "HOLD"
    REDUCE_RISK = "REDUCE_RISK"
    EXIT_INVALIDATE = "EXIT_INVALIDATE"


@dataclass(frozen=True)
class Decision:
    kind: Kind
    signal_id: str
    instrument: str
    setup: str
    as_of: str
    entry: float | None
    stop: float | None
    target: float | None
    position_size: float | None
    reasons_for: tuple[str, ...]
    reasons_against: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    model_probability: float | None = None
    confidence: str = "unproven"
    policy_version: str = "existing-policy-display-v1"
    direction: str = "long"
    market_regime: str | None = None
    sector_regime: str | None = None
    expected_holding_sessions: int | None = None
    max_holding_sessions: int | None = None
    model_agreement: dict[str, Any] | None = None
    expected_return_pct: float | None = None
    expected_net_return_pct: float | None = None
    reward_risk: float | None = None
    estimated_slippage_pct: float | None = None
    estimated_costs_pct: float | None = None
    risk_per_trade_pct: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        reasons = "; ".join(self.reasons_against or self.reasons_for)
        return f"{self.instrument}: {self.kind.value} — {reasons}"


def from_entry(
    entry: dict[str, Any],
    as_of: str,
    *,
    confirmed: bool = False,
    health_ok: bool = False,
    risk_ok: bool = False,
) -> Decision:
    sig = entry["signal"]
    reasons = tuple(entry.get("rejected_for", []))
    eligible = entry.get("alertable", False) and not reasons and entry.get("qty", 0) > 0
    kind = Kind.NO_TRADE
    if eligible:
        kind = Kind.TRADE if confirmed and health_ok and risk_ok else Kind.WATCHLIST
    if not eligible and not reasons:
        reasons = ("Existing entry is not alertable or has zero size",)
    return Decision(
        kind,
        sig["id"],
        sig["symbol"],
        sig["setup"],
        as_of,
        sig["trigger"],
        sig["stop"],
        sig["t1"],
        entry.get("qty"),
        tuple(sig.get("reasons", [])),
        reasons,
        ("Existing stop, expiry, gap and results rules continue to apply",),
        entry.get("probability"),
        market_regime=sig.get("regime"),
        sector_regime=entry.get("sector_regime"),
        expected_holding_sessions=entry.get("expected_holding_sessions"),
        max_holding_sessions=sig.get("exit_plan", {}).get("max_hold_sessions"),
        model_agreement=entry.get("model_agreement"),
        expected_return_pct=entry.get("expected_return_pct"),
        expected_net_return_pct=entry.get("expected_net_return_pct"),
        reward_risk=entry.get("net_rr_t1"),
        estimated_slippage_pct=entry.get("estimated_slippage_pct"),
        estimated_costs_pct=(
            entry["costs_round_trip"] / entry["position_value"]
            if entry.get("position_value", 0) > 0 and entry.get("costs_round_trip") is not None
            else None
        ),
        risk_per_trade_pct=entry.get("risk_pct"),
    )


def from_position(
    position: dict[str, Any],
    as_of: str,
    *,
    exit_reasons: tuple[str, ...] = (),
    reduce_reasons: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> Decision:
    """Exit/reduction events must come from an existing rule. Near-stop is a warning."""
    sig = position["signal"]
    kind = (
        Kind.EXIT_INVALIDATE if exit_reasons else Kind.REDUCE_RISK if reduce_reasons else Kind.HOLD
    )
    return Decision(
        kind,
        sig["id"],
        sig["symbol"],
        sig["setup"],
        as_of,
        position["entry_price"],
        position["stop"],
        sig["t1"],
        position["qty_open"],
        (),
        (*exit_reasons, *reduce_reasons, *warnings),
        (),
        market_regime=sig.get("regime"),
        max_holding_sessions=sig.get("exit_plan", {}).get("max_hold_sessions"),
    )
