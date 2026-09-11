"""Candidate filters (PLAN.md 6.7) applied after scanning, before the watchlist.

Reject when: average turnover too low, ATR% outside the band, on a surveillance (ASM/GSM)
list, price near a circuit limit, the regime forbids entries, or the net reward:risk to the
final target is below the minimum after costs. Results-in-window and sector/heat checks
happen elsewhere (setups and the portfolio). Each rejection carries a reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from tradedesk.config.models import ChargeSchedule, RiskConfig, UniverseConfig
from tradedesk.engine.signals import Signal
from tradedesk.models import TradeType
from tradedesk.risk.costs import net_reward_risk


@dataclass(frozen=True)
class FilterResult:
    ok: bool
    reasons: list[str]
    net_rr_t1: float | None = None
    net_rr_t2: float | None = None


def net_rr(sig: Signal, qty: int, schedule: ChargeSchedule) -> tuple[float | None, float | None]:
    if qty <= 0:
        return None, None
    common = dict(trade_type=TradeType.DELIVERY, qty=qty, entry=_d(sig.trigger), stop=_d(sig.stop))
    t1 = float(net_reward_risk(schedule, target=_d(sig.t1), **common))  # type: ignore[arg-type]
    t2 = float(net_reward_risk(schedule, target=_d(sig.t2), **common))  # type: ignore[arg-type]
    return t1, t2


def _d(x: float) -> Decimal:
    return Decimal(str(round(x, 2)))


def apply_filters(
    sig: Signal,
    *,
    qty: int,
    atr_pct: float | None,
    avg_turnover: float | None,
    regime: str | None,
    risk: RiskConfig,
    universe: UniverseConfig,
    surveillance: Mapping[str, str] | None = None,  # symbol -> list name (ASM/GSM)
    upper_circuit: float | None = None,
) -> FilterResult:
    reasons: list[str] = []
    if avg_turnover is not None and avg_turnover < float(universe.min_avg_daily_turnover_inr):
        reasons.append(f"turnover {avg_turnover / 1e7:.1f} cr < min")
    if atr_pct is not None:
        lo, hi = float(universe.atr_pct_band.min) * 100, float(universe.atr_pct_band.max) * 100
        if not lo <= atr_pct <= hi:
            reasons.append(f"ATR {atr_pct:.1f}% outside {lo:.1f}-{hi:.1f}%")
    if surveillance and sig.symbol in surveillance:
        reasons.append(f"on {surveillance[sig.symbol]} list")
    if upper_circuit is not None and sig.trigger >= upper_circuit * 0.995:
        reasons.append("trigger at the upper circuit")
    if regime == "risk_off":
        reasons.append("regime risk_off: no new swing entries")
    rr1, rr2 = net_rr(sig, qty, risk.costs)
    if rr2 is not None and rr2 < float(risk.min_net_rr):
        reasons.append(f"net R:R {rr2:.2f} < {risk.min_net_rr} to final target")
    return FilterResult(ok=not reasons, reasons=reasons, net_rr_t1=rr1, net_rr_t2=rr2)
