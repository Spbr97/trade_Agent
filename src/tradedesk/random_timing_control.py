"""Prospective, matched-prior timing control for one frozen selector/model.

Each accepted real call is a winner frozen by one selector contract before its outcome.
Its controls are the twenty nearest *earlier sealed qualified calls* for the same symbol,
setup, regime and feature/strategy contract.  Those controls therefore passed the agent's
historical eligibility process; arbitrary dates are never treated as tradeable calls.

The test has one terminal look at 100 unique sessions.  The twenty control ranks are
enumerated as common temporal shifts across all calls, preserving shared market/serial
dependence instead of pretending that per-call random draws are independent.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from tradedesk.engine.indicators import atr
from tradedesk.evidence import CONTRACTS, OutcomeState
from tradedesk.outcome_resolver import DeterministicOutcome, resolve_versioned_call
from tradedesk.precision_selector import PRIMARY_SELECTOR_POLICY, SELECTOR_VERSION
from tradedesk.prediction_ledger import canonical_sha256, seal_prediction

CONTROL_VERSION = "matched-prior-eligible-timing-v3"
ACCUMULATOR_VERSION = "matched-random-timing-ledger-v2"
SELECTION_CONTRACT_VERSION = "timing-selection-contract-v1"
SELECTION_MANIFEST_VERSION = "timing-selection-manifest-v1"
ALTERNATIVE_SESSIONS = 20
N_COHORTS = ALTERNATIVE_SESSIONS
TERMINAL_PAIRED_CALLS = 100
MINIMUM_PAIRED_CALLS = TERMINAL_PAIRED_CALLS
MINIMUM_ACTIVE_SESSIONS = 100
MINIMUM_ADVANTAGE_R = 0.10
MAXIMUM_EMPIRICAL_RANK_TAIL = 0.05
FLOAT_ABS_TOLERANCE = 1e-8
FLOAT_REL_TOLERANCE = 1e-9

BarsLoader = Callable[[str], pd.DataFrame]


class PendingTimingOutcome(ValueError):
    """The frozen winner/control has not reached a terminal ledger state yet."""


def control_configuration() -> dict[str, Any]:
    return {
        "version": CONTROL_VERSION,
        "selector_version": SELECTOR_VERSION,
        "selector_policy": PRIMARY_SELECTOR_POLICY,
        "alternative_sessions": ALTERNATIVE_SESSIONS,
        "cohort_design": "enumerated_common_prior_eligible_rank",
        "terminal_paired_calls": TERMINAL_PAIRED_CALLS,
        "minimum_active_sessions": MINIMUM_ACTIVE_SESSIONS,
        "minimum_advantage_r": MINIMUM_ADVANTAGE_R,
        "maximum_empirical_rank_tail_probability": MAXIMUM_EMPIRICAL_RANK_TAIL,
        "inferential_p_value_available": False,
        "looks": 1,
        "actual_call_replayed": True,
        "no_fill_return_r": 0.0,
        "population": "prospectively_frozen_top1_per_session",
        "matching": (
            "same_symbol_setup_regime_contract_digest_strategy_feature_model_and_"
            "historical_qualified_eligibility"
        ),
        "source_replay_mode": (
            "historical_database_snapshot_hashes_only_candle_frames_not_embedded"
        ),
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _float_matches(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    left, right = _finite(expected), _finite(actual)
    return bool(
        left is not None
        and right is not None
        and math.isclose(
            left,
            right,
            rel_tol=FLOAT_REL_TOLERANCE,
            abs_tol=FLOAT_ABS_TOLERANCE,
        )
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _frame_hash(frame: pd.DataFrame) -> str:
    values = [
        [
            pd.Timestamp(str(stamp)).isoformat(),
            *(
                None if pd.isna(value) else float(value)
                for value in row[["open", "high", "low", "close", "volume"]]
            ),
        ]
        for stamp, row in frame.iterrows()
    ]
    return canonical_sha256(values)


def _database_path(market: str) -> Path:
    return {
        "nse": Path("data/tradedesk.duckdb"),
        "bse": Path("data/bse.duckdb"),
        "crypto": Path("data/crypto.duckdb"),
    }[market]


def _duckdb_loader(path: Path) -> tuple[duckdb.DuckDBPyConnection, BarsLoader]:
    connection = duckdb.connect(str(path), read_only=True)

    def load(code: str) -> pd.DataFrame:
        frame = connection.execute(
            "SELECT ts,open,high,low,close,volume FROM candles "
            "WHERE scrip_code=? AND interval='1day' ORDER BY ts",
            [code],
        ).df()
        if frame.empty:
            return frame
        index = pd.to_datetime(frame.pop("ts"), unit="s", utc=True).dt.tz_convert(
            "Asia/Kolkata"
        )
        frame.index = pd.DatetimeIndex(index)
        return frame[~frame.index.duplicated(keep="last")].sort_index()

    return connection, load


def candle_source_identity(
    rows: Sequence[Mapping[str, Any]], bars_loader: BarsLoader | None
) -> dict[str, Any]:
    """Hash current full candle inputs before experiment-cache lookup."""

    if bars_loader is None:
        return {
            "status": "unavailable",
            "sha256": canonical_sha256({"status": "unavailable", "codes": []}),
        }
    sources: list[dict[str, str]] = []
    try:
        codes = sorted(
            {
                str((row.get("timing_spec") or {}).get("scrip_code") or "")
                for row in rows
            }
            - {""}
        )
        for code in codes:
            bars = bars_loader(code)
            required = {"open", "high", "low", "close", "volume"}
            if bars.empty or not required.issubset(bars.columns):
                raise ValueError(f"daily candle source is unavailable for {code}")
            if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
                raise ValueError(f"daily candle source is unordered or duplicated for {code}")
            sources.append({"scrip_code": code, "sha256": _frame_hash(bars)})
    except (KeyError, TypeError, ValueError, OSError) as exc:
        payload = {"status": "invalid", "reason": str(exc), "sources": sources}
        return {**payload, "sha256": canonical_sha256(payload)}
    payload = {"status": "available", "sources": sources}
    return {"status": "available", "sha256": canonical_sha256(payload)}


def _unavailable(
    reason: str,
    *,
    status: str,
    market: str,
    contract_version: str,
    selection_context_sha256: str | None = None,
    awaiting_outcomes: int = 0,
    insufficient_matched_history: int = 0,
) -> dict[str, Any]:
    return {
        "version": CONTROL_VERSION,
        "status": status,
        "reason": reason,
        "market": market,
        "contract_version": contract_version,
        "selector_policy": PRIMARY_SELECTOR_POLICY,
        "selector_version": SELECTOR_VERSION,
        "selection_context_sha256": selection_context_sha256,
        "control_configuration": control_configuration(),
        "paired_calls": 0,
        "active_sessions": 0,
        "awaiting_selected_outcomes": awaiting_outcomes,
        "insufficient_matched_history": insufficient_matched_history,
        "records": [],
        "markets_pooled": False,
        "contracts_pooled": False,
        "random_timing_gate_passed": False,
        "terminal_evaluation_locked": False,
        "eligible_for_live": False,
        "active_model_changed": False,
    }


def _validate_sealed_object(
    record: Mapping[str, Any], *, version: str, label: str
) -> str:
    digest = record.get("record_sha256")
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    if record.get("version") != version or not digest or digest != canonical_sha256(payload):
        raise ValueError(f"invalid {label} integrity contract")
    return str(digest)


def _validate_selection_binding(
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str,
    contract_version: str,
    selection_context: Mapping[str, Any],
    selection_manifest: Mapping[str, Any],
) -> str:
    context_sha = _validate_sealed_object(
        selection_context, version=SELECTION_CONTRACT_VERSION, label="timing selection"
    )
    _validate_sealed_object(
        selection_manifest,
        version=SELECTION_MANIFEST_VERSION,
        label="timing selection manifest",
    )
    source_cohort = selection_context.get("source_cohort")
    if (
        selection_context.get("market") != market
        or selection_context.get("contract_version") != contract_version
        or selection_context.get("selector_policy") != PRIMARY_SELECTOR_POLICY
        or selection_context.get("selector_version") != SELECTOR_VERSION
        or selection_context.get("control_configuration_sha256")
        != canonical_sha256(control_configuration())
        or selection_manifest.get("selection_context_sha256") != context_sha
        or selection_manifest.get("market") != market
        or selection_manifest.get("contract_version") != contract_version
        or not isinstance(source_cohort, Mapping)
        or source_cohort.get("contract_version") != contract_version
        or any(
            row.get(name) != expected
            for row in rows
            for name, expected in source_cohort.items()
        )
    ):
        raise ValueError("timing selection binding scope/configuration mismatch")
    sessions = [str(row.get("armed_on") or "") for row in rows]
    if len(sessions) != len(set(sessions)):
        raise ValueError("top_1_per_session produced duplicate armed sessions")
    selected_ids = sorted(str(row.get("signal_id") or "") for row in rows)
    if (
        not all(selected_ids)
        or len(selected_ids) != len(set(selected_ids))
        or sorted(selection_manifest.get("selected_signal_ids") or []) != selected_ids
    ):
        raise ValueError("timing selection manifest does not match selected rows")
    if sorted(selection_manifest.get("selected_sessions") or []) != sorted(sessions):
        raise ValueError("timing selection manifest does not match selected sessions")
    starts_after = str(selection_context.get("starts_after") or "")
    if not starts_after or any(session <= starts_after for session in sessions):
        raise ValueError("timing evidence predates its prospective selection contract")
    return context_sha


def _validate_row_shape(
    row: Mapping[str, Any], *, market: str, contract_version: str
) -> tuple[str, dict[str, Any], Mapping[str, Any]]:
    if row.get("contract_version") != contract_version or row.get("market") != market:
        raise ValueError("timing opportunity scope mismatch")
    timing_spec = row.get("timing_spec")
    if not isinstance(timing_spec, Mapping):
        raise ValueError("sealed candle-replay timing specification is unavailable")
    payload = timing_spec.get("prediction_payload")
    if not isinstance(payload, dict):
        raise ValueError("sealed prediction payload is unavailable")
    if seal_prediction(payload) != row.get("prediction_sha256"):
        raise ValueError("prediction seal does not match timing specification")
    source_snapshot = payload.get("source_snapshot")
    if not isinstance(source_snapshot, Mapping) or payload.get(
        "source_snapshot_sha256"
    ) != canonical_sha256(source_snapshot):
        raise ValueError("sealed source snapshot integrity mismatch")
    code = str(timing_spec.get("scrip_code") or "")
    levels = payload.get("levels") or {}
    instrument = payload.get("instrument") or {}
    contract = payload.get("contract") or {}
    versions = payload.get("versions") or {}
    entries = levels.get("entry_range") or []
    targets = levels.get("targets") or []
    if (
        not code
        or payload.get("market") != market
        or str(instrument.get("scrip_code") or "") != code
        or str(levels.get("armed_on") or "") != str(row.get("armed_on") or "")
        or not entries
        or len(targets) < 2
        or _finite(entries[0]) != _finite(timing_spec.get("entry"))
        or _finite(levels.get("stop")) != _finite(timing_spec.get("stop"))
        or _finite(targets[0]) != _finite(timing_spec.get("t1"))
        or _finite(targets[1]) != _finite(timing_spec.get("t2"))
        or str((payload.get("contract") or {}).get("version") or "")
        != contract_version
        or row.get("evidence_role") != "recommended"
        or str(row.get("contract_sha256") or "")
        != str(contract.get("sha256") or "")
        or row.get("strategy_version") != versions.get("strategy")
        or row.get("feature_version") != versions.get("feature_contract")
        or row.get("model_version") != versions.get("model")
    ):
        raise ValueError("timing specification does not match sealed prediction")
    return code, payload, source_snapshot


def _reconcile_actual(row: Mapping[str, Any], outcome: DeterministicOutcome) -> None:
    expected_state = row.get("outcome_state")
    if expected_state == OutcomeState.PENDING_CALL.value:
        raise PendingTimingOutcome("frozen timing winner is still pending")
    if expected_state == OutcomeState.INVALID_CALL.value:
        raise ValueError("frozen timing winner has an invalid terminal outcome")
    if expected_state not in {
        OutcomeState.RESOLVED_CALL.value,
        OutcomeState.NEVER_TRIGGERED.value,
    }:
        raise ValueError("timing opportunity outcome state is unavailable")
    exact_fields: dict[str, Any] = {
        "outcome_state": outcome.outcome_state,
        "outcome": outcome.outcome,
        "label": outcome.label,
        "entry_on": outcome.entry_on,
        "exit_on": outcome.exit_on,
    }
    for field, replayed in exact_fields.items():
        if row.get(field) != replayed:
            raise ValueError(f"actual candle replay mismatch: {field}")
    float_fields: dict[str, Any] = {
        "actual_entry_price": outcome.entry_price,
        "exit_price": outcome.exit_price,
        "net_r": outcome.net_r,
    }
    for field, replayed in float_fields.items():
        if not _float_matches(row.get(field), replayed):
            raise ValueError(f"actual candle replay mismatch: {field}")


def _replay(
    row: Mapping[str, Any],
    *,
    market: str,
    contract_version: str,
    load_bars: BarsLoader,
) -> tuple[float, dict[str, Any]]:
    code, payload, source_snapshot = _validate_row_shape(
        row, market=market, contract_version=contract_version
    )
    if row.get("outcome_state") == OutcomeState.PENDING_CALL.value:
        raise PendingTimingOutcome("frozen timing opportunity is still pending")
    bars = load_bars(code)
    required = {"open", "high", "low", "close", "volume"}
    if bars.empty or not required.issubset(bars.columns):
        raise ValueError("daily candle source is unavailable")
    if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError("daily candle source is unordered or duplicated")
    dates = list(pd.DatetimeIndex(bars.index).date)
    armed = pd.Timestamp(str(row["armed_on"])).date()
    if armed not in dates:
        raise ValueError("real arming session is absent from candle source")
    armed_index = dates.index(armed)
    source_row = source_snapshot.get("source_feature_row") or {}
    source_atr = _finite(source_row.get("atr", source_row.get("atr14")))
    source_close = _finite(source_row.get("close"))
    replayed_atr = _finite(atr(bars.iloc[: armed_index + 1], 14).iloc[-1])
    replayed_close = _finite(bars.iloc[armed_index]["close"])
    if (
        source_atr is None
        or source_atr <= 0
        or replayed_atr is None
        or replayed_atr <= 0
        or source_close is None
        or replayed_close is None
        or not _float_matches(source_atr, replayed_atr)
        or not _float_matches(source_close, replayed_close)
        or str(source_snapshot.get("as_of_session") or "") != armed.isoformat()
    ):
        raise ValueError("sealed armed-session close/ATR does not match candle source")
    entry = _finite((row.get("timing_spec") or {}).get("entry"))
    stop = _finite((row.get("timing_spec") or {}).get("stop"))
    if entry is None or stop is None or entry <= stop:
        raise ValueError("sealed call geometry is invalid")
    call = SimpleNamespace(
        prediction_payload=payload,
        market=market,
        contract_version=contract_version,
        armed_on=armed.isoformat(),
        entry=entry,
        stop=stop,
    )
    outcome = resolve_versioned_call(call, bars)
    if outcome is None:
        raise PendingTimingOutcome("frozen timing opportunity is unexpectedly immature")
    if outcome.outcome_state == OutcomeState.INVALID_CALL.value:
        raise ValueError(f"timing resolver rejected source: {outcome.first_event}")
    _reconcile_actual(row, outcome)
    if outcome.exit_on is None:
        raise ValueError("terminal replay has no exit session")
    exit_day = pd.Timestamp(outcome.exit_on).date()
    if exit_day not in dates:
        raise ValueError("terminal exit session is absent from candle source")
    exit_index = dates.index(exit_day)
    net_r = (
        float(outcome.net_r)
        if outcome.outcome_state == OutcomeState.RESOLVED_CALL.value
        and outcome.net_r is not None
        else 0.0
    )
    return net_r, {
        "signal_id": str(row["signal_id"]),
        "prediction_sha256": str(row["prediction_sha256"]),
        "scrip_code": code,
        "armed_on": str(row["armed_on"]),
        "entry_on": outcome.entry_on,
        "exit_on": outcome.exit_on,
        "outcome_state": outcome.outcome_state,
        "outcome": outcome.outcome,
        "net_r": net_r,
        # ATR is recursively calculated from the beginning of the stored series. Hashing
        # from row zero through exit covers every candle that can influence replay.
        "source_range": {
            "first_session": pd.Timestamp(bars.index[0]).date().isoformat(),
            "last_session": outcome.exit_on,
        },
        "source_sha256": _frame_hash(bars.iloc[: exit_index + 1]),
    }


def _matching_history(
    row: Mapping[str, Any],
    historical: Sequence[Mapping[str, Any]],
    *,
    starts_after: str,
) -> list[Mapping[str, Any]]:
    fields = (
        "scrip_code",
        "setup",
        "regime",
        "contract_version",
        "contract_sha256",
        "strategy_version",
        "feature_version",
        "model_version",
    )
    matches = [
        candidate
        for candidate in historical
        if candidate.get("evidence_role") == "recommended"
        and str(candidate.get("armed_on") or "") <= starts_after
        and str(candidate.get("armed_on") or "") < str(row.get("armed_on") or "")
        and all(candidate.get(field) == row.get(field) for field in fields)
    ]
    matches.sort(
        key=lambda candidate: (
            str(candidate.get("armed_on") or ""),
            str(candidate.get("signal_id") or ""),
        ),
        reverse=True,
    )
    return matches[:ALTERNATIVE_SESSIONS]


def _seal_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    return {**payload, "record_sha256": canonical_sha256(payload)}


def _validate_record(
    record: Mapping[str, Any],
    *,
    market: str,
    contract_version: str,
    selection_context_sha256: str,
) -> None:
    digest = record.get("record_sha256")
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    if not digest or digest != canonical_sha256(payload):
        raise ValueError("random-timing record integrity hash mismatch")
    if (
        record.get("control_version") != CONTROL_VERSION
        or record.get("market") != market
        or record.get("contract_version") != contract_version
        or record.get("selector_policy") != PRIMARY_SELECTOR_POLICY
        or record.get("selection_context_sha256") != selection_context_sha256
    ):
        raise ValueError("random-timing record scope mismatch")


def summarize_random_timing_records(
    records: Sequence[Mapping[str, Any]],
    *,
    market: str,
    contract_version: str,
    selection_context_sha256: str,
) -> dict[str, Any]:
    """Evaluate the fixed first-100 terminal cohort exactly once."""

    if not records:
        return _unavailable(
            "no valid prospectively frozen timing pairs are available",
            status="collecting_insufficient_evidence",
            market=market,
            contract_version=contract_version,
            selection_context_sha256=selection_context_sha256,
        )
    ordered = sorted(
        (dict(record) for record in records),
        key=lambda record: (str(record.get("armed_on")), str(record.get("signal_id"))),
    )
    signal_ids: set[str] = set()
    sessions: set[str] = set()
    for record in ordered:
        _validate_record(
            record,
            market=market,
            contract_version=contract_version,
            selection_context_sha256=selection_context_sha256,
        )
        signal_id = str(record.get("signal_id") or "")
        session = str(record.get("armed_on") or "")
        if not signal_id or signal_id in signal_ids:
            raise ValueError("random-timing records have missing or duplicate signal ids")
        if not session or session in sessions:
            raise ValueError("random-timing records violate top_1_per_session")
        signal_ids.add(signal_id)
        sessions.add(session)
        alternatives = record.get("alternatives")
        if not isinstance(alternatives, list) or len(alternatives) != N_COHORTS:
            raise ValueError("matched prior timing alternatives are incomplete")
        if [int(item.get("rank", 0)) for item in alternatives] != list(
            range(1, N_COHORTS + 1)
        ):
            raise ValueError("matched prior timing ranks are invalid")
        if _finite(record.get("actual_net_r")) is None or not _is_sha256(
            record.get("source_sha256")
        ):
            raise ValueError("random-timing actual replay identity is invalid")
        alternative_ids = [str(item.get("signal_id") or "") for item in alternatives]
        if (
            not all(alternative_ids)
            or len(alternative_ids) != len(set(alternative_ids))
            or any(_finite(item.get("net_r")) is None for item in alternatives)
            or any(not _is_sha256(item.get("source_sha256")) for item in alternatives)
            or any(
                not str(item.get("exit_on") or "")
                or str(item.get("exit_on")) >= session
                for item in alternatives
            )
        ):
            raise ValueError("matched prior timing replay identities are invalid")

    terminal = len(ordered) >= TERMINAL_PAIRED_CALLS
    evaluated = ordered[:TERMINAL_PAIRED_CALLS]
    actual = np.asarray([float(record["actual_net_r"]) for record in evaluated])
    rank_means = np.asarray(
        [
            np.mean([float(record["alternatives"][rank]["net_r"]) for record in evaluated])
            for rank in range(N_COHORTS)
        ],
        dtype=float,
    )
    actual_mean = float(actual.mean())
    random_mean = float(rank_means.mean())
    advantage = actual_mean - random_mean
    rank_tail_probability = float(
        (np.sum(rank_means >= actual_mean) + 1) / (N_COHORTS + 1)
    )
    checks = {
        "terminal_sample_reached": terminal,
        "paired_calls": len(evaluated) == TERMINAL_PAIRED_CALLS,
        "active_sessions": len(evaluated) >= MINIMUM_ACTIVE_SESSIONS,
        "advantage_r": advantage >= MINIMUM_ADVANTAGE_R,
        "empirical_rank_tail_probability": (
            rank_tail_probability <= MAXIMUM_EMPIRICAL_RANK_TAIL
        ),
        "single_terminal_look": terminal,
    }
    status = (
        "random_timing_pass"
        if terminal and all(checks.values())
        else "random_timing_fail"
        if terminal
        else "collecting_insufficient_evidence"
    )
    source_manifest = [
        {
            "signal_id": record["signal_id"],
            "source_sha256": record["source_sha256"],
            "record_sha256": record["record_sha256"],
        }
        for record in evaluated
    ]
    report: dict[str, Any] = {
        "version": CONTROL_VERSION,
        "status": status,
        "market": market,
        "contract_version": contract_version,
        "selector_policy": PRIMARY_SELECTOR_POLICY,
        "selector_version": SELECTOR_VERSION,
        "selection_context_sha256": selection_context_sha256,
        "control_configuration": control_configuration(),
        "paired_calls": len(evaluated),
        "observed_pairs_total": len(ordered),
        "active_sessions": len(evaluated),
        "actual_fills": sum(
            record.get("actual_outcome_state") == OutcomeState.RESOLVED_CALL.value
            for record in evaluated
        ),
        "actual_no_fills": sum(
            record.get("actual_outcome_state") == OutcomeState.NEVER_TRIGGERED.value
            for record in evaluated
        ),
        "model_expectancy_r": actual_mean,
        "matched_prior_timing_expectancy_r_mean": random_mean,
        "matched_prior_timing_expectancy_r_std": float(rank_means.std()),
        # Compatibility name used by the dashboard and promotion gate.
        "random_timing_expectancy_r_mean": random_mean,
        "random_timing_expectancy_r_std": float(rank_means.std()),
        "timing_advantage_r": advantage,
        "empirical_rank_tail_probability": rank_tail_probability,
        "p_value": None,
        "inferential_p_value_available": False,
        "statistical_interpretation": (
            "fixed empirical tail rank across matched historical controls; not an "
            "exchangeability-based inferential p-value"
        ),
        "gate_checks": checks,
        "n_cohorts": N_COHORTS,
        "alternative_sessions": ALTERNATIVE_SESSIONS,
        "cohort_design": "enumerated_common_prior_eligible_rank",
        "control_scope": "same_symbol_matched_prior_qualified_calls",
        "population": "prospectively_frozen_top1_per_session",
        "matching": control_configuration()["matching"],
        "actual_and_placebo_no_fill_return_r": 0.0,
        "source_replay_mode": control_configuration()["source_replay_mode"],
        "source_manifest_sha256": canonical_sha256(source_manifest),
        "records": evaluated,
        "markets_pooled": False,
        "contracts_pooled": False,
        "random_timing_gate_passed": status == "random_timing_pass",
        "terminal_evaluation_locked": terminal,
        "eligible_for_live": False,
        "active_model_changed": False,
    }
    report["replay_sha256"] = canonical_sha256(report)
    return report


def _evaluate(
    rows: Sequence[Mapping[str, Any]],
    *,
    historical_opportunities: Sequence[Mapping[str, Any]],
    market: str,
    contract_version: str,
    selection_context: Mapping[str, Any],
    selection_manifest: Mapping[str, Any],
    load_bars: BarsLoader,
) -> dict[str, Any]:
    context_sha = _validate_selection_binding(
        rows,
        market=market,
        contract_version=contract_version,
        selection_context=selection_context,
        selection_manifest=selection_manifest,
    )
    if not rows:
        return _unavailable(
            "the frozen selector has no post-registration winners yet",
            status="collecting_insufficient_evidence",
            market=market,
            contract_version=contract_version,
            selection_context_sha256=context_sha,
        )
    starts_after = str(selection_context["starts_after"])
    records: list[dict[str, Any]] = []
    awaiting = 0
    insufficient = 0
    for row in rows:
        try:
            matches = _matching_history(
                row, historical_opportunities, starts_after=starts_after
            )
            if len(matches) < ALTERNATIVE_SESSIONS:
                insufficient += 1
                continue
            actual_net_r, actual = _replay(
                row,
                market=market,
                contract_version=contract_version,
                load_bars=load_bars,
            )
            alternatives = []
            for rank, alternative_row in enumerate(matches, start=1):
                alternative_net_r, alternative = _replay(
                    alternative_row,
                    market=market,
                    contract_version=contract_version,
                    load_bars=load_bars,
                )
                if str(alternative["exit_on"]) >= str(row["armed_on"]):
                    raise ValueError(
                        "matched prior opportunity did not resolve before real arming"
                    )
                alternatives.append(
                    {"rank": rank, **alternative, "net_r": alternative_net_r}
                )
            source_sha = canonical_sha256(
                {
                    "actual": actual["source_sha256"],
                    "alternatives": [item["source_sha256"] for item in alternatives],
                }
            )
            record = {
                "control_version": CONTROL_VERSION,
                "market": market,
                "contract_version": contract_version,
                "selector_policy": PRIMARY_SELECTOR_POLICY,
                "selection_context_sha256": context_sha,
                "selection_manifest_sha256": selection_manifest["record_sha256"],
                "signal_id": str(row["signal_id"]),
                "prediction_sha256": str(row["prediction_sha256"]),
                "scrip_code": actual["scrip_code"],
                "armed_on": str(row["armed_on"]),
                "actual_outcome_state": actual["outcome_state"],
                "actual_outcome": actual["outcome"],
                "actual_entry_on": actual["entry_on"],
                "actual_exit_on": actual["exit_on"],
                "actual_net_r": actual_net_r,
                "alternatives": alternatives,
                "source_sha256": source_sha,
            }
            records.append(_seal_record(record))
        except PendingTimingOutcome:
            awaiting += 1
        except (KeyError, TypeError, ValueError) as exc:
            return _unavailable(
                f"{row.get('signal_id', 'unknown')}: {exc}",
                status="invalid_source",
                market=market,
                contract_version=contract_version,
                selection_context_sha256=context_sha,
                awaiting_outcomes=awaiting,
                insufficient_matched_history=insufficient,
            )
    if not records:
        reason = (
            "frozen winners or matched controls are awaiting terminal outcomes"
            if awaiting
            else "frozen winners lack twenty prior matched qualified calls"
        )
        return _unavailable(
            reason,
            status="collecting_insufficient_evidence",
            market=market,
            contract_version=contract_version,
            selection_context_sha256=context_sha,
            awaiting_outcomes=awaiting,
            insufficient_matched_history=insufficient,
        )
    report = summarize_random_timing_records(
        records,
        market=market,
        contract_version=contract_version,
        selection_context_sha256=context_sha,
    )
    report.update(
        awaiting_selected_outcomes=awaiting,
        insufficient_matched_history=insufficient,
        selection_manifest_sha256=selection_manifest["record_sha256"],
    )
    report["replay_sha256"] = canonical_sha256(
        {key: value for key, value in report.items() if key != "replay_sha256"}
    )
    return report


def validate_random_timing_report(report: Mapping[str, Any]) -> None:
    digest = report.get("replay_sha256")
    payload = {key: value for key, value in report.items() if key != "replay_sha256"}
    if not digest or digest != canonical_sha256(payload):
        raise ValueError("random-timing replay hash mismatch")
    if (
        report.get("version") != CONTROL_VERSION
        or report.get("selector_version") != SELECTOR_VERSION
        or report.get("selector_policy") != PRIMARY_SELECTOR_POLICY
        or report.get("control_configuration") != control_configuration()
        or report.get("markets_pooled") is not False
        or report.get("contracts_pooled") is not False
    ):
        raise ValueError("random-timing replay configuration mismatch")
    passed = report.get("random_timing_gate_passed")
    if passed != (report.get("status") == "random_timing_pass"):
        raise ValueError("random-timing replay status contradicts pass flag")
    if passed and (
        report.get("terminal_evaluation_locked") is not True
        or int(report.get("paired_calls") or 0) != TERMINAL_PAIRED_CALLS
        or int(report.get("active_sessions") or 0) < MINIMUM_ACTIVE_SESSIONS
        or not all((report.get("gate_checks") or {}).values())
    ):
        raise ValueError("random-timing passing gate is semantically incomplete")
    if passed:
        records = report.get("records")
        context_sha = str(report.get("selection_context_sha256") or "")
        if not isinstance(records, list) or not context_sha:
            raise ValueError("random-timing passing records are unavailable")
        recomputed = summarize_random_timing_records(
            records,
            market=str(report.get("market") or ""),
            contract_version=str(report.get("contract_version") or ""),
            selection_context_sha256=context_sha,
        )
        for field in (
            "status",
            "paired_calls",
            "active_sessions",
            "model_expectancy_r",
            "random_timing_expectancy_r_mean",
            "timing_advantage_r",
            "empirical_rank_tail_probability",
            "gate_checks",
            "source_manifest_sha256",
            "random_timing_gate_passed",
        ):
            if report.get(field) != recomputed.get(field):
                raise ValueError(f"random-timing passing metric mismatch: {field}")


def accumulate_random_timing_evidence(
    batch_report: Mapping[str, Any], path: Path
) -> dict[str, Any]:
    """Append unique-session records for one frozen model and lock the first 100."""

    has_records = bool(batch_report.get("records"))
    if has_records:
        validate_random_timing_report(batch_report)
    elif batch_report.get("status") != "collecting_insufficient_evidence":
        return dict(batch_report)
    market = str(batch_report.get("market") or "")
    contract_version = str(batch_report.get("contract_version") or "")
    context_sha = str(batch_report.get("selection_context_sha256") or "")
    if market not in {"nse", "bse", "crypto"} or not contract_version or not context_sha:
        raise ValueError("random-timing batch scope is unavailable")
    state: dict[str, Any] = {
        "version": ACCUMULATOR_VERSION,
        "control_configuration": control_configuration(),
        "market": market,
        "contract_version": contract_version,
        "selector_policy": PRIMARY_SELECTOR_POLICY,
        "selector_version": SELECTOR_VERSION,
        "selection_context_sha256": context_sha,
        "records": [],
        "batches": [],
        "terminal_locked": False,
    }
    loaded_ledger_sha: str | None = None
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("random-timing accumulator is malformed")
        digest = loaded.get("ledger_sha256")
        payload = {key: value for key, value in loaded.items() if key != "ledger_sha256"}
        if not digest or digest != canonical_sha256(payload):
            raise ValueError("random-timing accumulator integrity hash mismatch")
        if any(
            (
                loaded.get("version") != ACCUMULATOR_VERSION,
                loaded.get("control_configuration") != control_configuration(),
                loaded.get("market") != market,
                loaded.get("contract_version") != contract_version,
                loaded.get("selector_policy") != PRIMARY_SELECTOR_POLICY,
                loaded.get("selector_version") != SELECTOR_VERSION,
                loaded.get("selection_context_sha256") != context_sha,
            )
        ):
            raise ValueError("random-timing accumulator scope/configuration mismatch")
        state = payload
        loaded_ledger_sha = str(digest)
    elif not has_records:
        return dict(batch_report)

    existing: dict[str, dict[str, Any]] = {}
    sessions: dict[str, str] = {}
    for raw_record in state.get("records") or []:
        record = dict(raw_record)
        _validate_record(
            record,
            market=market,
            contract_version=contract_version,
            selection_context_sha256=context_sha,
        )
        signal_id = str(record.get("signal_id") or "")
        session = str(record.get("armed_on") or "")
        if not signal_id or signal_id in existing or not session or session in sessions:
            raise ValueError("random-timing accumulator violates top_1_per_session")
        existing[signal_id] = record
        sessions[session] = signal_id

    added = 0
    terminal_was_locked = state.get("terminal_locked") is True
    if not terminal_was_locked:
        for raw_record in batch_report.get("records") or []:
            record = dict(raw_record)
            _validate_record(
                record,
                market=market,
                contract_version=contract_version,
                selection_context_sha256=context_sha,
            )
            signal_id = str(record["signal_id"])
            session = str(record["armed_on"])
            if signal_id in existing:
                if existing[signal_id] != record:
                    raise ValueError("random-timing signal changed across locked batches")
                continue
            if session in sessions:
                raise ValueError("a second top-one winner appeared for an armed session")
            existing[signal_id] = record
            sessions[session] = signal_id
            added += 1

    ordered = sorted(
        existing.values(),
        key=lambda record: (str(record.get("armed_on")), str(record.get("signal_id"))),
    )
    if len(ordered) >= TERMINAL_PAIRED_CALLS:
        ordered = ordered[:TERMINAL_PAIRED_CALLS]
        state["terminal_locked"] = True
    state["records"] = ordered
    batches = [dict(batch) for batch in state.get("batches") or []]
    batch_digest = str(batch_report.get("replay_sha256") or "")
    if (
        has_records
        and not terminal_was_locked
        and not any(batch.get("replay_sha256") == batch_digest for batch in batches)
    ):
        batches.append(
            {
                "replay_sha256": batch_digest,
                "source_manifest_sha256": batch_report.get("source_manifest_sha256"),
                "selection_manifest_sha256": batch_report.get(
                    "selection_manifest_sha256"
                ),
                "signal_ids": sorted(
                    str(record["signal_id"])
                    for record in batch_report.get("records") or []
                ),
            }
        )
    state["batches"] = batches
    state["ledger_sha256"] = canonical_sha256(state)
    if has_records and (not terminal_was_locked or loaded_ledger_sha is None):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(path)

    aggregate = summarize_random_timing_records(
        ordered,
        market=market,
        contract_version=contract_version,
        selection_context_sha256=context_sha,
    )
    aggregate.update(
        evidence_mode="append_only_single_frozen_selector_terminal_cohort",
        evidence_ledger_sha256=state["ledger_sha256"],
        batches=len(batches),
        new_pairs_added=added,
        batch_replay_sha256=(
            batch_digest
            if has_records and not terminal_was_locked
            else batches[-1]["replay_sha256"]
            if batches
            else None
        ),
        awaiting_selected_outcomes=int(
            batch_report.get("awaiting_selected_outcomes") or 0
        ),
        insufficient_matched_history=int(
            batch_report.get("insufficient_matched_history") or 0
        ),
        terminal_batch_ignored=bool(has_records and terminal_was_locked),
    )
    aggregate["replay_sha256"] = canonical_sha256(
        {key: value for key, value in aggregate.items() if key != "replay_sha256"}
    )
    return aggregate


def evaluate_matched_random_timing(
    rows: Sequence[Mapping[str, Any]],
    *,
    historical_opportunities: Sequence[Mapping[str, Any]],
    market: str,
    contract_version: str,
    selection_context: Mapping[str, Any],
    selection_manifest: Mapping[str, Any],
    database_path: Path | None = None,
    bars_by_code: Mapping[str, pd.DataFrame] | None = None,
    bars_loader: BarsLoader | None = None,
) -> dict[str, Any]:
    if market not in {"nse", "bse", "crypto"}:
        raise ValueError("random-timing market must be nse, bse, or crypto")
    if contract_version not in CONTRACTS:
        raise ValueError("random-timing outcome contract is unknown")
    if bars_by_code is not None and bars_loader is not None:
        raise ValueError("provide bars_by_code or bars_loader, not both")
    if bars_by_code is not None:
        def mapping_loader(code: str) -> pd.DataFrame:
            return bars_by_code.get(code, pd.DataFrame())

        return _evaluate(
            rows,
            historical_opportunities=historical_opportunities,
            market=market,
            contract_version=contract_version,
            selection_context=selection_context,
            selection_manifest=selection_manifest,
            load_bars=mapping_loader,
        )
    if bars_loader is not None:
        cache: dict[str, pd.DataFrame] = {}

        def cached(code: str) -> pd.DataFrame:
            if code not in cache:
                cache[code] = bars_loader(code)
            return cache[code]

        return _evaluate(
            rows,
            historical_opportunities=historical_opportunities,
            market=market,
            contract_version=contract_version,
            selection_context=selection_context,
            selection_manifest=selection_manifest,
            load_bars=cached,
        )
    path = database_path or _database_path(market)
    if not path.exists():
        return _unavailable(
            f"daily candle database is unavailable: {path}",
            status="not_available",
            market=market,
            contract_version=contract_version,
        )
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection, database_loader = _duckdb_loader(path)
        return _evaluate(
            rows,
            historical_opportunities=historical_opportunities,
            market=market,
            contract_version=contract_version,
            selection_context=selection_context,
            selection_manifest=selection_manifest,
            load_bars=database_loader,
        )
    except (duckdb.Error, OSError, ValueError) as exc:
        return _unavailable(
            f"candle replay failed: {exc}",
            status="invalid_source",
            market=market,
            contract_version=contract_version,
        )
    finally:
        if connection is not None:
            connection.close()
