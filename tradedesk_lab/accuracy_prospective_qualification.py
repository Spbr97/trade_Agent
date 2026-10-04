"""Atomic prospective qualification review for the frozen NSE accuracy candidate.

The module combines M8-M11 without changing any of them.  It cannot promote a model or
change the canonical baseline.  Its strongest result is ``human_review_authorized``;
live eligibility is deliberately hard-coded false.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

VERSION = "accuracy-prospective-qualification-v1"
TERMINAL_VERSION = "accuracy-prospective-qualification-first-look-v1"
TERMINAL_STATUSES = frozenset({"human_review_authorized", "prospective_rejected"})
TERMINAL_FILE = "terminal-first-look.json"
DEFAULT_OUTPUT = OUTPUT / "accuracy_prospective_qualification"
M8_PATH = OUTPUT / "accuracy_prospective_shadow" / "state.json"
M9_PATH = OUTPUT / "accuracy_prospective_monitor" / "latest.json"
M10_PATH = OUTPUT / "accuracy_prospective_control" / "state.json"
M11_PATH = OUTPUT / "accuracy_prospective_timing" / "state.json"

EXPECTED_VERSIONS = {
    "m8_accuracy": "accuracy-prospective-shadow-v1",
    "m9_integrity_stress": "accuracy-prospective-integrity-v1",
    "m10_selection_control": "accuracy-prospective-control-v1",
    "m11_timing_control": "accuracy-prospective-timing-v1",
}
M8_GATES = (
    "resolved_calls",
    "active_sessions",
    "accuracy",
    "wilson_lower_bound",
    "session_target_rate",
    "expectancy_r",
)
CONTROL_GATES = ("resolved_calls", "active_sessions", "advantage_r", "p_value")

CANONICAL_BASELINE = {
    "contract": "aem-v1-same-session",
    "strict_wins": 149,
    "resolved_fills": 693,
    "strict_success_rate": 149 / 693,
    "wilson_lower_bound": 0.186035,
    "mean_net_r": -0.27471,
    "status": "unchanged",
}
LOCKED_CHALLENGER = {
    "contract": "trend-pullback-next-open-1atr-0.5r-3session",
    "strict_wins": 186,
    "resolved_fills": 223,
    "strict_success_rate": 186 / 223,
    "wilson_lower_bound": 0.7796832721797459,
    "mean_net_r": 0.08292911511374737,
    "evidence_class": "historical_locked_not_prospective",
}


def canonical_sha256(value: Any) -> str:
    """Return the same stable JSON fingerprint used by the frozen controls."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _terminal_report_sha256(report: dict[str, Any]) -> str:
    payload = {
        key: value for key, value in report.items() if key != "first_look_report_sha256"
    }
    return canonical_sha256(payload)


def _all_true(gates: Any, required: tuple[str, ...]) -> bool:
    return isinstance(gates, dict) and all(gates.get(name) is True for name in required)


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    except (TypeError, ValueError):
        return left == right


def _summary_matches(left: Any, right: Any, names: tuple[str, ...]) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return all(
        left.get(name) == right.get(name)
        if isinstance(left.get(name), (dict, list)) or isinstance(right.get(name), (dict, list))
        else _same_number(left.get(name), right.get(name))
        for name in names
    )


