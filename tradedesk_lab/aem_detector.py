"""Deterministic anticipatory early-momentum candidate and trigger decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from tradedesk_lab.aem_contract import DEFAULT_AEM_CONTRACT, AemContract
from tradedesk_lab.aem_features import daily_snapshot, intraday_snapshot


@dataclass(frozen=True)
class AemCandidate:
    identifier: str
    scrip_code: str
    symbol: str
    armed_on: date
    resistance: float
    daily_atr: float
    features: dict
    contract_sha256: str


@dataclass(frozen=True)
class AemDecision:
    candidate_id: str
    decision: str
    available_at: str
    signal_price: float | None
    reasons: tuple[str, ...]
    features: dict
    contract_sha256: str


def detect_daily_candidate(
    scrip_code: str,
    symbol: str,
    daily: pd.DataFrame,
    on: date | pd.Timestamp,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
) -> AemCandidate | None:
    values = daily_snapshot(daily, on, contract)
    if not values["eligible"]:
        return None
    armed = pd.Timestamp(on).date()
    return AemCandidate(
        identifier=f"{contract.strategy_version}:{scrip_code}:{armed}",
        scrip_code=scrip_code,
        symbol=symbol,
        armed_on=armed,
        resistance=float(values["resistance"]),
        daily_atr=float(values["daily_atr"]),
        features=values,
        contract_sha256=contract.sha256,
    )


def evaluate_trigger(
    candidate: AemCandidate,
    intraday: pd.DataFrame,
    at: pd.Timestamp,
    contract: AemContract = DEFAULT_AEM_CONTRACT,
) -> AemDecision:
    if candidate.contract_sha256 != contract.sha256:
        raise ValueError("candidate and detector contracts differ")
    values = intraday_snapshot(
        intraday,
        at=at,
        resistance=candidate.resistance,
        contract=contract,
    )
    failed = tuple(name for name, passed in values["checks"].items() if not passed)
    if not values["checks"]["decision_window"]:
        decision = "NO_TRADE"
    elif values["eligible"]:
        decision = "TRADE"
    else:
        decision = "WATCH"
    return AemDecision(
        candidate_id=candidate.identifier,
        decision=decision,
        available_at=str(values["available_at"]),
        signal_price=float(values["signal_price"]) if decision == "TRADE" else None,
        reasons=("all_aem_conditions_pass",) if decision == "TRADE" else failed,
        features=values,
        contract_sha256=contract.sha256,
    )
