"""Real-population mechanism check for Same-Slot Micro-Momentum.

Milestone 2 deliberately tests the published return mechanism before any trade
geometry or fitted selector is allowed to run.  It assembles every stock/slot
decision in the frozen 50-stock, 120-session population and compares exact
same-slot cross-sectional rank continuation with adjacent-slot and
candidate-shuffled controls.

This module is research-only.  It cannot emit a live call, change the canonical
baseline, or alter dashboard, risk, broker, management, alert, or order behavior.
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

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.ssm_contract import DEFAULT_SSM_CONTRACT, SsmContract

IST = "Asia/Kolkata"
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class SsmMechanismContract:
    """Frozen diagnostics and stop gates for the Milestone-2 mechanism test."""

    version: str = "ssm-mechanism-v1"
    lag_sessions: tuple[int, ...] = tuple(range(1, 41))
    adjacent_slot_offsets: tuple[int, ...] = (-1, 1)
    shuffle_repetitions: int = 128
    shuffle_seed: int = 20260929
    minimum_candidates_per_group: int = 31
    top_bucket_percentile: float = 0.90
    minimum_positive_lag_fraction: float = 0.60
    minimum_lag_mean_t_statistic: float = 1.96
    maximum_shuffle_empirical_p: float = 0.05

    def __post_init__(self) -> None:
        if self.lag_sessions != tuple(range(1, 41)):
            raise ValueError("SSM mechanism lags must remain exactly 1 through 40")
        if self.adjacent_slot_offsets != (-1, 1):
            raise ValueError("SSM placebos must remain the two adjacent half-hours")
        if self.shuffle_repetitions < 100:
            raise ValueError("SSM requires at least 100 candidate-shuffled controls")
        if self.shuffle_seed != 20260929:
            raise ValueError("SSM shuffle seed cannot change after registration")
        if self.minimum_candidates_per_group < 31:
            raise ValueError("SSM mechanism groups require a candidate plus at least 30 peers")
        if not 0.5 < self.top_bucket_percentile < 1:
            raise ValueError("SSM top bucket must be an upper cross-sectional tail")
        if not 0.5 <= self.minimum_positive_lag_fraction <= 1:
            raise ValueError("positive-lag gate cannot be weaker than a majority")
        if self.minimum_lag_mean_t_statistic < 1.96:
            raise ValueError("SSM descriptive lag-mean t gate cannot be weakened")
        if not 0 < self.maximum_shuffle_empirical_p <= 0.05:
            raise ValueError("SSM shuffled-control p gate cannot exceed five percent")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @property
    def sha256(self) -> str:
        return _sha(self.to_dict())


DEFAULT_SSM_MECHANISM_CONTRACT = SsmMechanismContract()


@dataclass(frozen=True)
class SlotReturnPanel:
    """Complete half-hour returns in calendar/session/stock coordinates."""

    codes: tuple[str, ...]
    symbols: dict[str, str]
    sessions: tuple[str, ...]
    slot_starts: tuple[str, ...]
    values: np.ndarray
    source_exclusions: tuple[dict[str, str], ...]

    def __post_init__(self) -> None:
        expected = (len(self.sessions), len(self.slot_starts), len(self.codes))
        if self.values.shape != expected:
            raise ValueError("slot-return panel shape does not match its coordinates")
        if set(self.codes) != set(self.symbols):
            raise ValueError("slot-return symbols do not match the frozen codes")
        if len(self.codes) != len(set(self.codes)) or len(self.sessions) != len(set(self.sessions)):
            raise ValueError("slot-return coordinates must be unique")


def _as_ist_index(index: pd.Index) -> pd.DatetimeIndex:
    try:
        result = pd.DatetimeIndex(index)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_m1_timestamps") from exc
    return result.tz_localize(IST) if result.tz is None else result.tz_convert(IST)


def _validated_session(frame: pd.DataFrame, *, session: str) -> pd.DataFrame:
    try:
        bars = frame.loc[:, REQUIRED_COLUMNS].copy()
    except (KeyError, TypeError) as exc:
        raise ValueError("missing_m1_columns") from exc
    bars.index = _as_ist_index(bars.index)
    index = pd.DatetimeIndex(bars.index)
    if (
        bars.empty
        or index.hasnans
        or index.has_duplicates
        or not index.is_monotonic_increasing
        or any(str(stamp.date()) != session for stamp in index)
        or not ((index.second == 0) & (index.microsecond == 0)).all()
    ):
        raise ValueError("invalid_m1_session")
    try:
        values = bars.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_m1_values") from exc
    prices, volume = values[:, :4], values[:, 4]
    if (
        not np.isfinite(values).all()
        or (prices <= 0).any()
        or (volume < 0).any()
        or (bars.low.to_numpy() > prices[:, [0, 3]].min(axis=1)).any()
        or (bars.high.to_numpy() < prices[:, [0, 3]].max(axis=1)).any()
        or (bars.high.to_numpy() < bars.low.to_numpy()).any()
    ):
        raise ValueError("invalid_m1_values")
    return bars


def _slot_return(frame: pd.DataFrame, *, session: str, slot_start: str) -> float | None:
    start = pd.Timestamp(f"{session} {slot_start}", tz=IST)
    expected = pd.date_range(start, periods=30, freq="1min")
    selected = frame.loc[frame.index.isin(expected)]
    if not selected.index.equals(expected):
        return None
    result = float(selected.close.iloc[-1] / selected.open.iloc[0] - 1)
    return result if math.isfinite(result) else None


def build_slot_return_panel(
    per_code_sessions: dict[str, dict[str, pd.DataFrame]],
    symbols: dict[str, str],
    *,
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
) -> SlotReturnPanel:
    """Aggregate exact, non-overlapping 30-minute returns without crossing sessions."""

    codes = tuple(sorted(symbols))
    if not codes or set(codes) != set(per_code_sessions):
        raise ValueError("minute source and symbol registry must contain the same codes")
    sessions = tuple(
        sorted({session for code in codes for session in per_code_sessions[code]})
    )
    values = np.full((len(sessions), len(contract.slot_starts), len(codes)), np.nan)
    session_positions = {session: position for position, session in enumerate(sessions)}
    exclusions: list[dict[str, str]] = []

    for code_position, code in enumerate(codes):
        for session, raw_frame in sorted(per_code_sessions[code].items()):
            try:
                frame = _validated_session(raw_frame, session=session)
            except ValueError as exc:
                for slot_start in contract.slot_starts:
                    exclusions.append(
                        {
                            "scrip_code": code,
                            "session": session,
                            "slot_start": slot_start,
                            "reason": str(exc),
                        }
                    )
                continue
            for slot_position, slot_start in enumerate(contract.slot_starts):
                result = _slot_return(frame, session=session, slot_start=slot_start)
                if result is None:
                    exclusions.append(
                        {
                            "scrip_code": code,
                            "session": session,
                            "slot_start": slot_start,
                            "reason": "incomplete_half_hour",
                        }
                    )
                    continue
                values[session_positions[session], slot_position, code_position] = result

    return SlotReturnPanel(
        codes=codes,
        symbols={code: symbols[code] for code in codes},
        sessions=sessions,
        slot_starts=contract.slot_starts,
        values=values,
        source_exclusions=tuple(exclusions),
    )


def build_decision_population(
    panel: SlotReturnPanel,
    *,
    evaluation_sessions: list[str] | tuple[str, ...],
    included_sessions: dict[str, set[str]],
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
) -> pd.DataFrame:
    """Account for all planned stock/session/slot decisions, including exclusions."""

    evaluation = tuple(sorted(set(evaluation_sessions)))
    if not evaluation or any(session not in panel.sessions for session in evaluation):
        raise ValueError("evaluation sessions must be present in the slot-return source")
    if set(included_sessions) != set(panel.codes):
        raise ValueError("included-session registry must cover every frozen code")
    positions = {session: position for position, session in enumerate(panel.sessions)}
    rows: list[dict[str, Any]] = []

    for session in evaluation:
        session_position = positions[session]
        for code_position, code in enumerate(panel.codes):
            source_pair_included = session in included_sessions[code]
            for slot_position, slot_start in enumerate(panel.slot_starts):
                history_positions = np.flatnonzero(
                    np.isfinite(panel.values[:session_position, slot_position, code_position])
                )[-contract.history_lookback_sessions :]
                peer_count = 0
                if len(history_positions):
                    peer_complete = np.isfinite(
                        panel.values[history_positions, slot_position, :]
                    ).all(axis=0)
                    peer_complete[code_position] = False
                    peer_count = int(peer_complete.sum())
                realized = panel.values[session_position, slot_position, code_position]
                reason = None
                if not source_pair_included:
                    reason = "source_universe_pair_excluded"
                elif not math.isfinite(float(realized)):
                    reason = "incomplete_current_slot"
                elif len(history_positions) < contract.minimum_history_sessions:
                    reason = "insufficient_same_slot_history"
                elif peer_count < contract.minimum_cross_sectional_peers:
                    reason = "insufficient_peer_universe"
                entry_at = pd.Timestamp(f"{session} {slot_start}", tz=IST)
                rows.append(
                    {
                        "session": session,
                        "scrip_code": code,
                        "symbol": panel.symbols[code],
                        "slot_start": slot_start,
                        "slot_index": slot_position,
                        "decision_at": (entry_at - pd.Timedelta(minutes=1)).isoformat(),
                        "entry_at": entry_at.isoformat(),
                        "source_pair_included": source_pair_included,
                        "mechanism_eligible": reason is None,
                        "exclusion_reason": reason,
                        "historical_same_slot_sessions": int(len(history_positions)),
                        "candidate_excluded_peer_count": peer_count,
                        "realized_slot_return": float(realized)
                        if math.isfinite(float(realized))
                        else None,
                    }
                )
    population = pd.DataFrame(rows)
    expected = len(panel.codes) * len(evaluation) * len(panel.slot_starts)
    if len(population) != expected:
        raise ValueError("decision population does not cover every planned coordinate")
    identity = ["session", "scrip_code", "slot_start"]
    if population.duplicated(identity).any():
        raise ValueError("decision population contains duplicate coordinates")
    return population


def _cross_sectional_percentile_ranks(values: np.ndarray) -> np.ndarray:
    ranks = np.full(values.shape, np.nan, dtype=float)
    for session_position in range(values.shape[0]):
        for slot_position in range(values.shape[1]):
            row = values[session_position, slot_position]
            valid = np.isfinite(row)
            if valid.any():
                ranks[session_position, slot_position, valid] = (
                    pd.Series(row[valid]).rank(method="average", pct=True).to_numpy(dtype=float)
                )
    return ranks


def _row_correlations(
    left: np.ndarray,
    right: np.ndarray,
    allowed: np.ndarray,
    *,
    minimum: int,
) -> tuple[np.ndarray, np.ndarray]:
    if left.shape != right.shape or left.shape != allowed.shape or left.ndim != 2:
        raise ValueError("rank-correlation inputs must have identical two-dimensional shapes")
    valid = allowed & np.isfinite(left) & np.isfinite(right)
    counts = valid.sum(axis=1)
    safe = np.maximum(counts, 1)
    left_values = np.where(valid, left, 0.0)
    right_values = np.where(valid, right, 0.0)
    left_mean = left_values.sum(axis=1) / safe
    right_mean = right_values.sum(axis=1) / safe
    left_centered = np.where(valid, left - left_mean[:, None], 0.0)
    right_centered = np.where(valid, right - right_mean[:, None], 0.0)
    numerator = (left_centered * right_centered).sum(axis=1)
    denominator = np.sqrt(
        (left_centered * left_centered).sum(axis=1)
        * (right_centered * right_centered).sum(axis=1)
    )
    correlations = np.full(len(counts), np.nan, dtype=float)
    usable = (counts >= minimum) & (denominator > 0)
    correlations[usable] = numerator[usable] / denominator[usable]
    return correlations, counts


def _coefficient_summary(correlations: np.ndarray, counts: np.ndarray) -> dict[str, Any]:
    valid = np.isfinite(correlations)
    usable = correlations[valid]
    if not len(usable):
        return {
            "coefficient": None,
            "group_count": 0,
            "candidate_pairs": 0,
            "standard_error": None,
            "t_statistic": None,
        }
    standard_error = (
        float(np.std(usable, ddof=1) / math.sqrt(len(usable))) if len(usable) > 1 else None
    )
    coefficient = float(np.mean(usable))
    return {
        "coefficient": coefficient,
        "group_count": int(len(usable)),
        "candidate_pairs": int(counts[valid].sum()),
        "standard_error": standard_error,
        "t_statistic": coefficient / standard_error
        if standard_error is not None and standard_error > 0
        else None,
    }


def _top_bucket_summary(
    predictor_ranks: np.ndarray,
    realized_returns: np.ndarray,
    allowed: np.ndarray,
    *,
    percentile: float,
    minimum: int,
) -> dict[str, Any]:
    valid = allowed & np.isfinite(predictor_ranks) & np.isfinite(realized_returns)
    group_counts = valid.sum(axis=1)
    usable = group_counts >= minimum
    selected = valid & (predictor_ranks >= percentile)
    selected_counts = selected.sum(axis=1)
    selected_mean = np.divide(
        np.where(selected, realized_returns, 0.0).sum(axis=1),
        selected_counts,
        out=np.full(len(selected_counts), np.nan),
        where=selected_counts > 0,
    )
    group_mean = np.divide(
        np.where(valid, realized_returns, 0.0).sum(axis=1),
        group_counts,
        out=np.full(len(group_counts), np.nan),
        where=group_counts > 0,
    )
    keep = usable & np.isfinite(selected_mean) & np.isfinite(group_mean)
    return {
        "gross_return": float(np.mean(selected_mean[keep])) if keep.any() else None,
        "gross_excess_return": float(np.mean(selected_mean[keep] - group_mean[keep]))
        if keep.any()
        else None,
        "group_count": int(keep.sum()),
        "selected_candidate_pairs": int(selected_counts[keep].sum()),
    }


def _eligibility_cube(panel: SlotReturnPanel, population: pd.DataFrame) -> np.ndarray:
    result = np.zeros(panel.values.shape, dtype=bool)
    session_positions = {value: index for index, value in enumerate(panel.sessions)}
    slot_positions = {value: index for index, value in enumerate(panel.slot_starts)}
    code_positions = {value: index for index, value in enumerate(panel.codes)}
    for row in population.loc[population["mechanism_eligible"]].itertuples(index=False):
        result[
            session_positions[row.session],
            slot_positions[row.slot_start],
            code_positions[row.scrip_code],
        ] = True
    return result


def _lag_measurements(
    *,
    ranks: np.ndarray,
    returns: np.ndarray,
    eligibility: np.ndarray,
    target_positions: np.ndarray,
    lag: int,
    contract: SsmMechanismContract,
) -> dict[str, Any]:
    source_positions = target_positions - lag
    usable_targets = source_positions >= 0
    target_positions = target_positions[usable_targets]
    source_positions = source_positions[usable_targets]
    if not len(target_positions):
        raise ValueError("lag has no source sessions")

    current_rank = ranks[target_positions]
    current_return = returns[target_positions]
    allowed = eligibility[target_positions]
    same_rank = ranks[source_positions]
    same_correlations, same_counts = _row_correlations(
        same_rank.reshape(-1, same_rank.shape[-1]),
        current_rank.reshape(-1, current_rank.shape[-1]),
        allowed.reshape(-1, allowed.shape[-1]),
        minimum=contract.minimum_candidates_per_group,
    )
    same = _coefficient_summary(same_correlations, same_counts)
    same["top_bucket"] = _top_bucket_summary(
        same_rank.reshape(-1, same_rank.shape[-1]),
        current_return.reshape(-1, current_return.shape[-1]),
        allowed.reshape(-1, allowed.shape[-1]),
        percentile=contract.top_bucket_percentile,
        minimum=contract.minimum_candidates_per_group,
    )

    adjacent: dict[str, dict[str, Any]] = {}
    for offset in contract.adjacent_slot_offsets:
        if offset == -1:
            predictor = ranks[source_positions, :-1]
            outcome = current_rank[:, 1:]
            outcome_returns = current_return[:, 1:]
            permitted = allowed[:, 1:]
        else:
            predictor = ranks[source_positions, 1:]
            outcome = current_rank[:, :-1]
            outcome_returns = current_return[:, :-1]
            permitted = allowed[:, :-1]
        correlations, counts = _row_correlations(
            predictor.reshape(-1, predictor.shape[-1]),
            outcome.reshape(-1, outcome.shape[-1]),
            permitted.reshape(-1, permitted.shape[-1]),
            minimum=contract.minimum_candidates_per_group,
        )
        summary = _coefficient_summary(correlations, counts)
        summary["top_bucket"] = _top_bucket_summary(
            predictor.reshape(-1, predictor.shape[-1]),
            outcome_returns.reshape(-1, outcome_returns.shape[-1]),
            permitted.reshape(-1, permitted.shape[-1]),
            percentile=contract.top_bucket_percentile,
            minimum=contract.minimum_candidates_per_group,
        )
        adjacent[str(offset)] = summary
    return {"lag": lag, "same_slot": same, "adjacent_slot": adjacent}


def _shuffled_lag_coefficients(
    *,
    shuffled_ranks: np.ndarray,
    ranks: np.ndarray,
    eligibility: np.ndarray,
    target_positions: np.ndarray,
    lags: tuple[int, ...],
    minimum: int,
) -> np.ndarray:
    coefficients = []
    for lag in lags:
        source_positions = target_positions - lag
        usable = source_positions >= 0
        target = target_positions[usable]
        source = source_positions[usable]
        predictor = shuffled_ranks[source]
        outcome = ranks[target]
        permitted = eligibility[target]
        correlations, _ = _row_correlations(
            predictor.reshape(-1, predictor.shape[-1]),
            outcome.reshape(-1, outcome.shape[-1]),
            permitted.reshape(-1, permitted.shape[-1]),
            minimum=minimum,
        )
        coefficients.append(float(np.nanmean(correlations)))
    return np.asarray(coefficients, dtype=float)


def estimate_same_slot_mechanism(
    panel: SlotReturnPanel,
    population: pd.DataFrame,
    *,
    mechanism_contract: SsmMechanismContract = DEFAULT_SSM_MECHANISM_CONTRACT,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Estimate lag 1..40 periodicity and frozen adjacent/shuffled controls."""

    required = {"session", "scrip_code", "slot_start", "mechanism_eligible"}
    if not required.issubset(population.columns):
        raise ValueError("decision population is missing mechanism coordinates")
    evaluation_sessions = tuple(sorted(population["session"].unique()))
    session_positions = {session: position for position, session in enumerate(panel.sessions)}
    target_positions = np.asarray(
        [session_positions[session] for session in evaluation_sessions], dtype=int
    )
    ranks = _cross_sectional_percentile_ranks(panel.values)
    eligibility = _eligibility_cube(panel, population)

    measurements = [
        _lag_measurements(
            ranks=ranks,
            returns=panel.values,
            eligibility=eligibility,
            target_positions=target_positions,
            lag=lag,
            contract=mechanism_contract,
        )
        for lag in mechanism_contract.lag_sessions
    ]
    rows = []
    for item in measurements:
        row: dict[str, Any] = {"lag": item["lag"]}
        same = item["same_slot"]
        row.update(
            {
                "same_slot_coefficient": same["coefficient"],
                "same_slot_group_count": same["group_count"],
                "same_slot_candidate_pairs": same["candidate_pairs"],
                "same_slot_standard_error": same["standard_error"],
                "same_slot_t_statistic": same["t_statistic"],
                "same_slot_top_bucket_gross_return": same["top_bucket"]["gross_return"],
                "same_slot_top_bucket_gross_excess_return": same["top_bucket"][
                    "gross_excess_return"
                ],
            }
        )
        for offset, summary in item["adjacent_slot"].items():
            name = "previous" if offset == "-1" else "next"
            row[f"adjacent_{name}_coefficient"] = summary["coefficient"]
            row[f"adjacent_{name}_group_count"] = summary["group_count"]
            row[f"adjacent_{name}_top_bucket_gross_excess_return"] = summary["top_bucket"][
                "gross_excess_return"
            ]
        rows.append(row)
    diagnostics = pd.DataFrame(rows)

    rng = np.random.default_rng(mechanism_contract.shuffle_seed)
    shuffled_rows = []
    for repetition in range(mechanism_contract.shuffle_repetitions):
        order = np.argsort(rng.random(ranks.shape), axis=2)
        shuffled = np.take_along_axis(ranks, order, axis=2)
        coefficients = _shuffled_lag_coefficients(
            shuffled_ranks=shuffled,
            ranks=ranks,
            eligibility=eligibility,
            target_positions=target_positions,
            lags=mechanism_contract.lag_sessions,
            minimum=mechanism_contract.minimum_candidates_per_group,
        )
        shuffled_rows.append(
            {
                "repetition": repetition,
                "mean_lag1_40_coefficient": float(np.mean(coefficients)),
                "lag1_coefficient": float(coefficients[0]),
            }
        )
    shuffled_distribution = pd.DataFrame(shuffled_rows)

    same_coefficients = diagnostics["same_slot_coefficient"].to_numpy(dtype=float)
    adjacent_previous = diagnostics["adjacent_previous_coefficient"].to_numpy(dtype=float)
    adjacent_next = diagnostics["adjacent_next_coefficient"].to_numpy(dtype=float)
    same_mean = float(np.mean(same_coefficients))
    lag_standard_error = float(
        np.std(same_coefficients, ddof=1) / math.sqrt(len(same_coefficients))
    )
    lag_mean_t = same_mean / lag_standard_error if lag_standard_error > 0 else None
    positive_fraction = float(np.mean(same_coefficients > 0))
    adjacent_previous_mean = float(np.mean(adjacent_previous))
    adjacent_next_mean = float(np.mean(adjacent_next))
    strongest_adjacent = max(adjacent_previous_mean, adjacent_next_mean)
    shuffled_means = shuffled_distribution["mean_lag1_40_coefficient"].to_numpy(dtype=float)
    shuffled_p95 = float(np.quantile(shuffled_means, 0.95))
    shuffle_p = float((1 + np.sum(shuffled_means >= same_mean)) / (len(shuffled_means) + 1))
    gates = {
        "lag1_positive": bool(same_coefficients[0] > 0),
        "mean_lag1_40_positive": bool(same_mean > 0),
        "positive_lag_fraction": bool(
            positive_fraction >= mechanism_contract.minimum_positive_lag_fraction
        ),
        "lag_mean_t_statistic": bool(
            lag_mean_t is not None
            and lag_mean_t >= mechanism_contract.minimum_lag_mean_t_statistic
        ),
        "beats_both_adjacent_slot_placebos": bool(same_mean > strongest_adjacent),
        "beats_shuffled_95th_percentile": bool(same_mean > shuffled_p95),
        "shuffle_empirical_p": bool(
            shuffle_p <= mechanism_contract.maximum_shuffle_empirical_p
        ),
        "lag1_top_bucket_gross_excess_positive": bool(
            float(diagnostics.iloc[0]["same_slot_top_bucket_gross_excess_return"]) > 0
        ),
    }
    summary = {
        "same_slot_mean_lag1_40_coefficient": same_mean,
        "same_slot_lag1_coefficient": float(same_coefficients[0]),
        "same_slot_positive_lag_fraction": positive_fraction,
        "same_slot_lag_mean_standard_error": lag_standard_error,
        "same_slot_lag_mean_t_statistic": lag_mean_t,
        "adjacent_previous_mean_lag1_40_coefficient": adjacent_previous_mean,
        "adjacent_next_mean_lag1_40_coefficient": adjacent_next_mean,
        "strongest_adjacent_mean_lag1_40_coefficient": strongest_adjacent,
        "shuffle_mean_lag1_40_p95": shuffled_p95,
        "shuffle_empirical_p": shuffle_p,
        "lag1_top_bucket_gross_return": float(
            diagnostics.iloc[0]["same_slot_top_bucket_gross_return"]
        ),
        "lag1_top_bucket_gross_excess_return": float(
            diagnostics.iloc[0]["same_slot_top_bucket_gross_excess_return"]
        ),
        "gates": gates,
        "passed": all(gates.values()),
    }
    return diagnostics, shuffled_distribution, summary


