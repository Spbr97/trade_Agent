"""Deterministic, market-isolated learning datasets from immutable prediction records."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from tradedesk.failure_attribution import attribute_failure
from tradedesk.prediction_ledger import canonical_sha256, validate_prediction_record

DATASET_VERSION = "self-learning-dataset-v2"
DatasetPurpose = Literal["development", "locked_test", "prospective"]


@dataclass(frozen=True)
class LearningDataset:
    dataset_id: str
    version: str
    market: str
    purpose: DatasetPurpose
    rows: tuple[dict[str, Any], ...]
    exclusions: dict[str, int]
    source_records: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numeric_features(value: Any, *, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            out.update(_numeric_features(value[key], prefix=name))
    elif isinstance(value, bool):
        out[prefix] = float(value)
    elif isinstance(value, int | float) and math.isfinite(float(value)):
        out[prefix] = float(value)
    return out


def build_learning_dataset(
    records: Iterable[Mapping[str, Any]],
    *,
    market: str,
    purpose: DatasetPurpose,
) -> LearningDataset:
    if market not in {"nse", "bse", "crypto"}:
        raise ValueError(f"unsupported learning market: {market}")
    source = [dict(record) for record in records]
    exclusions: Counter[str] = Counter()
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        source,
        key=lambda r: (str(r.get("armed_on") or ""), str(r.get("signal_id") or "")),
    )
    for record in ordered:
        signal_id = str(record.get("signal_id") or "")
        if not signal_id:
            exclusions["missing_signal_id"] += 1
            continue
        if signal_id in seen:
            exclusions["duplicate_signal_id"] += 1
            continue
        seen.add(signal_id)
        if record.get("market") != market:
            exclusions["market_mismatch"] += 1
            continue
        if record.get("ledger_schema_version") != "prediction-ledger-v1":
            exclusions["legacy_unsealed"] += 1
            continue
        try:
            validate_prediction_record(record)
        except (TypeError, ValueError):
            exclusions["integrity_or_contract_mismatch"] += 1
            continue
        state = record.get("outcome_state")
        if state == "pending_call":
            exclusions["pending"] += 1
            continue
        if state == "invalid_call":
            exclusions["invalid"] += 1
            continue
        if state == "never_triggered":
            exclusions["never_triggered"] += 1
            continue
        if state != "resolved_call" or record.get("label") not in {0, 1}:
            exclusions["unusable_outcome"] += 1
            continue
        if purpose == "prospective" and record.get("source") != "live":
            exclusions["backfill_not_prospective"] += 1
            continue
        payload = record["prediction_payload"]
        assert isinstance(payload, dict)
        features = _numeric_features(
            {
                "context": payload.get("context") or {},
                "decision": {
                    "rule_score": (payload.get("decision") or {}).get("rule_score"),
                    "probability": (payload.get("decision") or {}).get("probability"),
                },
                "execution": {
                    key: (payload.get("execution") or {}).get(key)
                    for key in (
                        "quantity",
                        "risk_amount",
                        "risk_pct",
                        "position_value",
                        "estimated_round_trip_cost",
                        "net_rr_t1",
                        "net_rr_t2",
                    )
                },
                "source_snapshot": payload.get("source_snapshot") or {},
            }
        )
        evidence_class = str(record.get("evidence_class"))
        rows.append(
            {
                "signal_id": signal_id,
                "prediction_sha256": record.get("prediction_sha256"),
                "market": market,
                "symbol": record.get("symbol"),
                "setup": record.get("setup"),
                "sector": (payload.get("instrument") or {}).get("sector"),
                "regime": (payload.get("context") or {}).get("market_regime"),
                "contract_kind": record.get("contract_kind"),
                "contract_version": record.get("contract_version"),
                "strategy_version": (payload.get("versions") or {}).get("strategy"),
                "feature_version": (payload.get("versions") or {}).get("feature_contract"),
                "model_version": (payload.get("versions") or {}).get("model"),
                "model_kind": (payload.get("versions") or {}).get("model_kind"),
                "evidence_class": evidence_class,
                "evidence_role": (
                    "recommended" if evidence_class == "qualified_call" else "counterfactual"
                ),
                "cohort": purpose,
                "armed_on": record.get("armed_on"),
                "features": features,
                "label": int(record["label"]),
                "outcome": record.get("outcome"),
                "gross_r": record.get("gross_r", record.get("r_multiple")),
                "net_r": record.get("net_r"),
                "after_tax_r": record.get("after_tax_r"),
                "holding_sessions": record.get("holding_sessions"),
                "entry_on": record.get("entry_on"),
                "exit_on": record.get("exit_on"),
                "time_to_resolution_sessions": record.get("time_to_resolution_sessions"),
                "failure_attributions": (
                    record.get("failure_attributions") or attribute_failure(record)
                ),
            }
        )
    identity = {
        "version": DATASET_VERSION,
        "market": market,
        "purpose": purpose,
        "rows": [
            {
                "signal_id": row["signal_id"],
                "prediction_sha256": row["prediction_sha256"],
                "label": row["label"],
                "outcome": row["outcome"],
                "net_r": row["net_r"],
            }
            for row in rows
        ],
    }
    return LearningDataset(
        dataset_id=canonical_sha256(identity),
        version=DATASET_VERSION,
        market=market,
        purpose=purpose,
        rows=tuple(rows),
        exclusions=dict(sorted(exclusions.items())),
        source_records=len(source),
    )


def register_dataset_use(dataset: LearningDataset, registry_path: Path) -> dict[str, Any]:
    """Append one use, refusing reuse of evidence ever registered as locked test."""

    existing = []
    if registry_path.exists():
        existing = [
            json.loads(line)
            for line in registry_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    ids = {str(row["signal_id"]) for row in dataset.rows}
    for use in existing:
        prior_ids = set(use.get("signal_ids") or [])
        overlap = ids & prior_ids
        if overlap and (use.get("purpose") == "locked_test" or dataset.purpose == "locked_test"):
            raise ValueError(
                "locked outcome reuse refused: " + ", ".join(sorted(overlap)[:5])
            )
    if any(use.get("dataset_id") == dataset.dataset_id for use in existing):
        raise ValueError(f"dataset already registered: {dataset.dataset_id}")
    record = {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "market": dataset.market,
        "purpose": dataset.purpose,
        "signal_ids": sorted(ids),
        "row_count": len(dataset.rows),
        "dataset_sha256": canonical_sha256(dataset.to_dict()),
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with registry_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record
