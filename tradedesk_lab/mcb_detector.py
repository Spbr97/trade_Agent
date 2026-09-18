"""Deterministic MCB candidate and trigger decisions; no model inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from tradedesk_lab.mcb_contract import DEFAULT_MCB_CONTRACT, McbContract
from tradedesk_lab.mcb_features import daily_snapshot, intraday_snapshot


@dataclass(frozen=True)
class McbCandidate:
    identifier: str
    scrip_code: str
    symbol: str
    armed_on: date
    breakout_level: float
    compression_low: float
    daily_atr: float
    features: dict
    contract_sha256: str


@dataclass(frozen=True)
class McbDecision:
    candidate_id: str
    decision: str
    available_at: str
    entry_trigger: float | None
    stop: float | None
    provisional_target: float | None
    reasons: tuple[str, ...]
    features: dict
    contract_sha256: str


def detect_daily_candidate(
    scrip_code: str,
    symbol: str,
    daily: pd.DataFrame,
    on: date | pd.Timestamp,
    contract: McbContract = DEFAULT_MCB_CONTRACT,
) -> McbCandidate | None:
    values = daily_snapshot(daily, on, contract)
    if not values["eligible"]:
        return None
    armed = pd.Timestamp(on).date()
    return McbCandidate(
        identifier=f"{contract.strategy_version}:{scrip_code}:{armed}",
        scrip_code=scrip_code,
        symbol=symbol,
        armed_on=armed,
        breakout_level=float(values["breakout_level"]),
        compression_low=float(values["compression_low"]),
        daily_atr=float(values["daily_atr"]),
        features=values,
        contract_sha256=contract.sha256,
    )


def evaluate_trigger(
    candidate: McbCandidate,
    intraday: pd.DataFrame,
    at: pd.Timestamp,
    contract: McbContract = DEFAULT_MCB_CONTRACT,
) -> McbDecision:
    if candidate.contract_sha256 != contract.sha256:
        raise ValueError("candidate and detector contracts differ")
    values = intraday_snapshot(
        intraday,
        at=at,
        breakout_level=candidate.breakout_level,
        daily_atr=candidate.daily_atr,
        contract=contract,
    )
    failed = tuple(name for name, passed in values["checks"].items() if not passed)
    if not values["checks"]["decision_window"]:
        decision = "NO_TRADE"
    elif values["eligible"]:
        decision = "TRADE"
    else:
        decision = "WATCH"
    trigger = float(values["trigger"])
    intraday_atr = max(float(values["intraday_atr"]), 1e-9)
    stop = max(
        candidate.breakout_level - contract.stop_atr * candidate.daily_atr,
        float(values["bar_low"]) - 0.10 * intraday_atr,
    )
    if stop >= trigger:
        stop = trigger - max(0.10 * intraday_atr, 0.01)
    target = trigger + contract.target_r * (trigger - stop)
    return McbDecision(
        candidate_id=candidate.identifier,
        decision=decision,
        available_at=str(values["available_at"]),
        entry_trigger=trigger if decision == "TRADE" else None,
        stop=stop if decision == "TRADE" else None,
        provisional_target=target if decision == "TRADE" else None,
        reasons=("all_mcb_conditions_pass",) if decision == "TRADE" else failed,
        features=values,
        contract_sha256=contract.sha256,
    )
