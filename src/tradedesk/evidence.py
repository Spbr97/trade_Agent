"""Frozen evidence categories and outcome contracts for self-learning calls.

Recommendation authority and outcome lifecycle are deliberately separate axes.  A shadow
call can be pending and later resolved; calling both concepts one ``status`` made it too
easy to hide failed research calls or count pending rows as evidence.

The contract registry is descriptive in Milestone 1.  Existing resolution behavior is not
changed here.  Rows created before this registry stay explicitly ``legacy`` rather than
being retroactively claimed by a contract that did not exist when they were logged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class EvidenceClass(StrEnum):
    """What authority the prediction had when it was created."""

    QUALIFIED_CALL = "qualified_call"
    SHADOW_CALL = "shadow_call"
    REJECTED_CALL = "rejected_call"


class OutcomeState(StrEnum):
    """Where the prediction is in its independently tracked outcome lifecycle."""

    PENDING_CALL = "pending_call"
    RESOLVED_CALL = "resolved_call"
    INVALID_CALL = "invalid_call"
    NEVER_TRIGGERED = "never_triggered"


class ContractKind(StrEnum):
    LEGACY = "legacy"
    QUICK_PROFIT = "quick_profit"
    SWING = "swing"


@dataclass(frozen=True)
class OutcomeContract:
    """A versioned, immutable definition of what one predicted outcome means."""

    version: str
    kind: ContractKind
    target_r: float | None
    entry_valid_sessions: int | None
    max_hold_sessions: int | None
    entry_rule: str
    stop_rule: str
    same_bar_rule: str
    timeout_rule: str
    success_rule: str


LEGACY_TRACKER_V1 = OutcomeContract(
    version="legacy-t1-tracker-v1",
    kind=ContractKind.LEGACY,
    target_r=None,
    entry_valid_sessions=None,
    max_hold_sessions=None,
    entry_rule="historical tracker behavior; read stored levels and source code",
    stop_rule="stored stop level",
    same_bar_rule="stop_before_target",
    timeout_rule="historical runtime max_hold; not retroactively inferred",
    success_rule="stored label and outcome; never pooled with versioned contracts",
)

QUICK_PROFIT_V1 = OutcomeContract(
    version="quick-profit-v1",
    kind=ContractKind.QUICK_PROFIT,
    target_r=0.75,
    entry_valid_sessions=3,
    max_hold_sessions=3,
    entry_rule=(
        "first valid session that touches the trigger; gap fills at the open; reject a "
        "chased open beyond the signal's declared chase limit"
    ),
    stop_rule="gap below stop exits at the open; otherwise a stop touch exits at the stop",
    same_bar_rule="stop_before_target",
    timeout_rule="close on the third held session when neither barrier resolved first",
    success_rule="0.75R target before stop or expiry and positive net result after costs",
)

SWING_V1 = OutcomeContract(
    version="swing-v1",
    kind=ContractKind.SWING,
    target_r=2.0,
    entry_valid_sessions=3,
    max_hold_sessions=10,
    entry_rule=(
        "first valid session that touches the trigger; gap fills at the open; reject a "
        "chased open beyond the signal's declared chase limit"
    ),
    stop_rule="gap below stop exits at the open; otherwise a stop touch exits at the stop",
    same_bar_rule="stop_before_target",
    timeout_rule="close on the tenth held session when neither barrier resolved first",
    success_rule="2.0R target before stop or expiry and positive net result after costs",
)

CONTRACTS: Mapping[str, OutcomeContract] = {
    contract.version: contract
    for contract in (LEGACY_TRACKER_V1, QUICK_PROFIT_V1, SWING_V1)
}

_RESOLVED_OUTCOMES = {
    "target",
    "stop",
    "gap_stop",
    "timeout",
    "trail",
    "time_stop",
    "max_hold",
    "end",
}
_NEVER_TRIGGERED_OUTCOMES = {"never_triggered", "expired", "chased", "invalidated"}
_INVALID_OUTCOMES = {
    "invalid",
    "unavailable",
    "excluded",
    "rejected_geometry",
    "untradeable",
}
_MARKETS = {"nse", "bse", "crypto"}


def classify_evidence(rejected_for: Iterable[str], *, shadow: bool) -> EvidenceClass:
    """Freeze recommendation authority; shadow takes precedence over other rejections."""

    if shadow:
        return EvidenceClass.SHADOW_CALL
    if any(str(reason).strip() for reason in rejected_for):
        return EvidenceClass.REJECTED_CALL
    return EvidenceClass.QUALIFIED_CALL


def classify_outcome(outcome: str | None) -> OutcomeState:
    """Fail closed for unknown terminal values instead of treating them as resolved."""

    if outcome is None or outcome == "insufficient":
        return OutcomeState.PENDING_CALL
    if outcome in _RESOLVED_OUTCOMES:
        return OutcomeState.RESOLVED_CALL
    if outcome in _NEVER_TRIGGERED_OUTCOMES:
        return OutcomeState.NEVER_TRIGGERED
    if outcome in _INVALID_OUTCOMES:
        return OutcomeState.INVALID_CALL
    return OutcomeState.INVALID_CALL


def is_trainable_outcome(outcome: str | None) -> bool:
    """Only a known, mature price outcome may enter a learning dataset."""

    return classify_outcome(outcome) is OutcomeState.RESOLVED_CALL


def infer_market(log_path: Path) -> str | None:
    """Infer market only from the dedicated tracker filename; generic test logs stay unset."""

    name = log_path.name.lower()
    for market in _MARKETS:
        if name == f"{market}_signal_tracking.jsonl":
            return market
    return None


def enrich_call_record(record: Mapping[str, Any], *, market: str | None = None) -> dict[str, Any]:
    """Return a backward-compatible record with explicit evidence and lifecycle fields."""

    out = dict(record)
    stored_market = out.get("market")
    if market is not None and market not in _MARKETS:
        raise ValueError(f"unsupported evidence market: {market}")
    if stored_market is not None and stored_market not in _MARKETS:
        raise ValueError(f"unsupported stored evidence market: {stored_market}")
    if market is not None and stored_market is not None and market != stored_market:
        raise ValueError(
            f"cross-market evidence row: stored={stored_market}, log={market}"
        )
    out["market"] = stored_market or market
    out["evidence_class"] = out.get("evidence_class") or classify_evidence(
        out.get("rejected_for") or [], shadow=bool(out.get("shadow"))
    ).value
    valid_evidence = {item.value for item in EvidenceClass}
    if out["evidence_class"] not in valid_evidence:
        raise ValueError(f"unknown evidence class: {out['evidence_class']}")
    # Lifecycle is derived from the current outcome every time so resolving an existing
    # pending row cannot leave a stale ``pending_call`` marker behind.
    out["outcome_state"] = classify_outcome(out.get("outcome")).value
    out["contract_kind"] = out.get("contract_kind") or ContractKind.LEGACY.value
    out["contract_version"] = out.get("contract_version") or LEGACY_TRACKER_V1.version
    if out["contract_version"] not in CONTRACTS:
        raise ValueError(f"unknown outcome contract: {out['contract_version']}")
    expected_kind = CONTRACTS[out["contract_version"]].kind.value
    if out["contract_kind"] != expected_kind:
        raise ValueError(
            f"contract kind/version mismatch: {out['contract_kind']} vs "
            f"{out['contract_version']}"
        )
    return out