def build_real_mechanism_check(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    audit_id: str | None = None,
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
    mechanism_contract: SsmMechanismContract = DEFAULT_SSM_MECHANISM_CONTRACT,
) -> tuple[SlotReturnPanel, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Load the frozen 50-stock source and return the complete M2 evidence in memory."""

    from tradedesk_lab.aem_v2_development_experiment import load_real_session_sources

    root, output = Path(root).resolve(), Path(output).resolve()
    sources = load_real_session_sources(root, output, audit_id=audit_id)
    panel = build_slot_return_panel(
        sources["per_code_sessions"], sources["symbols"], contract=contract
    )
    evaluation_sessions = sorted(
        {session for sessions in sources["included_sessions"].values() for session in sessions}
    )
    population = build_decision_population(
        panel,
        evaluation_sessions=evaluation_sessions,
        included_sessions=sources["included_sessions"],
        contract=contract,
    )
    diagnostics, shuffled, mechanism = estimate_same_slot_mechanism(
        panel, population, mechanism_contract=mechanism_contract
    )
    metadata = {
        "source_dataset_id": sources["dataset_id"],
        "source_universe_audit_id": sources["audit_id"],
        "source_sha256": sources["source_sha256"],
        "source_sessions": len(panel.sessions),
        "evaluation_sessions": len(evaluation_sessions),
        "universe_symbols": len(panel.codes),
        "planned_decisions": int(len(population)),
        "eligible_decisions": int(population["mechanism_eligible"].sum()),
        "excluded_decisions": int((~population["mechanism_eligible"]).sum()),
        "exclusion_reason_counts": population.loc[
            ~population["mechanism_eligible"], "exclusion_reason"
        ]
        .value_counts()
        .to_dict(),
        "slot_source_exclusions": len(panel.source_exclusions),
        "evidence_class": "consumed_historical_development",
        "mechanism": mechanism,
    }
    return panel, population, diagnostics, shuffled, metadata


def freeze_real_mechanism_check(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    audit_id: str | None = None,
    contract: SsmContract = DEFAULT_SSM_CONTRACT,
    mechanism_contract: SsmMechanismContract = DEFAULT_SSM_MECHANISM_CONTRACT,
) -> dict[str, Any]:
    """Persist the real population and mechanism evidence without running a selector."""

    root, output = Path(root).resolve(), Path(output).resolve()
    panel, population, diagnostics, shuffled, metadata = build_real_mechanism_check(
        root,
        output,
        audit_id=audit_id,
        contract=contract,
        mechanism_contract=mechanism_contract,
    )
    run_id = uuid4().hex
    target = output / "ssm/mechanism/runs" / run_id
    target.mkdir(parents=True, exist_ok=True)
    decisions_path = target / "decisions.csv"
    diagnostics_path = target / "lag_diagnostics.csv"
    shuffled_path = target / "shuffled_controls.csv"
    exclusions_path = target / "source_slot_exclusions.json"
    population.to_csv(decisions_path, index=False)
    diagnostics.to_csv(diagnostics_path, index=False)
    shuffled.to_csv(shuffled_path, index=False)
    write_json(exclusions_path, list(panel.source_exclusions))

    passed = bool(metadata["mechanism"]["passed"])
    status = "mechanism_present_selector_race_allowed" if passed else "mechanism_absent_stop"
    reason = (
        "The registered same-slot continuation gates beat adjacent-slot and shuffled "
        "controls. A bounded selector race may proceed, but no baseline improvement exists."
        if passed
        else (
            "At least one registered same-slot continuation gate failed. SSM stops before "
            "a selector race and does not change the canonical baseline."
        )
    )
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "version": "ssm-milestone-2-real-mechanism-v1",
        "status": status,
        "milestone": 2,
        "eligible_for_live": False,
        "baseline_improved": False,
        "algorithm_evaluated": False,
        "trade_geometry_evaluated": False,
        "selector_evaluated": False,
        "mechanism_evaluated": True,
        "mechanism_passed": passed,
        "ssm_contract_sha256": contract.sha256,
        "mechanism_contract": mechanism_contract.to_dict(),
        "mechanism_contract_sha256": mechanism_contract.sha256,
        **metadata,
        "decisions_sha256": digest(decisions_path),
        "lag_diagnostics_sha256": digest(diagnostics_path),
        "shuffled_controls_sha256": digest(shuffled_path),
        "source_slot_exclusions_sha256": digest(exclusions_path),
        "implementation_sha256": digest(Path(__file__)),
        "decision": {
            "proceed_to_selector_race": passed,
            "register_candidate": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
            "change_dashboard": False,
            "reason": reason,
        },
    }
    report_path = target / "report.json"
    write_json(report_path, report)
    write_json(
        output / "ssm/mechanism/latest.json",
        {"id": run_id, "path": str(report_path), "status": status},
    )
    return report
