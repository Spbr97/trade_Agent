"""Explicit promotion, prospective registration, health pause, and rollback controls."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST
from tradedesk.engine.scoring import wilson_lower_bound
from tradedesk.precision_selector import PRIMARY_SELECTOR_POLICY, SELECTOR_VERSION
from tradedesk.prediction_ledger import canonical_sha256
from tradedesk.random_timing_control import (
    CONTROL_VERSION as RANDOM_TIMING_CONTROL_VERSION,
)
from tradedesk.random_timing_control import (
    control_configuration as random_timing_configuration,
)
from tradedesk.random_timing_control import validate_random_timing_report

CONTROL_VERSION = "promotion-control-v2"

_REQUIRED_HISTORICAL_GATES = (
    "sample_threshold_reached",
    "primary_selector_policy_clears_accuracy_and_net_gate",
    "matched_random_timing_margin_passed",
)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in payload.items() if key != "record_sha256"}
    out["record_sha256"] = canonical_sha256(out)
    return out


def _validate(record: dict[str, Any], *, kind: str) -> None:
    digest = record.get("record_sha256")
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    if record.get("version") != CONTROL_VERSION or record.get("kind") != kind:
        raise ValueError(f"invalid {kind} contract")
    if not digest or digest != canonical_sha256(payload):
        raise ValueError(f"invalid {kind} integrity hash")


def challenger_reproduction_sha256(challenger: dict[str, Any]) -> str:
    controls = challenger.get("controls") or {}
    random_timing = controls.get("random_timing") if isinstance(controls, dict) else None
    return canonical_sha256(
        {
            "experiment_id": challenger.get("experiment_id"),
            "dataset_id": challenger.get("dataset_id"),
            "configuration": challenger.get("configuration"),
            "cohorts": challenger.get("cohorts"),
            "features": challenger.get("features"),
            "model": challenger.get("model"),
            "scores": challenger.get("scores"),
            "precision_selector": challenger.get("precision_selector"),
            # Bind the exact replay, including its source hashes, assignments,
            # aggregates and replay/aggregate identity.  A reproduction that changes
            # only this control is therefore a different reproduction.
            "random_timing": random_timing,
            "final_evidence_gates": challenger.get("final_evidence_gates"),
        }
    )


def _validated_random_timing_evidence(challenger: dict[str, Any]) -> dict[str, Any]:
    controls = challenger.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("prospective cohort requires challenger controls")
    timing = controls.get("random_timing")
    if not isinstance(timing, dict):
        raise ValueError("prospective cohort requires exact random-timing evidence")
    if (
        timing.get("status") != "random_timing_pass"
        or timing.get("random_timing_gate_passed") is not True
    ):
        raise ValueError("prospective cohort requires passing random-timing evidence")
    if timing.get("version") != RANDOM_TIMING_CONTROL_VERSION:
        raise ValueError("random-timing evidence control version mismatch")
    if timing.get("selector_version") != SELECTOR_VERSION:
        raise ValueError("random-timing evidence selector version mismatch")
    if timing.get("selector_policy") != PRIMARY_SELECTOR_POLICY:
        raise ValueError("random-timing evidence selector-policy mismatch")
    if timing.get("control_configuration") != random_timing_configuration():
        raise ValueError("random-timing evidence frozen configuration mismatch")
    replay_digest = timing.get("replay_sha256")
    if not _is_sha256(replay_digest):
        raise ValueError("random-timing evidence lacks a replay digest")
    replay_payload = {
        key: value for key, value in timing.items() if key != "replay_sha256"
    }
    if replay_digest != canonical_sha256(replay_payload):
        raise ValueError("random-timing replay digest mismatch")
    validate_random_timing_report(timing)
    if (
        timing.get("evidence_mode")
        != "append_only_single_frozen_selector_terminal_cohort"
        or not _is_sha256(timing.get("evidence_ledger_sha256"))
        or not _is_sha256(timing.get("selection_context_sha256"))
    ):
        raise ValueError("random-timing evidence lacks a frozen terminal ledger binding")
    if timing.get("market") != challenger.get("market"):
        raise ValueError("random-timing evidence market mismatch")
    if timing.get("markets_pooled") is not False:
        raise ValueError("random-timing evidence must not pool markets")
    if timing.get("contracts_pooled") is not False:
        raise ValueError("random-timing evidence must not pool contracts")
    timing_selection = challenger.get("timing_selection")
    if (
        not isinstance(timing_selection, dict)
        or timing_selection.get("context_sha256")
        != timing.get("selection_context_sha256")
    ):
        raise ValueError("random-timing evidence selection-context mismatch")
    return timing


def _validated_primary_selector(challenger: dict[str, Any]) -> tuple[dict[str, Any], str]:
    selector = challenger.get("precision_selector")
    if not isinstance(selector, dict):
        raise ValueError("prospective cohort requires one preregistered selector policy")
    policy = selector.get("policy")
    primary = selector.get("primary_policy")
    operating_points = selector.get("operating_points")
    if (
        selector.get("version") != SELECTOR_VERSION
        or selector.get("market") != challenger.get("market")
        or not isinstance(policy, dict)
        or policy.get("version") != SELECTOR_VERSION
        or policy.get("market") != challenger.get("market")
        or primary != PRIMARY_SELECTOR_POLICY
        or selector.get("selected_live_policy") is not None
        or not isinstance(operating_points, dict)
    ):
        raise ValueError("prospective cohort selector identity/policy mismatch")
    score = operating_points.get(PRIMARY_SELECTOR_POLICY)
    if (
        not isinstance(score, dict)
        or score.get("passes_final_accuracy_gate") is not True
        or int(score.get("selected") or 0) <= 0
        or float(score.get("strict_accuracy") or 0.0) < 0.80
        or float(score.get("wilson_95_low") or 0.0) < 0.70
        or float(score.get("mean_net_r") or 0.0) <= 0.0
    ):
        raise ValueError("prospective cohort primary selector evidence is incomplete")
    return selector, primary


def register_prospective_cohort(
    challenger: dict[str, Any], path: Path, *, starts_after: str
) -> dict[str, Any]:
    """Freeze a forward cohort before outcomes; historical weakness cannot be waived."""

    gates = challenger.get("final_evidence_gates") or {}
    missing = [
        name for name in _REQUIRED_HISTORICAL_GATES if gates.get(name) is not True
    ]
    if missing:
        raise ValueError("challenger cannot enter prospective cohort: " + ", ".join(missing))
    contract_versions = (challenger.get("cohorts") or {}).get("contract_versions") or []
    if len(contract_versions) != 1:
        raise ValueError("prospective cohort requires one frozen outcome contract")
    precision_selector, primary_policy = _validated_primary_selector(challenger)
    timing = _validated_random_timing_evidence(challenger)
    if timing.get("contract_version") != contract_versions[0]:
        raise ValueError("random-timing evidence contract mismatch")
    if timing.get("selector_policy") != primary_policy:
        raise ValueError("random-timing evidence selector-policy mismatch")
    reproduction_sha256 = challenger_reproduction_sha256(challenger)
    payload = _seal(
        {
            "version": CONTROL_VERSION,
            "kind": "prospective_cohort",
            "cohort_id": canonical_sha256(
                {
                    "experiment_id": challenger.get("experiment_id"),
                    "starts_after": starts_after,
                    "contract_version": contract_versions[0],
                    "reproduction_sha256": reproduction_sha256,
                }
            ),
            "registered_at": datetime.now(IST).isoformat(),
            "starts_after": starts_after,
            "market": challenger.get("market"),
            "experiment_id": challenger.get("experiment_id"),
            "dataset_id": challenger.get("dataset_id"),
            "contract_version": contract_versions[0],
            "reproduction_sha256": reproduction_sha256,
            "random_timing_evidence_sha256": canonical_sha256(timing),
            "frozen_random_timing_evidence": copy.deepcopy(timing),
            "frozen_final_evidence_gates": copy.deepcopy(
                challenger.get("final_evidence_gates")
            ),
            "frozen_configuration": challenger.get("configuration"),
            "frozen_features": challenger.get("features"),
            "frozen_model": challenger.get("model"),
            "frozen_selector_policy": precision_selector.get("policy"),
            "frozen_selector_operating_point": primary_policy,
            "execution_contract": challenger.get("cohorts"),
            "source": "forward_only_live",
            "backfill_allowed": False,
            "outcomes_at_registration": 0,
            "promotion_authorized": False,
        }
    )
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        _validate(existing, kind="prospective_cohort")
        if existing.get("cohort_id") == payload["cohort_id"]:
            return dict(existing)
        raise ValueError("a different prospective cohort is already registered")
    _write_json(path, payload)
    return payload


def seal_prospective_result(
    cohort: dict[str, Any], metrics: dict[str, Any], path: Path
) -> dict[str, Any]:
    _validate(cohort, kind="prospective_cohort")
    calls = int(metrics.get("resolved_calls") or 0)
    wins = int(metrics.get("wins") or 0)
    accuracy = wins / calls if calls else 0.0
    wilson = wilson_lower_bound(wins, calls)
    passed = bool(
        calls >= 100
        and int(metrics.get("sessions") or 0) >= 30
        and accuracy >= 0.80
        and wilson >= 0.70
        and float(metrics.get("mean_net_r") or 0.0) > 0
        and float(metrics.get("mean_after_tax_r") or 0.0) > 0
        and float(metrics.get("random_timing_margin_r") or 0.0) >= 0.10
        and metrics.get("integrity_passed") is True
    )
    result = _seal(
        {
            "version": CONTROL_VERSION,
            "kind": "prospective_result",
            "cohort_id": cohort["cohort_id"],
            "experiment_id": cohort["experiment_id"],
            "market": cohort["market"],
            "contract_version": cohort["contract_version"],
            "completed_at": datetime.now(IST).isoformat(),
            "metrics": {
                **metrics,
                "strict_accuracy": accuracy,
                "wilson_95_low": wilson,
            },
            "passed": passed,
            "promotion_authorized": False,
        }
    )
    _write_json(path, result)
    return result


def build_promotion_review(
    challenger: dict[str, Any],
    cohort: dict[str, Any],
    prospective_result: dict[str, Any],
    reproduction_sha256: str,
    path: Path,
) -> dict[str, Any]:
    _validate(cohort, kind="prospective_cohort")
    _validate(prospective_result, kind="prospective_result")
    expected = challenger_reproduction_sha256(challenger)
    blockers = []
    if cohort.get("experiment_id") != challenger.get("experiment_id"):
        blockers.append("cohort and challenger experiment mismatch")
    if prospective_result.get("cohort_id") != cohort.get("cohort_id"):
        blockers.append("prospective result and cohort mismatch")
    if prospective_result.get("passed") is not True:
        blockers.append("prospective evidence did not pass")
    if reproduction_sha256 != expected or cohort.get("reproduction_sha256") != expected:
        blockers.append("independent reproduction hash mismatch")
    review = _seal(
        {
            "version": CONTROL_VERSION,
            "kind": "promotion_review",
            "review_id": canonical_sha256(
                {
                    "experiment_id": challenger.get("experiment_id"),
                    "cohort_id": cohort.get("cohort_id"),
                    "reproduction_sha256": reproduction_sha256,
                }
            ),
            "created_at": datetime.now(IST).isoformat(),
            "market": challenger.get("market"),
            "experiment_id": challenger.get("experiment_id"),
            "contract_version": cohort.get("contract_version"),
            "cohort_id": cohort.get("cohort_id"),
            "reproduction_sha256": reproduction_sha256,
            "side_by_side": challenger.get("scores"),
            "prospective_metrics": prospective_result.get("metrics"),
            "selector_policy": (
                (challenger.get("precision_selector") or {}).get("primary_policy")
            ),
            "status": "blocked" if blockers else "awaiting_explicit_user_approval",
            "blockers": blockers,
            "promotion_authorized": False,
        }
    )
    _write_json(path, review)
    return review


def approve_promotion(
    review: dict[str, Any],
    path: Path,
    *,
    explicit_user_approval: bool,
    approval_reference: str,
) -> dict[str, Any]:
    _validate(review, kind="promotion_review")
    if not explicit_user_approval or not approval_reference.strip():
        raise PermissionError("explicit user approval is required for promotion")
    if review.get("status") != "awaiting_explicit_user_approval" or review.get("blockers"):
        raise ValueError("blocked promotion review cannot be approved")
    previous = None
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        validate_promotion_record(previous)
    promotion = _seal(
        {
            "version": CONTROL_VERSION,
            "kind": "promotion",
            "status": "approved",
            "approved_by_user": True,
            "approval_reference": approval_reference,
            "approved_at": datetime.now(IST).isoformat(),
            "market": review.get("market"),
            "experiment_id": review.get("experiment_id"),
            "contract_version": review.get("contract_version"),
            "review_id": review.get("review_id"),
            "selector_policy": review.get("selector_policy"),
            "paused": False,
            "pause_reasons": [],
            "previous_promotion": previous,
        }
    )
    _write_json(path, promotion)
    return promotion


def validate_promotion_record(record: dict[str, Any]) -> None:
    _validate(record, kind="promotion")
    if record.get("approved_by_user") is not True or not record.get("approval_reference"):
        raise ValueError("promotion lacks explicit approval evidence")


def monitor_promotion_health(
    promotion: dict[str, Any], *, data_fresh: bool, drift_detected: bool, contract_matches: bool
) -> dict[str, Any]:
    validate_promotion_record(promotion)
    reasons = []
    if not data_fresh:
        reasons.append("stale_data")
    if drift_detected:
        reasons.append("performance_or_calibration_drift")
    if not contract_matches:
        reasons.append("contract_mismatch")
    updated = {
        **promotion,
        "status": "paused" if reasons else promotion.get("status"),
        "paused": bool(reasons),
        "pause_reasons": reasons,
        "health_checked_at": datetime.now(IST).isoformat(),
    }
    return _seal(updated)


def rollback_promotion(
    promotion: dict[str, Any], path: Path, *, explicit_user_request: bool
) -> dict[str, Any]:
    validate_promotion_record(promotion)
    if not explicit_user_request:
        raise PermissionError("explicit user request is required for rollback")
    previous = promotion.get("previous_promotion")
    if not isinstance(previous, dict):
        raise ValueError("no previous promotion is available for rollback")
    validate_promotion_record(previous)
    restored = _seal(
        {
            **previous,
            "rollback_from_experiment_id": promotion.get("experiment_id"),
            "rolled_back_at": datetime.now(IST).isoformat(),
        }
    )
    _write_json(path, restored)
    return restored
