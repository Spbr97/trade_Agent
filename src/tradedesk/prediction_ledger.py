"""Immutable prediction-time payloads for the per-market signal trackers.

The tracker JSONL remains the operational materialized view.  New rows additionally carry
a canonical prediction payload and SHA-256 seal.  Outcome fields are deliberately outside
that payload: resolving a call may append facts, but cannot rewrite what was predicted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from tradedesk.evidence import CONTRACTS
from tradedesk.prediction.features import FEATURE_VERSION

LEDGER_SCHEMA_VERSION = "prediction-ledger-v1"
STRATEGY_VERSION = "evening-scan-v1"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def contract_sha256(contract_version: str) -> str:
    try:
        contract = CONTRACTS[contract_version]
    except KeyError as exc:
        raise ValueError(f"unknown outcome contract: {contract_version}") from exc
    return canonical_sha256(asdict(contract))


def build_prediction_payload(
    *,
    watchlist: Any,
    entry: Any,
    market: str,
    source: str,
    evidence_class: str,
    contract_kind: str,
    contract_version: str,
) -> dict[str, Any]:
    """Capture only information available when the watchlist was generated."""

    signal = entry.signal
    generated_at = watchlist.generated_at.isoformat()
    regime = watchlist.regime.model_dump(mode="json") if watchlist.regime is not None else None
    source_snapshot = {
        "as_of_session": watchlist.on.isoformat(),
        "watchlist_generated_at": generated_at,
        "regime": regime,
        "signal_geometry": signal.geometry,
        "score_components": entry.score_components,
        "score_notes": entry.score_notes,
        "source_feature_row": entry.source_bar,
        "relative_strength_percentile": signal.rs_percentile,
        "sector_percentile": entry.sector_percentile,
        "atr_pct": entry.atr_pct,
        "average_turnover": entry.avg_turnover,
        "results_in_sessions": entry.results_in_sessions,
    }
    contract_hash = contract_sha256(contract_version)
    payload = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "signal_id": signal.id,
        "created_at": generated_at,
        "source": source,
        "market": market,
        "instrument": {
            "scrip_code": signal.scrip_code,
            "symbol": signal.symbol,
            "sector": entry.sector,
        },
        "setup": signal.setup.value,
        "evidence_class": evidence_class,
        "levels": {
            "armed_on": signal.armed_on.isoformat(),
            "entry_range": [signal.trigger, signal.trigger],
            "stop": signal.stop,
            "targets": [signal.t1, signal.t2],
            "entry_valid_sessions": signal.valid_sessions,
            "intended_holding_sessions": signal.exit_plan.max_hold_sessions,
            "chased_atr_multiple": signal.chased_atr_mult,
        },
        "decision": {
            "grade": entry.grade.value,
            "rule_score": entry.score,
            "probability": entry.probability,
            "alertable": entry.alertable,
            "rejected_for": list(entry.rejected_for),
        },
        "context": {
            "market_regime": signal.regime,
            "sector": entry.sector,
            "sector_percentile": entry.sector_percentile,
            "sector_regime": None,  # no sector regime classifier exists; absence is explicit
            "relative_strength_percentile": signal.rs_percentile,
            "atr_pct": entry.atr_pct,
            "average_turnover": entry.avg_turnover,
            "score_components": entry.score_components,
            "signal_geometry": signal.geometry,
        },
        "execution": {
            "quantity": entry.qty,
            "risk_amount": entry.risk_amount,
            "risk_pct": entry.risk_pct,
            "position_value": entry.position_value,
            "size_caps": list(entry.size_caps),
            "estimated_round_trip_cost": entry.costs_round_trip,
            "net_rr_t1": entry.net_rr_t1,
            "net_rr_t2": entry.net_rr_t2,
            "assumptions": watchlist.execution_assumptions,
        },
        "versions": {
            "strategy": STRATEGY_VERSION,
            "feature_contract": entry.feature_version or FEATURE_VERSION,
            "model": entry.model_version,
            "model_kind": entry.model_kind,
        },
        "contract": {
            "kind": contract_kind,
            "version": contract_version,
            "sha256": contract_hash,
        },
        "source_snapshot": source_snapshot,
        "source_snapshot_sha256": canonical_sha256(source_snapshot),
    }
    return payload


def seal_prediction(payload: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(payload))


def validate_prediction_record(record: Mapping[str, Any]) -> None:
    """Fail closed if a sealed prediction or its materialized fields were modified."""

    payload = record.get("prediction_payload")
    digest = record.get("prediction_sha256")
    schema = record.get("ledger_schema_version")
    if payload is None and digest is None and schema in {None, "legacy-unsealed-v0"}:
        return
    if not isinstance(payload, dict) or not digest:
        raise ValueError("incomplete immutable prediction seal")
    if schema != LEDGER_SCHEMA_VERSION or payload.get("schema_version") != schema:
        raise ValueError("prediction ledger schema mismatch")
    if seal_prediction(payload) != digest:
        raise ValueError("immutable prediction payload hash mismatch")
    if payload.get("source_snapshot_sha256") != canonical_sha256(payload.get("source_snapshot")):
        raise ValueError("source snapshot hash mismatch")
    contract = payload.get("contract") or {}
    if contract.get("sha256") != contract_sha256(str(contract.get("version"))):
        raise ValueError("outcome contract hash mismatch")

    mirrors = {
        "signal_id": payload.get("signal_id"),
        "market": payload.get("market"),
        "source": payload.get("source"),
        "scrip_code": (payload.get("instrument") or {}).get("scrip_code"),
        "symbol": (payload.get("instrument") or {}).get("symbol"),
        "setup": payload.get("setup"),
        "armed_on": (payload.get("levels") or {}).get("armed_on"),
        "grade": (payload.get("decision") or {}).get("grade"),
        "probability": (payload.get("decision") or {}).get("probability"),
        "rejected_for": (payload.get("decision") or {}).get("rejected_for"),
        "entry": ((payload.get("levels") or {}).get("entry_range") or [None])[0],
        "stop": (payload.get("levels") or {}).get("stop"),
        "t1": ((payload.get("levels") or {}).get("targets") or [None, None])[0],
        "t2": ((payload.get("levels") or {}).get("targets") or [None, None])[1],
        "net_rr_t1": (payload.get("execution") or {}).get("net_rr_t1"),
        "net_rr_t2": (payload.get("execution") or {}).get("net_rr_t2"),
        "contract_kind": contract.get("kind"),
        "contract_version": contract.get("version"),
        "contract_sha256": contract.get("sha256"),
        "source_snapshot_sha256": payload.get("source_snapshot_sha256"),
        "strategy_version": (payload.get("versions") or {}).get("strategy"),
        "feature_version": (payload.get("versions") or {}).get("feature_contract"),
        "model_version": (payload.get("versions") or {}).get("model"),
        "evidence_class": payload.get("evidence_class"),
        "shadow": payload.get("evidence_class") == "shadow_call",
    }
    for field, expected in mirrors.items():
        if record.get(field) != expected:
            raise ValueError(f"immutable prediction field changed: {field}")
