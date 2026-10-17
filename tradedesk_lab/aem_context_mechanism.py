"""Preregistered mechanism test for causal AEM index-price context.

The protocol is intentionally small and fixed before real AEM outcomes are joined to
the feature-only integrity artifact.  Passing permits a later selector diagnostic;
it never changes the canonical baseline or production behavior by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from tradedesk_lab.aem_context_integrity import OUTCOME_COLUMNS
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json


@dataclass(frozen=True)
class ContextRule:
    id: str
    window_minutes: int
    mode: str

    def __post_init__(self) -> None:
        if self.window_minutes not in {3, 5}:
            raise ValueError("context mechanism rules are frozen to 3m or 5m returns")
        if self.mode not in {
            "nifty50_positive",
            "majority_positive",
            "all_positive",
        }:
            raise ValueError("unknown context mechanism rule mode")
        if not self.id or not self.id.replace("_", "").isalnum():
            raise ValueError("context mechanism rule id is invalid")


RULES = (
    ContextRule("nifty50_positive_5m", 5, "nifty50_positive"),
    ContextRule("majority_positive_5m", 5, "majority_positive"),
    ContextRule("all_positive_5m", 5, "all_positive"),
    ContextRule("all_positive_3m", 3, "all_positive"),
)


@dataclass(frozen=True)
class ContextMechanismProtocol:
    version: str = "aem-index-context-mechanism-v1"
    evidence_class: str = "consumed_historical_development_diagnostic"
    rules: tuple[ContextRule, ...] = RULES
    evaluation_sessions: int = 120
    fold_count: int = 3
    fold_sessions: int = 40
    shuffle_repetitions: int = 256
    shuffle_seed: int = 20260930
    temporal_placebo: str = "within_session_circular_next_event_rotation"
    minimum_resolved_fills: int = 100
    minimum_active_sessions: int = 48
    minimum_active_session_coverage: float = 0.40
    maximum_shuffle_empirical_p: float = 0.05
    expected_trade_attempts: int = 815
    expected_resolved_fills: int = 693
    expected_strict_wins: int = 149
    expected_baseline_rate: float = 0.215007215007215
    expected_baseline_mean_net_r: float = -0.2747079541350378

    def __post_init__(self) -> None:
        if not 1 <= len(self.rules) <= 6 or len({rule.id for rule in self.rules}) != len(
            self.rules
        ):
            raise ValueError("context mechanism requires one to six unique rules")
        if self.evaluation_sessions != self.fold_count * self.fold_sessions:
            raise ValueError("context mechanism folds must cover all sessions exactly")
        if self.fold_count != 3 or self.fold_sessions != 40:
            raise ValueError("context mechanism is frozen to three 40-session folds")
        if self.shuffle_repetitions < 128:
            raise ValueError("context mechanism requires at least 128 shuffles")
        if not 0 < self.maximum_shuffle_empirical_p <= 0.05:
            raise ValueError("context mechanism shuffle p gate cannot exceed five percent")
        if self.minimum_resolved_fills < 100 or self.minimum_active_sessions < 30:
            raise ValueError("context mechanism sample floors cannot be weakened")
        if self.minimum_active_session_coverage != 0.40:
            raise ValueError("context mechanism coverage floor must remain 40 percent")
        if self.minimum_active_sessions < math.ceil(
            self.evaluation_sessions * self.minimum_active_session_coverage
        ):
            raise ValueError("active-session floor is below the coverage floor")
        if self.temporal_placebo != "within_session_circular_next_event_rotation":
            raise ValueError("context mechanism temporal placebo is frozen")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_CONTEXT_MECHANISM_PROTOCOL = ContextMechanismProtocol()
RETURN_SLUGS = ("nifty50", "bank_nifty", "nifty_financial")


def _as_bool(series: pd.Series, name: str, *, allow_missing: bool = False) -> pd.Series:
    mapped = series.map(
        {
            True: True,
            False: False,
            "True": True,
            "False": False,
            "true": True,
            "false": False,
            "1": True,
            "0": False,
            "": np.nan,
        }
    )
    if not allow_missing and mapped.isna().any():
        raise ValueError(f"{name} contains missing or invalid booleans")
    return mapped


def _wilson95_lower(wins: int, observations: int) -> float | None:
    if observations == 0:
        return None
    z = 1.959963984540054
    rate = wins / observations
    denominator = 1 + z * z / observations
    centre = rate + z * z / (2 * observations)
    margin = z * math.sqrt(
        rate * (1 - rate) / observations + z * z / (4 * observations * observations)
    )
    return float((centre - margin) / denominator)


def rule_mask(frame: pd.DataFrame, rule: ContextRule) -> pd.Series:
    columns = [f"{slug}_return_{rule.window_minutes}m" for slug in RETURN_SLUGS]
    availability = f"context_return_{rule.window_minutes}m_available"
    missing = set(columns + [availability]) - set(frame)
    if missing:
        raise ValueError(f"context mechanism features missing {sorted(missing)}")
    available = _as_bool(frame[availability], availability)
    values = frame[columns].apply(pd.to_numeric, errors="coerce")
    if values.loc[available].isna().any().any():
        raise ValueError("available context mechanism returns contain missing values")
    if rule.mode == "nifty50_positive":
        selected = values[columns[0]] > 0
    elif rule.mode == "majority_positive":
        selected = (values > 0).sum(axis=1) >= 2
    else:
        selected = (values > 0).all(axis=1)
    return (available & selected).astype(bool)


def _rule_available(frame: pd.DataFrame, rule: ContextRule) -> pd.Series:
    availability = f"context_return_{rule.window_minutes}m_available"
    if availability not in frame:
        raise ValueError(f"context mechanism features missing ['{availability}']")
    return _as_bool(frame[availability], availability).astype(bool)


def prepare_mechanism_population(
    features: pd.DataFrame,
    events: pd.DataFrame,
    *,
    sessions: list[str],
    protocol: ContextMechanismProtocol = DEFAULT_CONTEXT_MECHANISM_PROTOCOL,
) -> pd.DataFrame:
    if set(features) & OUTCOME_COLUMNS:
        raise ValueError("context feature artifact contains forbidden outcomes")
    required_features = {
        "event_id",
        "scrip_code",
        "session_date",
        "decision",
        "available_at",
        "context_joined",
    }
    required_events = (
        "event_id",
        "scrip_code",
        "session_date",
        "decision",
        "status",
        "strict_success",
        "net_r",
    )
    if required_features - set(features) or set(required_events) - set(events):
        raise ValueError("context mechanism inputs are missing required columns")
    if (
        features.event_id.isna().any()
        or events.event_id.isna().any()
        or features.event_id.duplicated().any()
        or events.event_id.duplicated().any()
    ):
        raise ValueError("context mechanism event identifiers must be present and unique")
    if len(sessions) != protocol.evaluation_sessions or sessions != sorted(set(sessions)):
        raise ValueError("context mechanism requires the frozen 120-session calendar")

    feature_copy = features.copy()
    feature_copy["context_joined"] = _as_bool(
        feature_copy.context_joined, "context_joined"
    )
    safe_events = events[list(required_events)].copy()
    population = feature_copy.merge(
        safe_events,
        on="event_id",
        how="outer",
        validate="one_to_one",
        suffixes=("_feature", "_event"),
        indicator=True,
    )
    if not population._merge.eq("both").all():
        raise ValueError("context feature and event populations differ")
    for name in ("scrip_code", "session_date", "decision"):
        if not population[f"{name}_feature"].astype(str).equals(
            population[f"{name}_event"].astype(str)
        ):
            raise ValueError(f"context mechanism {name} identity mismatch")
        population[name] = population.pop(f"{name}_feature")
        population.drop(columns=f"{name}_event", inplace=True)
    population.drop(columns="_merge", inplace=True)
    population = population.loc[population.decision == "TRADE"].copy()
    if len(population) != protocol.expected_trade_attempts:
        raise ValueError("context mechanism trade-attempt population changed")
    if not population.context_joined.all():
        raise ValueError("context mechanism cannot consume an excluded context join")
    if set(population.session_date.astype(str)) - set(sessions):
        raise ValueError("context mechanism event falls outside the frozen calendar")

    population["resolved"] = population.status.eq("resolved")
    population["strict_success"] = _as_bool(
        population.strict_success, "strict_success", allow_missing=True
    )
    population["net_r"] = pd.to_numeric(population.net_r, errors="coerce")
    resolved = population.resolved
    if population.loc[resolved, "strict_success"].isna().any() or population.loc[
        resolved, "net_r"
    ].isna().any():
        raise ValueError("resolved context outcomes are incomplete")
    if population.loc[~resolved, ["strict_success", "net_r"]].notna().any().any():
        raise ValueError("unresolved context attempts contain outcome values")
    population["strict_success"] = population.strict_success.eq(True)
    population = population.sort_values(
        ["session_date", "available_at", "event_id"]
    ).reset_index(drop=True)
    return population


def mechanism_metrics(
    population: pd.DataFrame, mask: pd.Series | np.ndarray, sessions: list[str]
) -> dict[str, Any]:
    selected = population.loc[np.asarray(mask, dtype=bool)]
    resolved = selected.loc[selected.resolved]
    wins = int(resolved.strict_success.sum())
    count = len(resolved)
    active = int(resolved.session_date.nunique())
    symbol_share = (
        float(resolved.scrip_code.value_counts(normalize=True).max()) if count else None
    )
    return {
        "attempts": len(selected),
        "resolved_fills": count,
        "unresolved_or_unfilled_attempts": int((~selected.resolved).sum()),
        "strict_wins": wins,
        "strict_success_rate": float(wins / count) if count else None,
        "wilson95_lower": _wilson95_lower(wins, count),
        "mean_net_r": float(resolved.net_r.mean()) if count else None,
        "active_sessions": active,
        "active_session_coverage": active / len(sessions),
        "zero_resolved_call_sessions": len(sessions) - active,
        "maximum_symbol_share": symbol_share,
    }


def _rotated_within_session(population: pd.DataFrame, mask: pd.Series) -> pd.Series:
    rotated = np.zeros(len(population), dtype=bool)
    raw = mask.to_numpy(dtype=bool)
    for positions in population.groupby("session_date", sort=False).indices.values():
        indices = np.asarray(positions, dtype=int)
        rotated[indices] = np.roll(raw[indices], -1)
    return pd.Series(rotated, index=population.index)


def _shuffled_masks(
    population: pd.DataFrame,
    mask: pd.Series,
    protocol: ContextMechanismProtocol,
):
    rng = np.random.default_rng(protocol.shuffle_seed)
    groups = [
        np.asarray(positions, dtype=int)
        for positions in population.groupby("session_date", sort=False).indices.values()
    ]
    raw = mask.to_numpy(dtype=bool)
    for repetition in range(protocol.shuffle_repetitions):
        shuffled = raw.copy()
        for indices in groups:
            shuffled[indices] = rng.permutation(raw[indices])
        yield repetition, pd.Series(shuffled, index=population.index)


def _finite_metric(metrics: dict[str, Any], name: str) -> float:
    value = metrics[name]
    return float(value) if value is not None and math.isfinite(float(value)) else -math.inf


def _shuffle_control(
    rows: list[dict], metric: str, observed: float
) -> tuple[float | None, float]:
    values = np.asarray(
        [
            float(row[metric])
            for row in rows
            if row[metric] is not None and math.isfinite(float(row[metric]))
        ],
        dtype=float,
    )
    if not math.isfinite(observed) or not len(values):
        return None, 1.0
    percentile = float(np.quantile(values, 0.95))
    empirical_p = float((1 + int((values >= observed).sum())) / (len(rows) + 1))
    return percentile, empirical_p


def evaluate_context_mechanism(
    population: pd.DataFrame,
    *,
    sessions: list[str],
    protocol: ContextMechanismProtocol = DEFAULT_CONTEXT_MECHANISM_PROTOCOL,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if len(population) != protocol.expected_trade_attempts:
        raise ValueError("context mechanism population changed")
    baseline = mechanism_metrics(population, np.ones(len(population), dtype=bool), sessions)
    if (
        baseline["resolved_fills"] != protocol.expected_resolved_fills
        or baseline["strict_wins"] != protocol.expected_strict_wins
        or not math.isclose(
            baseline["strict_success_rate"], protocol.expected_baseline_rate, abs_tol=1e-15
        )
        or not math.isclose(
            baseline["mean_net_r"], protocol.expected_baseline_mean_net_r, abs_tol=1e-12
        )
    ):
        raise ValueError("canonical AEM baseline identity changed")

    folds = [
        sessions[index : index + protocol.fold_sessions]
        for index in range(0, len(sessions), protocol.fold_sessions)
    ]
    trial_rows: list[dict] = []
    fold_rows: list[dict] = []
    shuffle_rows: list[dict] = []
    details: dict[str, Any] = {}
    for priority, rule in enumerate(protocol.rules):
        mask = rule_mask(population, rule)
        inverse_mask = _rule_available(population, rule) & ~mask
        placebo_mask = _rotated_within_session(population, mask)
        selected = mechanism_metrics(population, mask, sessions)
        inverse = mechanism_metrics(population, inverse_mask, sessions)
        placebo = mechanism_metrics(population, placebo_mask, sessions)
        rule_folds = []
        for fold_index, fold_sessions in enumerate(folds, start=1):
            in_fold = population.session_date.isin(fold_sessions)
            fold_baseline = mechanism_metrics(population, in_fold, fold_sessions)
            fold_selected = mechanism_metrics(population, in_fold & mask, fold_sessions)
            accuracy_delta = _finite_metric(fold_selected, "strict_success_rate") - _finite_metric(
                fold_baseline, "strict_success_rate"
            )
            net_r_delta = _finite_metric(fold_selected, "mean_net_r") - _finite_metric(
                fold_baseline, "mean_net_r"
            )
            row = {
                "rule_id": rule.id,
                "fold": fold_index,
                "first_session": fold_sessions[0],
                "last_session": fold_sessions[-1],
                "accuracy_delta": accuracy_delta,
                "mean_net_r_delta": net_r_delta,
                **{f"selected_{key}": value for key, value in fold_selected.items()},
            }
            fold_rows.append(row)
            rule_folds.append(row)

        rule_shuffles = []
        for repetition, shuffled_mask in _shuffled_masks(population, mask, protocol):
            metrics = mechanism_metrics(population, shuffled_mask, sessions)
            row = {"rule_id": rule.id, "repetition": repetition, **metrics}
            shuffle_rows.append(row)
            rule_shuffles.append(row)
        selected_accuracy = _finite_metric(selected, "strict_success_rate")
        selected_wilson = _finite_metric(selected, "wilson95_lower")
        selected_net_r = _finite_metric(selected, "mean_net_r")
        baseline_accuracy = _finite_metric(baseline, "strict_success_rate")
        baseline_wilson = _finite_metric(baseline, "wilson95_lower")
        baseline_net_r = _finite_metric(baseline, "mean_net_r")
        accuracy_p95, accuracy_p = _shuffle_control(
            rule_shuffles, "strict_success_rate", selected_accuracy
        )
        net_r_p95, net_r_p = _shuffle_control(
            rule_shuffles, "mean_net_r", selected_net_r
        )
        accuracy_delta = selected_accuracy - baseline_accuracy
        net_r_delta = selected_net_r - baseline_net_r
        gates = {
            "minimum_resolved_fills": selected["resolved_fills"]
            >= protocol.minimum_resolved_fills,
            "minimum_active_sessions": selected["active_sessions"]
            >= protocol.minimum_active_sessions,
            "minimum_active_session_coverage": selected["active_session_coverage"]
            >= protocol.minimum_active_session_coverage,
            "strict_accuracy_improves": accuracy_delta > 0,
            "wilson_lower_improves": selected_wilson > baseline_wilson,
            "mean_net_r_positive": selected_net_r > 0,
            "mean_net_r_improves": net_r_delta > 0,
            "all_folds_accuracy_and_net_r_improve": all(
                row["accuracy_delta"] > 0 and row["mean_net_r_delta"] > 0
                for row in rule_folds
            ),
            "beats_inverse_accuracy": selected_accuracy
            > _finite_metric(inverse, "strict_success_rate"),
            "beats_inverse_net_r": selected_net_r
            > _finite_metric(inverse, "mean_net_r"),
            "beats_temporal_placebo_accuracy": selected_accuracy
            > _finite_metric(placebo, "strict_success_rate"),
            "beats_temporal_placebo_net_r": selected_net_r
            > _finite_metric(placebo, "mean_net_r"),
            "beats_shuffle_accuracy_p95": accuracy_p95 is not None
            and selected_accuracy > accuracy_p95,
            "beats_shuffle_net_r_p95": net_r_p95 is not None
            and selected_net_r > net_r_p95,
            "shuffle_accuracy_empirical_p": accuracy_p
            <= protocol.maximum_shuffle_empirical_p,
            "shuffle_net_r_empirical_p": net_r_p <= protocol.maximum_shuffle_empirical_p,
        }
        passed = all(gates.values())
        trial_rows.append(
            {
                "rule_id": rule.id,
                "priority": priority,
                "window_minutes": rule.window_minutes,
                "mode": rule.mode,
                **selected,
                "accuracy_delta": accuracy_delta,
                "mean_net_r_delta": net_r_delta,
                "inverse_accuracy": inverse["strict_success_rate"],
                "inverse_mean_net_r": inverse["mean_net_r"],
                "placebo_accuracy": placebo["strict_success_rate"],
                "placebo_mean_net_r": placebo["mean_net_r"],
                "shuffle_accuracy_p95": accuracy_p95,
                "shuffle_mean_net_r_p95": net_r_p95,
                "shuffle_accuracy_empirical_p": accuracy_p,
                "shuffle_net_r_empirical_p": net_r_p,
                "passed": passed,
                "failed_gates": json.dumps([name for name, value in gates.items() if not value]),
            }
        )
        details[rule.id] = {
            "selected": selected,
            "inverse": inverse,
            "temporal_placebo": placebo,
            "accuracy_delta": accuracy_delta,
            "mean_net_r_delta": net_r_delta,
            "shuffle_accuracy_p95": accuracy_p95,
            "shuffle_mean_net_r_p95": net_r_p95,
            "shuffle_accuracy_empirical_p": accuracy_p,
            "shuffle_net_r_empirical_p": net_r_p,
            "gates": gates,
            "passed": passed,
        }

    trials = pd.DataFrame(trial_rows).sort_values("priority").reset_index(drop=True)
    qualified = trials.loc[trials.passed]
    if qualified.empty:
        selected_rule = None
    else:
        selected_rule = (
            qualified.sort_values(
                ["wilson95_lower", "mean_net_r", "resolved_fills", "priority"],
                ascending=[False, False, False, True],
            )
            .iloc[0]
            .rule_id
        )
    summary = {
        "baseline": baseline,
        "rules_tested": len(trials),
        "qualified_rules": qualified.rule_id.tolist(),
        "selected_rule": selected_rule,
        "mechanism_passed": selected_rule is not None,
        "trials": details,
    }
    return trials, pd.DataFrame(fold_rows), pd.DataFrame(shuffle_rows), summary


def freeze_context_mechanism_protocol(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    protocol: ContextMechanismProtocol = DEFAULT_CONTEXT_MECHANISM_PROTOCOL,
) -> dict:
    root, output = Path(root).resolve(), Path(output).resolve()
    plan_path = root / "docs/plan-aem-index-context-accuracy.md"
    integrity_path = root / "docs/evidence/aem-context-integrity.json"
    test_path = root / "tests_lab/test_aem_context_mechanism.py"
    for path in (plan_path, integrity_path, test_path):
        if not path.is_file():
            raise ValueError(f"required mechanism protocol input is missing: {path.name}")
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    if integrity.get("decision", {}).get("price_context_ready_for_mechanism_check") is not True:
        raise ValueError("price context integrity did not authorize the mechanism check")
    if integrity.get("decision", {}).get("vwap_context_ready") is not False:
        raise ValueError("mechanism v1 expects VWAP context to remain unavailable")

    run_id = uuid4().hex
    target = output / "aem_context/mechanism/protocol/runs" / run_id
    target.mkdir(parents=True, exist_ok=False)
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "protocol_frozen_no_outcome_evaluation",
        "milestone": "AEM index context checkpoint 2: mechanism protocol",
        "eligible_for_live": False,
        "baseline_improved": False,
        "mechanism_evaluated": False,
        "selector_evaluated": False,
        "protocol": protocol.to_dict(),
        "protocol_sha256": protocol.sha256,
        "integrity_run_id": integrity["run_id"],
        "integrity_features_sha256": integrity["context_features_sha256"],
        "source": {
            "plan_sha256": digest(plan_path),
            "integrity_evidence_sha256": digest(integrity_path),
            "implementation_sha256": digest(Path(__file__)),
            "tests_sha256": digest(test_path),
        },
        "decision": {
            "run_mechanism_once": True,
            "run_selector": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "change_dashboard": False,
            "reason": "Rules and controls are frozen before real outcomes are joined.",
        },
    }
    report_path = target / "report.json"
    write_json(report_path, report)
    write_json(
        output / "aem_context/mechanism/protocol/latest.json",
        {"id": run_id, "path": str(report_path), "status": report["status"]},
    )
    return report


def run_real_context_mechanism(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_id: str,
    integrity_run_id: str,
    protocol: ContextMechanismProtocol = DEFAULT_CONTEXT_MECHANISM_PROTOCOL,
) -> dict:
    """Open real outcomes once under the already-frozen protocol."""

    root, output = Path(root).resolve(), Path(output).resolve()
    protocol_latest = output / "aem_context/mechanism/protocol/latest.json"
    if not protocol_latest.is_file():
        raise ValueError("context mechanism protocol has not been frozen")
    protocol_pointer = json.loads(protocol_latest.read_text(encoding="utf-8"))
    protocol_report_path = Path(protocol_pointer["path"])
    protocol_report = json.loads(protocol_report_path.read_text(encoding="utf-8"))
    test_path = root / "tests_lab/test_aem_context_mechanism.py"
    plan_path = root / "docs/plan-aem-index-context-accuracy.md"
    integrity_evidence_path = root / "docs/evidence/aem-context-integrity.json"
    if protocol_report.get("protocol_sha256") != protocol.sha256:
        raise ValueError("context mechanism protocol fingerprint changed")
    frozen_sources = protocol_report.get("source", {})
    current_sources = {
        "plan_sha256": digest(plan_path),
        "integrity_evidence_sha256": digest(integrity_evidence_path),
        "implementation_sha256": digest(Path(__file__)),
        "tests_sha256": digest(test_path),
    }
    if frozen_sources != current_sources:
        raise ValueError("context mechanism implementation or evidence changed after freeze")
    integrity_target = output / "aem_context/integrity/runs" / integrity_run_id
    features_path = integrity_target / "context_features.csv"
    integrity_report_path = integrity_target / "report.json"
    dataset = output / "aem_staged/datasets" / dataset_id
    events_path = dataset / "events.csv"
    manifest_path = dataset / "manifest.json"
    context_plan_path = output / "aem_history/context" / dataset_id / "plan.json"
    for path in (
        features_path,
        integrity_report_path,
        events_path,
        manifest_path,
        context_plan_path,
        test_path,
    ):
        if not path.is_file():
            raise ValueError(f"required real mechanism input is missing: {path.name}")
    integrity_report = json.loads(integrity_report_path.read_text(encoding="utf-8"))
    if integrity_report.get("dataset_id") != dataset_id:
        raise ValueError("context integrity dataset differs from mechanism dataset")
    integrity_ready = integrity_report.get("decision", {}).get(
        "price_context_ready_for_mechanism_check"
    )
    if integrity_ready is not True:
        raise ValueError("context integrity did not authorize the mechanism check")
    if digest(features_path) != integrity_report["artifacts"]["context_features_sha256"]:
        raise ValueError("context feature artifact fingerprint changed")
    context_plan = json.loads(context_plan_path.read_text(encoding="utf-8"))
    sessions = context_plan["sessions"]
    features = pd.read_csv(features_path, keep_default_na=False)
    events = pd.read_csv(events_path, keep_default_na=False)
    population = prepare_mechanism_population(
        features, events, sessions=sessions, protocol=protocol
    )
    trials, folds, shuffles, summary = evaluate_context_mechanism(
        population, sessions=sessions, protocol=protocol
    )

    run_id = uuid4().hex
    target = output / "aem_context/mechanism/runs" / run_id
    target.mkdir(parents=True, exist_ok=False)
    population_path = target / "population.csv"
    trials_path = target / "trials.csv"
    folds_path = target / "folds.csv"
    shuffles_path = target / "shuffled_controls.csv"
    population.to_csv(population_path, index=False, float_format="%.12g", na_rep="")
    trials.to_csv(trials_path, index=False, float_format="%.12g", na_rep="")
    folds.to_csv(folds_path, index=False, float_format="%.12g", na_rep="")
    shuffles.to_csv(shuffles_path, index=False, float_format="%.12g", na_rep="")
    passed = bool(summary["mechanism_passed"])
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "mechanism_present_selector_allowed" if passed else "mechanism_absent_stop",
        "milestone": "AEM index context checkpoint 2: real mechanism test",
        "eligible_for_live": False,
        "baseline_improved": False,
        "mechanism_evaluated": True,
        "mechanism_passed": passed,
        "selector_evaluated": False,
        "dataset_id": dataset_id,
        "integrity_run_id": integrity_run_id,
        "protocol_run_id": protocol_report["id"],
        "protocol_sha256": protocol.sha256,
        "evidence_class": protocol.evidence_class,
        "summary": summary,
        "source": {
            "protocol_report_sha256": digest(protocol_report_path),
            "integrity_report_sha256": digest(integrity_report_path),
            "features_sha256": digest(features_path),
            "events_sha256": digest(events_path),
            "manifest_sha256": digest(manifest_path),
            "context_plan_sha256": digest(context_plan_path),
            "implementation_sha256": digest(Path(__file__)),
            "tests_sha256": digest(test_path),
        },
        "artifacts": {
            "population_sha256": digest(population_path),
            "trials_sha256": digest(trials_path),
            "folds_sha256": digest(folds_path),
            "shuffled_controls_sha256": digest(shuffles_path),
        },
        "decision": {
            "proceed_to_selector_race": passed,
            "register_candidate": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "change_dashboard": False,
            "reason": (
                "At least one preregistered rule passed every mechanism and economic gate; "
                "a later bounded selector diagnostic is allowed."
                if passed
                else "No preregistered rule passed every mechanism, placebo, fold, sample, "
                "and economic gate; stop before any selector race."
            ),
        },
    }
    report_path = target / "report.json"
    write_json(report_path, report)
    write_json(
        output / "aem_context/mechanism/latest.json",
        {"id": run_id, "path": str(report_path), "status": report["status"]},
    )
    return report