def _m8_component(state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None:
        return {
            "available": False,
            "ready": False,
            "passed": False,
            "status": "not_available",
            "gates": {name: False for name in M8_GATES},
        }
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    gates = summary.get("gate_checks") if isinstance(summary.get("gate_checks"), dict) else {}
    ready = gates.get("resolved_calls") is True and gates.get("active_sessions") is True
    return {
        "available": True,
        "ready": ready,
        "passed": summary.get("status") == "prospective_pass" and _all_true(gates, M8_GATES),
        "status": summary.get("status", "not_available"),
        "gates": {name: gates.get(name) is True for name in M8_GATES},
        "metrics": {
            name: summary.get(name)
            for name in (
                "resolved_calls",
                "wins",
                "accuracy",
                "wilson_lower_bound",
                "active_sessions",
                "session_target_rate",
                "expectancy_r",
            )
        },
    }


def _m9_component(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {
            "available": False,
            "ready": False,
            "passed": False,
            "status": "not_available",
            "gates": {
                "integrity": False,
                "double_slippage": False,
                "session_cluster_lower": False,
                "week_cluster_lower": False,
                "review_ready": False,
            },
        }
    integrity = report.get("integrity") or {}
    stress = report.get("double_slippage_stress") or {}
    uncertainty = report.get("uncertainty") or {}
    resolved = int(stress.get("resolved_calls") or 0)
    session_lower = uncertainty.get("session_cluster_lower_95")
    week_lower = uncertainty.get("week_cluster_lower_95")
    gates = {
        "integrity": integrity.get("passed") is True,
        "double_slippage": (
            stress.get("status") == "complete"
            and stress.get("positive_expectancy") is True
            and resolved >= 100
        ),
        "session_cluster_lower": session_lower is not None and float(session_lower) >= 0.70,
        "week_cluster_lower": week_lower is not None and float(week_lower) >= 0.70,
        "review_ready": report.get("review_ready") is True,
    }
    ready = resolved >= 100 and session_lower is not None and week_lower is not None
    return {
        "available": True,
        "ready": ready,
        "passed": all(gates.values()),
        "status": (
            "integrity_stress_pass"
            if all(gates.values())
            else "integrity_degraded"
            if integrity.get("passed") is False
            else "collecting_insufficient_evidence"
            if not ready
            else "integrity_stress_fail"
        ),
        "gates": gates,
        "metrics": {
            "stressed_resolved_calls": resolved,
            "stressed_accuracy": stress.get("accuracy"),
            "stressed_expectancy_r": stress.get("expectancy_r"),
            "session_cluster_lower_95": session_lower,
            "week_cluster_lower_95": week_lower,
        },
    }


def _control_component(
    state: dict[str, Any] | None,
    *,
    pass_status: str,
    count_name: str,
    sessions_name: str,
    advantage_name: str,
) -> dict[str, Any]:
    if state is None:
        return {
            "available": False,
            "ready": False,
            "passed": False,
            "status": "not_available",
            "gates": {name: False for name in CONTROL_GATES},
        }
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    gates = summary.get("gate_checks") if isinstance(summary.get("gate_checks"), dict) else {}
    ready = gates.get("resolved_calls") is True and gates.get("active_sessions") is True
    return {
        "available": True,
        "ready": ready,
        "passed": summary.get("status") == pass_status and _all_true(gates, CONTROL_GATES),
        "status": summary.get("status", "not_available"),
        "gates": {name: gates.get(name) is True for name in CONTROL_GATES},
        "metrics": {
            "resolved_calls": summary.get(count_name),
            "active_sessions": summary.get(sessions_name),
            "model_accuracy": summary.get("model_accuracy"),
            "model_expectancy_r": summary.get("model_expectancy_r"),
            "control_advantage_r": summary.get(advantage_name),
            "p_value": summary.get("p_value"),
        },
    }


def _version(value: dict[str, Any] | None, *, activation: bool = False) -> Any:
    if value is None:
        return None
    if activation:
        payload = value.get("activation") or {}
        return payload.get("version")
    return value.get("version")


def _parity(
    m8: dict[str, Any] | None,
    m9: dict[str, Any] | None,
    m10: dict[str, Any] | None,
    m11: dict[str, Any] | None,
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    inputs = (m8, m9, m10, m11)
    if any(value is None for value in inputs):
        missing = [
            name
            for name, value in zip(EXPECTED_VERSIONS, inputs, strict=True)
            if value is None
        ]
        return {
            "available": False,
            "identity_passed": False,
            "evaluation_ready": False,
            "evaluation_passed": False,
            "passed": False,
            "failures": [f"missing:{name}" for name in missing],
        }

    assert m8 is not None and m9 is not None and m10 is not None and m11 is not None
    activation = m8.get("activation") or {}
    activation_sha256 = canonical_sha256(activation)
    versions = {
        "m8_accuracy": _version(m8, activation=True),
        "m9_integrity_stress": _version(m9),
        "m10_selection_control": _version(m10),
        "m11_timing_control": _version(m11),
    }
    identity_failures = [
        f"version:{name}"
        for name, expected in EXPECTED_VERSIONS.items()
        if versions[name] != expected
    ]
    if m10.get("m8_activation_sha256") != activation_sha256:
        identity_failures.append("m10_activation")
    if m11.get("m8_activation_sha256") != activation_sha256:
        identity_failures.append("m11_activation")
    if canonical_sha256(m9.get("shadow_activation") or {}) != activation_sha256:
        identity_failures.append("m9_activation")
    summary_names = (
        "status",
        "selected_calls",
        "resolved_calls",
        "wins",
        "accuracy",
        "wilson_lower_bound",
        "active_sessions",
        "session_target_rate",
        "expectancy_r",
        "gate_checks",
    )
    if not _summary_matches(m8.get("summary"), m9.get("shadow_summary"), summary_names):
        identity_failures.append("m8_m9_summary")
    if (m9.get("integrity") or {}).get("passed") is False:
        identity_failures.append("m9_integrity")
    parity = m10.get("outcome_parity") or {}
    if parity.get("passed") is False:
        identity_failures.append("m10_outcome_parity")
    if (m11.get("source_integrity") or {}).get("passed") is False:
        identity_failures.append("m11_source_integrity")

    evaluation_ready = all(component.get("ready") is True for component in components.values())
    evaluation_failures: list[str] = []
    if evaluation_ready:
        s8 = m8.get("summary") or {}
        s10 = m10.get("summary") or {}
        s11 = m11.get("summary") or {}
        comparisons = {
            "m10_call_count": s10.get("resolved_model_calls") == s8.get("resolved_calls"),
            "m11_call_count": s11.get("paired_resolved_calls") == s8.get("resolved_calls"),
            "m10_session_count": s10.get("mature_sessions") == s8.get("active_sessions"),
            "m11_session_count": s11.get("active_sessions") == s8.get("active_sessions"),
            "m10_accuracy": _same_number(s10.get("model_accuracy"), s8.get("accuracy")),
            "m11_accuracy": _same_number(s11.get("model_accuracy"), s8.get("accuracy")),
            "m10_expectancy": _same_number(
                s10.get("model_expectancy_r"), s8.get("expectancy_r")
            ),
            "m11_expectancy": _same_number(
                s11.get("model_expectancy_r"), s8.get("expectancy_r")
            ),
            "m10_verified_calls": parity.get("checked_selected_calls")
            == s8.get("resolved_calls"),
        }
        evaluation_failures = [name for name, passed in comparisons.items() if not passed]

    return {
        "available": True,
        "activation_sha256": activation_sha256,
        "versions": versions,
        "identity_passed": not identity_failures,
        "evaluation_ready": evaluation_ready,
        "evaluation_passed": evaluation_ready and not evaluation_failures,
        "passed": not identity_failures and evaluation_ready and not evaluation_failures,
        "failures": identity_failures + evaluation_failures,
    }


def evaluate_qualification(
    m8_state: dict[str, Any] | None,
    m9_report: dict[str, Any] | None,
    m10_state: dict[str, Any] | None,
    m11_state: dict[str, Any] | None,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Combine the four frozen evidence streams without changing their decisions."""
    components = {
        "m8_accuracy": _m8_component(m8_state),
        "m9_integrity_stress": _m9_component(m9_report),
        "m10_selection_control": _control_component(
            m10_state,
            pass_status="selection_control_pass",
            count_name="resolved_model_calls",
            sessions_name="mature_sessions",
            advantage_name="selection_advantage_r",
        ),
        "m11_timing_control": _control_component(
            m11_state,
            pass_status="random_timing_pass",
            count_name="paired_resolved_calls",
            sessions_name="active_sessions",
            advantage_name="timing_advantage_r",
        ),
    }
    parity = _parity(m8_state, m9_report, m10_state, m11_state, components)
    all_available = all(component["available"] for component in components.values())
    all_ready = all(component["ready"] for component in components.values())
    all_passed = all(component["passed"] for component in components.values())
    integrity_failed = parity["available"] and not parity["identity_passed"]
    if not all_available:
        status = "not_available"
    elif integrity_failed or (all_ready and not parity["evaluation_passed"]):
        status = "degraded"
    elif not all_ready:
        status = "collecting_insufficient_evidence"
    elif all_passed and parity["passed"]:
        status = "human_review_authorized"
    else:
        status = "prospective_rejected"
    review_authorized = status == "human_review_authorized"
    activation = (m8_state or {}).get("activation") or {}
    return {
        "version": VERSION,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "market": "NSE",
        "status": status,
        "evidence_class": "fresh_prospective_shadow",
        "candidate": activation.get("candidate"),
        "candidate_activation_sha256": parity.get("activation_sha256"),
        "components": components,
        "parity": parity,
        "gate_checks": {
            "all_components_available": all_available,
            "all_components_ready": all_ready,
            "m8_accuracy_passed": components["m8_accuracy"]["passed"],
            "m9_integrity_stress_passed": components["m9_integrity_stress"]["passed"],
            "m10_selection_control_passed": components["m10_selection_control"]["passed"],
            "m11_timing_control_passed": components["m11_timing_control"]["passed"],
            "candidate_and_evaluation_parity": parity["passed"],
        },
        "qualification_passed": review_authorized,
        "review_authorized": review_authorized,
        "first_look_latched": False,
        "first_look_report_sha256": None,
        "canonical_baseline": CANONICAL_BASELINE,
        "locked_historical_challenger": LOCKED_CHALLENGER,
        "baseline_improved": False,
        "eligible_for_live": False,
        "authority": "read_only_human_review_gate_no_live_authority",
        "detail": (
            "This bundle can authorize a human review only. The 21.50% canonical baseline "
            "and all production, alert, sizing, management, broker and order behavior remain "
            "unchanged until a separate explicitly approved promotion decision."
        ),
    }


def _read_optional(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"qualification input is not a JSON object: {path}")
    return value


def _verified_terminal(path: Path) -> dict[str, Any]:
    """Read the immutable first-look result or fail closed on any inconsistency."""

    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE first-look terminal artifact is unreadable") from exc
    if not isinstance(envelope, dict):
        raise ValueError("NSE first-look terminal artifact is not an object")
    report = envelope.get("report")
    if not isinstance(report, dict):
        raise ValueError("NSE first-look terminal report is missing")
    errors: list[str] = []
    if envelope.get("version") != TERMINAL_VERSION:
        errors.append("terminal_version")
    if envelope.get("report_sha256") != canonical_sha256(report):
        errors.append("report_sha256")
    if report.get("version") != VERSION:
        errors.append("report_version")
    if report.get("market") != "NSE":
        errors.append("market")
    status = report.get("status")
    if status not in TERMINAL_STATUSES:
        errors.append("status")
    if report.get("baseline_improved") is not False:
        errors.append("baseline_improved")
    if report.get("eligible_for_live") is not False:
        errors.append("eligible_for_live")
    if not isinstance(report.get("source_artifacts"), dict):
        errors.append("source_artifacts")
    gates = report.get("gate_checks")
    parity = report.get("parity")
    if not isinstance(gates, dict) or gates.get("all_components_ready") is not True:
        errors.append("mature_first_look")
    if not isinstance(parity, dict) or parity.get("evaluation_ready") is not True:
        errors.append("evaluation_ready")
    elif parity.get("passed") is not True:
        errors.append("parity_passed")
    authorized = status == "human_review_authorized"
    if report.get("qualification_passed") is not authorized:
        errors.append("qualification_passed")
    if report.get("review_authorized") is not authorized:
        errors.append("review_authorized")
    if report.get("first_look_latched") is not True:
        errors.append("first_look_latched")
    if report.get("first_look_report_sha256") != _terminal_report_sha256(report):
        errors.append("first_look_report_sha256")
    if authorized and (not gates or not all(value is True for value in gates.values())):
        errors.append("passing_gates")
    if errors:
        raise ValueError(
            "NSE first-look terminal artifact failed verification: " + ", ".join(errors)
        )
    return report


def _latch_terminal(output: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Create the first-look envelope once; a concurrent winner is re-read and verified."""

    output.mkdir(parents=True, exist_ok=True)
    terminal_path = output / TERMINAL_FILE
    report = dict(report)
    report["first_look_latched"] = True
    report["first_look_report_sha256"] = _terminal_report_sha256(report)
    envelope = {
        "version": TERMINAL_VERSION,
        "latched_at": report.get("created_at"),
        "report_sha256": canonical_sha256(report),
        "report": report,
    }
    try:
        with terminal_path.open("x", encoding="utf-8") as stream:
            json.dump(envelope, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        return _verified_terminal(terminal_path)
    return _verified_terminal(terminal_path)


def run_qualification(
    *,
    output: Path = DEFAULT_OUTPUT,
    m8_path: Path = M8_PATH,
    m9_path: Path = M9_PATH,
    m10_path: Path = M10_PATH,
    m11_path: Path = M11_PATH,
) -> dict[str, Any]:
    """Read the current evidence atomically enough for a read-only qualification report."""
    terminal_path = output / TERMINAL_FILE
    if terminal_path.exists():
        terminal = _verified_terminal(terminal_path)
        write_json(output / "latest.json", terminal)
        return terminal
    paths = {
        "m8_accuracy": m8_path,
        "m9_integrity_stress": m9_path,
        "m10_selection_control": m10_path,
        "m11_timing_control": m11_path,
    }
    values = {name: _read_optional(path) for name, path in paths.items()}
    report = evaluate_qualification(
        values["m8_accuracy"],
        values["m9_integrity_stress"],
        values["m10_selection_control"],
        values["m11_timing_control"],
    )
    report["source_artifacts"] = {
        name: {
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "available": path.is_file(),
            "sha256": digest(path) if path.is_file() else None,
        }
        for name, path in paths.items()
    }
    if report["status"] in TERMINAL_STATUSES:
        report = _latch_terminal(output, report)
    write_json(output / "latest.json", report)
    return report
