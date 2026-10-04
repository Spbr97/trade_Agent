"""Frozen C2 crypto mechanism race over the verified C1 dataset.

The feature artifact is deliberately outcome-free.  C1 labels are joined only after the
feature and control assignments have been fixed, and no C2 result has live authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from tradedesk.reliability import wilson_lower_bound
from tradedesk_lab.artifacts import OUTPUT, digest, write_json
from tradedesk_lab.crypto_accuracy_dataset import (
    DEFAULT_DB as DEFAULT_C1_DB,
)
from tradedesk_lab.crypto_accuracy_dataset import (
    DEFAULT_GEOMETRIES,
    verify_crypto_dataset,
)
from tradedesk_lab.crypto_accuracy_dataset import (
    DEFAULT_OUTPUT as DEFAULT_C1_OUTPUT,
)
from tradedesk_lab.crypto_accuracy_dataset import (
    VERSION as C1_VERSION,
)

VERSION = "crypto-accuracy-mechanisms-v1"
DEFAULT_OUTPUT = OUTPUT / "crypto_accuracy_mechanisms"
BTC_CODE = "CDX_BTCINR"
MECHANISM_IDS = (
    "cross_sectional_momentum",
    "pullback_continuation",
    "liquidity_volatility_compression",
    "btc_relative_strength",
)
GEOMETRY_IDS = tuple(item.name for item in DEFAULT_GEOMETRIES)
CONTROL_IDS = ("inverse", "shuffled", "random_coin", "random_timing")
FEATURE_COLUMNS_FORBIDDEN = frozenset(
    {
        "status",
        "label",
        "outcome",
        "entry_at",
        "entry_session",
        "entry_price",
        "stop",
        "target",
        "exit_at",
        "exit_price",
        "gross_r",
        "net_r",
        "after_tax_r",
        "eligible_for_live",
        "mfe",
        "mae",
    }
)


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CryptoMechanismContract:
    """The preregistered C2 search space and evidence gates."""

    version: str = VERSION
    mechanism_ids: tuple[str, ...] = MECHANISM_IDS
    geometry_ids: tuple[str, ...] = GEOMETRY_IDS
    control_ids: tuple[str, ...] = CONTROL_IDS
    development_sessions: int = 30
    fold_count: int = 3
    selection_fraction: float = 0.20
    liquid_universe_fraction: float = 0.50
    minimum_cross_section: int = 200
    minimum_cross_section_coverage: float = 0.90
    cohort_repetitions: int = 1_000
    cohort_seed: int = 20261007
    minimum_pair_sessions: int = 30
    minimum_resolved_calls: int = 100
    minimum_active_sessions: int = 30
    minimum_accuracy: float = 0.80
    minimum_wilson95_lower: float = 0.70
    minimum_mean_net_r: float = 0.0
    minimum_control_advantage_r: float = 0.10
    maximum_familywise_empirical_p: float = 0.05

    def __post_init__(self) -> None:
        if self.version != VERSION:
            raise ValueError("crypto C2 protocol version is frozen")
        if self.mechanism_ids != MECHANISM_IDS:
            raise ValueError("crypto C2 mechanism registry is frozen")
        if self.geometry_ids != GEOMETRY_IDS:
            raise ValueError("crypto C2 geometry registry must match C1")
        if self.control_ids != CONTROL_IDS:
            raise ValueError("crypto C2 control registry is frozen")
        exact = {
            "development_sessions": (self.development_sessions, 30),
            "fold_count": (self.fold_count, 3),
            "selection_fraction": (self.selection_fraction, 0.20),
            "liquid_universe_fraction": (self.liquid_universe_fraction, 0.50),
            "minimum_cross_section": (self.minimum_cross_section, 200),
            "minimum_cross_section_coverage": (
                self.minimum_cross_section_coverage,
                0.90,
            ),
            "cohort_repetitions": (self.cohort_repetitions, 1_000),
            "cohort_seed": (self.cohort_seed, 20261007),
            "minimum_pair_sessions": (self.minimum_pair_sessions, 30),
            "minimum_resolved_calls": (self.minimum_resolved_calls, 100),
            "minimum_active_sessions": (self.minimum_active_sessions, 30),
            "minimum_accuracy": (self.minimum_accuracy, 0.80),
            "minimum_wilson95_lower": (self.minimum_wilson95_lower, 0.70),
            "minimum_mean_net_r": (self.minimum_mean_net_r, 0.0),
            "minimum_control_advantage_r": (
                self.minimum_control_advantage_r,
                0.10,
            ),
            "maximum_familywise_empirical_p": (
                self.maximum_familywise_empirical_p,
                0.05,
            ),
        }
        changed = [name for name, (actual, expected) in exact.items() if actual != expected]
        if changed:
            raise ValueError(f"crypto C2 frozen fields changed: {changed}")
        if self.development_sessions % self.fold_count:
            raise ValueError("crypto C2 folds must cover the development window exactly")
        if len(self.mechanism_ids) * len(self.geometry_ids) != 12:
            raise ValueError("crypto C2 is frozen to exactly 12 primary trials")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        return _json_hash(self.to_dict())


DEFAULT_CONTRACT = CryptoMechanismContract()


def _required_columns(frame: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def _as_bool(series: pd.Series, name: str) -> pd.Series:
    if series.isna().any():
        raise ValueError(f"{name} contains missing booleans")
    if not series.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError(f"{name} contains invalid booleans")
    return series.astype(bool)


def _feature_inputs(daily: pd.DataFrame) -> pd.DataFrame:
    required = {
        "scrip_code",
        "session",
        "closed_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quality_status",
        "membership_status",
        "configured_excluded",
        "point_in_time_eligible",
    }
    _required_columns(daily, required, "C1 daily artifact")
    frame = daily.copy()
    frame["scrip_code"] = frame.scrip_code.astype(str)
    frame["session"] = frame.session.astype(str)
    if frame[["scrip_code", "session"]].isna().any().any():
        raise ValueError("C1 daily identities contain missing values")
    if frame.duplicated(["scrip_code", "session"]).any():
        raise ValueError("C1 daily artifact contains duplicate pair/session rows")
    frame["configured_excluded"] = _as_bool(frame.configured_excluded, "configured_excluded")
    frame["point_in_time_eligible"] = _as_bool(
        frame.point_in_time_eligible, "point_in_time_eligible"
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["session_date"] = pd.to_datetime(frame.session, errors="raise").dt.date
    return frame.sort_values(["scrip_code", "session"]).reset_index(drop=True)


def _add_causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    enriched: list[pd.DataFrame] = []
    for _, raw_group in frame.groupby("scrip_code", sort=True):
        group = raw_group.copy().reset_index(drop=True)
        for column in (
            "return_3",
            "return_7",
            "return_14",
            "turnover_median_20",
            "compression_ratio_5_15",
        ):
            group[column] = np.nan
        dates = group.session_date.tolist()
        valid = group.quality_status.eq("valid").to_numpy()
        start = 0
        while start < len(group):
            if not valid[start]:
                start += 1
                continue
            end = start + 1
            while end < len(group) and valid[end] and (dates[end] - dates[end - 1]).days == 1:
                end += 1
            positions = group.index[start:end]
            segment = group.loc[positions]
            close = segment.close.astype(float)
            previous_close = close.shift(1)
            true_range = pd.concat(
                [
                    segment.high.astype(float) - segment.low.astype(float),
                    (segment.high.astype(float) - previous_close).abs(),
                    (segment.low.astype(float) - previous_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            range_pct = true_range / previous_close
            recent_range = range_pct.rolling(5, min_periods=5).mean()
            prior_range = range_pct.shift(5).rolling(15, min_periods=15).mean()
            group.loc[positions, "return_3"] = (close / close.shift(3) - 1).to_numpy()
            group.loc[positions, "return_7"] = (close / close.shift(7) - 1).to_numpy()
            group.loc[positions, "return_14"] = (close / close.shift(14) - 1).to_numpy()
            group.loc[positions, "turnover_median_20"] = (
                (close * segment.volume.astype(float)).rolling(20, min_periods=20).median()
            ).to_numpy()
            group.loc[positions, "compression_ratio_5_15"] = (recent_range / prior_range).to_numpy()
            start = end
        enriched.append(group)
    if not enriched:
        return frame.assign(
            return_3=np.nan,
            return_7=np.nan,
            return_14=np.nan,
            turnover_median_20=np.nan,
            compression_ratio_5_15=np.nan,
        )
    return pd.concat(enriched, ignore_index=True)


def _stable_select(
    session: pd.DataFrame,
    *,
    score_column: str,
    fraction: float,
) -> tuple[set[int], set[int]]:
    count = max(1, int(math.floor(len(session) * fraction)))
    ascending = session.sort_values(
        [score_column, "scrip_code"], ascending=[True, True], kind="mergesort"
    )
    descending = session.sort_values(
        [score_column, "scrip_code"], ascending=[False, True], kind="mergesort"
    )
    return set(descending.index[:count]), set(ascending.index[:count])


def build_feature_panel(
    daily: pd.DataFrame,
    contract: CryptoMechanismContract = DEFAULT_CONTRACT,
) -> pd.DataFrame:
    """Build the outcome-free, causal C2 feature/selection artifact."""
    frame = _add_causal_features(_feature_inputs(daily))
    decisions = frame.loc[frame.point_in_time_eligible & ~frame.configured_excluded].copy()
    if decisions.empty:
        return pd.DataFrame(
            columns=[
                "scrip_code",
                "decision_session",
                "decision_at",
                "mechanism",
                "score",
                "score_rank_pct",
                "registered_active_count",
                "feature_valid_count",
                "cross_section_coverage",
                "pool_size",
                "feature_status",
                "exclusion_reason",
                "pool_eligible",
                "selected",
                "inverse_selected",
            ]
        )
    decisions["btc_return_7"] = decisions.session.map(
        decisions.loc[decisions.scrip_code.eq(BTC_CODE)].set_index("session").return_7
    )
    rows: list[pd.DataFrame] = []
    for _session_name, raw_session in decisions.groupby("session", sort=True):
        base = raw_session.copy()
        active_count = len(base)
        formulas: dict[str, tuple[pd.Series, pd.Series, pd.Series]] = {}
        formulas["cross_sectional_momentum"] = (
            base.return_7,
            base.return_7.notna(),
            base.return_7.notna(),
        )
        long_rank = base.return_14.rank(pct=True, method="average")
        pullback_rank = (-base.return_3).rank(pct=True, method="average")
        formulas["pullback_continuation"] = (
            (long_rank + pullback_rank) / 2,
            base.return_14.notna() & base.return_3.notna(),
            base.return_14.notna()
            & base.return_3.notna()
            & base.return_14.gt(0)
            & base.return_3.le(0),
        )
        formulas["liquidity_volatility_compression"] = (
            -base.compression_ratio_5_15,
            base.turnover_median_20.gt(0) & base.compression_ratio_5_15.gt(0),
            base.turnover_median_20.gt(0) & base.compression_ratio_5_15.gt(0),
        )
        formulas["btc_relative_strength"] = (
            base.return_7 - base.btc_return_7,
            base.return_7.notna() & base.btc_return_7.notna() & ~base.scrip_code.eq(BTC_CODE),
            base.return_7.notna() & base.btc_return_7.notna() & ~base.scrip_code.eq(BTC_CODE),
        )
        for mechanism in contract.mechanism_ids:
            score, valid, qualifying = formulas[mechanism]
            feature_valid_count = int(valid.sum())
            denominator = active_count - (1 if mechanism == "btc_relative_strength" else 0)
            coverage = feature_valid_count / denominator if denominator > 0 else 0.0
            cross_section_ok = (
                feature_valid_count >= contract.minimum_cross_section
                and coverage >= contract.minimum_cross_section_coverage
            )
            out = base[["scrip_code", "session", "closed_at"]].copy()
            out.rename(
                columns={"session": "decision_session", "closed_at": "decision_at"},
                inplace=True,
            )
            out["mechanism"] = mechanism
            out["score"] = score.astype(float)
            out["score_rank_pct"] = np.nan
            out["registered_active_count"] = active_count
            out["feature_valid_count"] = feature_valid_count
            out["cross_section_coverage"] = coverage
            out["pool_size"] = 0
            out["feature_status"] = "excluded"
            out["exclusion_reason"] = "feature_unavailable"
            out["pool_eligible"] = False
            out["selected"] = False
            out["inverse_selected"] = False
            if not cross_section_ok:
                out.loc[:, "exclusion_reason"] = "cross_section_below_frozen_minimum"
                rows.append(out)
                continue
            valid_indices = base.index[valid]
            valid_scores = score.loc[valid_indices]
            out.loc[valid.to_numpy(), "feature_status"] = "eligible"
            out.loc[valid.to_numpy(), "exclusion_reason"] = None
            ranks = valid_scores.rank(pct=True, method="average")
            out.loc[valid.to_numpy(), "score_rank_pct"] = ranks.to_numpy()
            qualifying_indices = base.index[qualifying]
            if mechanism == "liquidity_volatility_compression":
                liquidity = base.loc[qualifying_indices, "turnover_median_20"]
                liquid_count = max(
                    1, int(math.ceil(len(liquidity) * contract.liquid_universe_fraction))
                )
                pool_indices = set(
                    base.loc[qualifying_indices]
                    .assign(_liquidity=liquidity)
                    .sort_values(
                        ["_liquidity", "scrip_code"],
                        ascending=[False, True],
                        kind="mergesort",
                    )
                    .index[:liquid_count]
                )
            else:
                pool_indices = set(qualifying_indices)
            pool_mask = out.index.isin(pool_indices)
            out.loc[pool_mask, "pool_eligible"] = True
            out.loc[:, "pool_size"] = len(pool_indices)
            if pool_indices:
                pool = base.loc[sorted(pool_indices)].copy()
                pool["_score"] = score.loc[pool.index]
                selected, inverse = _stable_select(
                    pool,
                    score_column="_score",
                    fraction=contract.selection_fraction,
                )
                out.loc[out.index.isin(selected), "selected"] = True
                out.loc[out.index.isin(inverse), "inverse_selected"] = True
            rows.append(out)
    result = pd.concat(rows, ignore_index=True)
    if set(result) & FEATURE_COLUMNS_FORBIDDEN:
        raise AssertionError("C2 feature artifact contains an outcome column")
    return result.sort_values(["decision_session", "mechanism", "scrip_code"]).reset_index(
        drop=True
    )


def mechanism_metrics(frame: pd.DataFrame, mask: pd.Series | np.ndarray) -> dict[str, Any]:
    chosen = frame.loc[np.asarray(mask, dtype=bool)]
    resolved = chosen.loc[chosen.label_status.eq("resolved")]
    count = len(resolved)
    wins = int(resolved.label.sum()) if count else 0
    return {
        "attempts": len(chosen),
        "resolved_calls": count,
        "wins": wins,
        "active_sessions": int(resolved.decision_session.nunique()) if count else 0,
        "observed_accuracy": float(wins / count) if count else None,
        "wilson95_lower": wilson_lower_bound(wins, count),
        "mean_gross_r": float(resolved.gross_r.mean()) if count else None,
        "mean_net_r": float(resolved.net_r.mean()) if count else None,
        "mean_after_tax_r": float(resolved.after_tax_r.mean()) if count else None,
        "maximum_coin_share": (
            float(resolved.scrip_code.value_counts(normalize=True).max()) if count else None
        ),
    }


def _label_inputs(labels: pd.DataFrame, contract: CryptoMechanismContract) -> pd.DataFrame:
    required = {
        "scrip_code",
        "decision_session",
        "geometry",
        "point_in_time_eligible",
        "status",
        "label",
        "gross_r",
        "net_r",
        "after_tax_r",
    }
    _required_columns(labels, required, "C1 label artifact")
    safe = labels[list(required)].copy()
    safe.rename(columns={"status": "label_status"}, inplace=True)
    safe["scrip_code"] = safe.scrip_code.astype(str)
    safe["decision_session"] = safe.decision_session.astype(str)
    safe["geometry"] = safe.geometry.astype(str)
    safe["point_in_time_eligible"] = _as_bool(
        safe.point_in_time_eligible, "label point_in_time_eligible"
    )
    safe = safe.loc[safe.point_in_time_eligible].copy()
    if safe.duplicated(["scrip_code", "decision_session", "geometry"]).any():
        raise ValueError("C1 labels contain duplicate pair/decision/geometry keys")
    unknown = sorted(set(safe.geometry) - set(contract.geometry_ids))
    if unknown:
        raise ValueError(f"C1 labels contain unregistered geometries: {unknown}")
    resolved = safe.label_status.eq("resolved")
    for column in ("label", "gross_r", "net_r", "after_tax_r"):
        safe[column] = pd.to_numeric(safe[column], errors="coerce")
    if safe.loc[resolved, ["label", "gross_r", "net_r", "after_tax_r"]].isna().any().any():
        raise ValueError("resolved C1 labels have incomplete outcomes")
    if not safe.loc[resolved, "label"].isin([0, 1]).all():
        raise ValueError("resolved C1 labels are not binary")
    return safe


def join_frozen_labels(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    contract: CryptoMechanismContract = DEFAULT_CONTRACT,
) -> pd.DataFrame:
    if set(features) & FEATURE_COLUMNS_FORBIDDEN:
        raise ValueError("C2 feature artifact contains forbidden outcome columns")
    required_features = {
        "scrip_code",
        "decision_session",
        "mechanism",
        "feature_status",
        "pool_eligible",
        "selected",
        "inverse_selected",
    }
    _required_columns(features, required_features, "C2 feature artifact")
    safe_features = features.copy()
    for column in ("scrip_code", "decision_session", "mechanism"):
        safe_features[column] = safe_features[column].astype(str)
    if safe_features.duplicated(["scrip_code", "decision_session", "mechanism"]).any():
        raise ValueError("C2 features contain duplicate pair/decision/mechanism keys")
    safe_labels = _label_inputs(labels, contract)
    population = safe_features.merge(
        safe_labels,
        on=["scrip_code", "decision_session"],
        how="left",
        validate="many_to_many",
        indicator=True,
    )
    expected = len(safe_features) * len(contract.geometry_ids)
    if len(population) != expected or not population._merge.eq("both").all():
        raise ValueError("C2 feature population and C1 geometry labels differ")
    population.drop(columns="_merge", inplace=True)
    return population.sort_values(
        ["mechanism", "geometry", "decision_session", "scrip_code"]
    ).reset_index(drop=True)


def _seed(contract: CryptoMechanismContract, *parts: str) -> int:
    payload = "|".join((str(contract.cohort_seed), *parts)).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _control_indices(
    mechanism_frame: pd.DataFrame,
    contract: CryptoMechanismContract,
) -> tuple[dict[str, np.ndarray], str]:
    """Freeze assignment indices using identities/features only, never outcomes."""
    frame = mechanism_frame.reset_index(drop=True)
    selected_positions = np.flatnonzero(frame.selected.to_numpy(dtype=bool))
    repetitions = contract.cohort_repetitions
    assignments: dict[str, np.ndarray] = {}
    identity_hash = hashlib.sha256()
    identity_hash.update(frame[["scrip_code", "decision_session"]].to_csv(index=False).encode())
    for control in ("shuffled", "random_coin"):
        rng = np.random.default_rng(_seed(contract, str(frame.mechanism.iloc[0]), control))
        chunks: list[np.ndarray] = []
        for _, positions in frame.groupby("decision_session", sort=True).indices.items():
            session_positions = np.asarray(positions, dtype=int)
            pool = session_positions[
                frame.loc[session_positions, "pool_eligible"].to_numpy(dtype=bool)
            ]
            chosen_count = int(frame.loc[session_positions, "selected"].to_numpy(dtype=bool).sum())
            if chosen_count == 0:
                continue
            if chosen_count > len(pool):
                raise ValueError("C2 control pool is smaller than the selected set")
            random_values = rng.random((repetitions, len(pool)))
            local = np.argpartition(random_values, chosen_count - 1, axis=1)[:, :chosen_count]
            if control == "shuffled":
                # Reverse the deterministic local order to keep this assignment stream
                # distinct from the independently seeded random-coin stream.
                local = local[:, ::-1]
            chunks.append(pool[local])
        assignments[control] = (
            np.concatenate(chunks, axis=1) if chunks else np.empty((repetitions, 0), dtype=int)
        )
        identity_hash.update(control.encode())
        identity_hash.update(assignments[control].astype("<i8", copy=False).tobytes())

    rng = np.random.default_rng(_seed(contract, str(frame.mechanism.iloc[0]), "random_timing"))
    timing = np.empty((repetitions, len(selected_positions)), dtype=int)
    for column, position in enumerate(selected_positions):
        code = frame.at[position, "scrip_code"]
        session = frame.at[position, "decision_session"]
        alternatives = np.flatnonzero(
            frame.scrip_code.eq(code).to_numpy()
            & frame.feature_status.eq("eligible").to_numpy()
            & ~frame.decision_session.eq(session).to_numpy()
        )
        if not len(alternatives):
            timing[:, column] = -1
        else:
            timing[:, column] = alternatives[rng.integers(0, len(alternatives), size=repetitions)]
    assignments["random_timing"] = timing
    identity_hash.update(b"random_timing")
    identity_hash.update(timing.astype("<i8", copy=False).tobytes())
    return assignments, identity_hash.hexdigest()


def _distribution(
    frame: pd.DataFrame,
    indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if indices.shape[1] == 0 or (indices < 0).any():
        empty = np.full(indices.shape[0], np.nan)
        return empty, empty.copy(), empty.copy()
    status = frame.label_status.eq("resolved").to_numpy()[indices]
    if not status.all(axis=1).all():
        empty = np.full(indices.shape[0], np.nan)
        return empty, empty.copy(), empty.copy()
    labels = frame.label.to_numpy(dtype=float, na_value=np.nan)[indices]
    net = frame.net_r.to_numpy(dtype=float, na_value=np.nan)[indices]
    after_tax = frame.after_tax_r.to_numpy(dtype=float, na_value=np.nan)[indices]
    return labels.mean(axis=1), net.mean(axis=1), after_tax.mean(axis=1)


def _fold_direction(frame: pd.DataFrame, contract: CryptoMechanismContract) -> list[dict[str, Any]]:
    sessions = sorted(frame.decision_session.unique())
    folds = np.array_split(np.asarray(sessions, dtype=object), contract.fold_count)
    rows: list[dict[str, Any]] = []
    for index, fold in enumerate(folds, start=1):
        inside = frame.decision_session.isin(fold)
        selected = mechanism_metrics(frame, inside & frame.selected)
        unfiltered = mechanism_metrics(frame, inside & frame.pool_eligible)
        accuracy_delta = (
            selected["observed_accuracy"] - unfiltered["observed_accuracy"]
            if selected["observed_accuracy"] is not None
            and unfiltered["observed_accuracy"] is not None
            else None
        )
        net_delta = (
            selected["mean_net_r"] - unfiltered["mean_net_r"]
            if selected["mean_net_r"] is not None and unfiltered["mean_net_r"] is not None
            else None
        )
        rows.append(
            {
                "fold": index,
                "first_session": str(fold[0]) if len(fold) else None,
                "last_session": str(fold[-1]) if len(fold) else None,
                "selected_resolved_calls": selected["resolved_calls"],
                "accuracy_delta_vs_unfiltered": accuracy_delta,
                "mean_net_r_delta_vs_unfiltered": net_delta,
                "directionally_positive": bool(
                    accuracy_delta is not None
                    and net_delta is not None
                    and accuracy_delta > 0
                    and net_delta > 0
                ),
            }
        )
    return rows


def _finite(value: Any) -> bool:
    return value is not None and math.isfinite(float(value))


def evaluate_mechanism_race(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    contract: CryptoMechanismContract = DEFAULT_CONTRACT,
) -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, Any]]:
    """Evaluate all 12 fixed trials after the first 30 sessions are fully mature."""
    population = join_frozen_labels(features, labels, contract)
    sessions = sorted(features.decision_session.unique())[: contract.development_sessions]
    population = population.loc[population.decision_session.isin(sessions)].copy()
    trial_work: list[dict[str, Any]] = []
    distribution_rows: list[dict[str, Any]] = []
    for mechanism in contract.mechanism_ids:
        feature_frame = (
            features.loc[
                features.mechanism.eq(mechanism) & features.decision_session.isin(sessions)
            ]
            .sort_values(["decision_session", "scrip_code"])
            .reset_index(drop=True)
        )
        assignments, assignment_sha = _control_indices(feature_frame, contract)
        for geometry in contract.geometry_ids:
            trial = (
                population.loc[
                    population.mechanism.eq(mechanism) & population.geometry.eq(geometry)
                ]
                .sort_values(["decision_session", "scrip_code"])
                .reset_index(drop=True)
            )
            selected = mechanism_metrics(trial, trial.selected)
            unfiltered = mechanism_metrics(trial, trial.pool_eligible)
            inverse = mechanism_metrics(trial, trial.inverse_selected)
            control_summaries: dict[str, dict[str, Any]] = {"inverse": inverse}
            raw_distributions: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
            for control in ("shuffled", "random_coin", "random_timing"):
                accuracy, net_r, after_tax = _distribution(trial, assignments[control])
                raw_distributions[control] = (accuracy, net_r, after_tax)
                valid = np.isfinite(accuracy) & np.isfinite(net_r)
                control_summaries[control] = {
                    "cohorts": int(valid.sum()),
                    "observed_accuracy_mean": (
                        float(np.mean(accuracy[valid])) if valid.any() else None
                    ),
                    "mean_net_r": float(np.mean(net_r[valid])) if valid.any() else None,
                    "mean_after_tax_r": (float(np.mean(after_tax[valid])) if valid.any() else None),
                }
                for repetition in range(contract.cohort_repetitions):
                    distribution_rows.append(
                        {
                            "mechanism": mechanism,
                            "geometry": geometry,
                            "control": control,
                            "repetition": repetition,
                            "observed_accuracy": (
                                float(accuracy[repetition])
                                if np.isfinite(accuracy[repetition])
                                else None
                            ),
                            "mean_net_r": (
                                float(net_r[repetition]) if np.isfinite(net_r[repetition]) else None
                            ),
                            "mean_after_tax_r": (
                                float(after_tax[repetition])
                                if np.isfinite(after_tax[repetition])
                                else None
                            ),
                        }
                    )
            trial_work.append(
                {
                    "mechanism": mechanism,
                    "geometry": geometry,
                    "assignment_sha256": assignment_sha,
                    "selected": selected,
                    "unfiltered": unfiltered,
                    "controls": control_summaries,
                    "raw_distributions": raw_distributions,
                    "folds": _fold_direction(trial, contract),
                }
            )

    distributions = pd.DataFrame(distribution_rows)
    familywise: dict[tuple[str, str, str, str], float] = {}
    for control in ("shuffled", "random_coin", "random_timing"):
        subset = distributions.loc[distributions.control.eq(control)]
        for metric in ("observed_accuracy", "mean_net_r"):
            maxima = subset.groupby("repetition")[metric].max().to_numpy(float)
            for work in trial_work:
                observed = work["selected"][metric]
                key = (work["mechanism"], work["geometry"], control, metric)
                familywise[key] = (
                    float((1 + int((maxima >= float(observed)).sum())) / (len(maxima) + 1))
                    if _finite(observed) and len(maxima) and np.isfinite(maxima).all()
                    else 1.0
                )

    trials: list[dict[str, Any]] = []
    for work in trial_work:
        mechanism, geometry = work["mechanism"], work["geometry"]
        selected = work["selected"]
        unfiltered = work["unfiltered"]
        controls = work["controls"]
        adjusted: dict[str, dict[str, float]] = {}
        for control in ("shuffled", "random_coin", "random_timing"):
            adjusted[control] = {
                "accuracy": familywise[(mechanism, geometry, control, "observed_accuracy")],
                "mean_net_r": familywise[(mechanism, geometry, control, "mean_net_r")],
            }
        net_advantages: dict[str, float | None] = {}
        accuracy_advantages: dict[str, float | None] = {}
        for control, metrics in controls.items():
            control_net = metrics.get("mean_net_r")
            control_accuracy = metrics.get("observed_accuracy")
            if control_accuracy is None:
                control_accuracy = metrics.get("observed_accuracy_mean")
            net_advantages[control] = (
                float(selected["mean_net_r"] - control_net)
                if _finite(selected["mean_net_r"]) and _finite(control_net)
                else None
            )
            accuracy_advantages[control] = (
                float(selected["observed_accuracy"] - control_accuracy)
                if _finite(selected["observed_accuracy"]) and _finite(control_accuracy)
                else None
            )
        gates = {
            "minimum_resolved_calls": selected["resolved_calls"] >= contract.minimum_resolved_calls,
            "minimum_active_sessions": selected["active_sessions"]
            >= contract.minimum_active_sessions,
            "accuracy_target": _finite(selected["observed_accuracy"])
            and selected["observed_accuracy"] >= contract.minimum_accuracy,
            "wilson_target": _finite(selected["wilson95_lower"])
            and selected["wilson95_lower"] >= contract.minimum_wilson95_lower,
            "positive_after_cost_expectancy": _finite(selected["mean_net_r"])
            and selected["mean_net_r"] > contract.minimum_mean_net_r,
            "beats_unfiltered_accuracy_and_net_r": bool(
                _finite(selected["observed_accuracy"])
                and _finite(unfiltered["observed_accuracy"])
                and _finite(selected["mean_net_r"])
                and _finite(unfiltered["mean_net_r"])
                and selected["observed_accuracy"] > unfiltered["observed_accuracy"]
                and selected["mean_net_r"] > unfiltered["mean_net_r"]
            ),
            "every_fold_directionally_positive": all(
                row["directionally_positive"] for row in work["folds"]
            ),
            "beats_every_control_accuracy": all(
                value is not None and value > 0 for value in accuracy_advantages.values()
            ),
            "minimum_net_r_advantage_every_control": all(
                value is not None and value >= contract.minimum_control_advantage_r
                for value in net_advantages.values()
            ),
            "familywise_accuracy_p": all(
                values["accuracy"] <= contract.maximum_familywise_empirical_p
                for values in adjusted.values()
            ),
            "familywise_net_r_p": all(
                values["mean_net_r"] <= contract.maximum_familywise_empirical_p
                for values in adjusted.values()
            ),
            "all_control_cohorts_available": all(
                controls[name].get("cohorts") == contract.cohort_repetitions
                for name in ("shuffled", "random_coin", "random_timing")
            ),
        }
        passed = all(gates.values())
        trials.append(
            {
                "mechanism": mechanism,
                "geometry": geometry,
                "status": "passed_research_only" if passed else "rejected_stopped",
                "assignment_sha256": work["assignment_sha256"],
                "selected": selected,
                "unfiltered": unfiltered,
                "controls": controls,
                "accuracy_advantage": accuracy_advantages,
                "net_r_advantage": net_advantages,
                "familywise_empirical_p": adjusted,
                "folds": work["folds"],
                "gates": gates,
                "passed": passed,
            }
        )
    passed_trials = [row for row in trials if row["passed"]]
    nominee = None
    if passed_trials:
        nominee = max(
            passed_trials,
            key=lambda row: (
                row["selected"]["wilson95_lower"],
                row["selected"]["mean_net_r"],
                -contract.mechanism_ids.index(row["mechanism"]),
                -contract.geometry_ids.index(row["geometry"]),
            ),
        )
    summary = {
        "registered_trials": len(trials),
        "evaluated_trials": len(trials),
        "passing_trials": len(passed_trials),
        "rejected_trials": len(trials) - len(passed_trials),
        "nominee": nominee,
    }
    return trials, distributions, summary


def _read_parquet(path: Path) -> pd.DataFrame:
    escaped = str(path).replace("'", "''")
    with duckdb.connect() as con:
        return con.execute(f"SELECT * FROM read_parquet('{escaped}')").fetchdf()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(path).replace("'", "''")
    with duckdb.connect() as con:
        con.register("_frame", frame)
        con.execute(f"COPY _frame TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")


def _artifact(path: Path, rows: int, final_path: Path) -> dict[str, Any]:
    return {"path": str(final_path), "rows": rows, "sha256": digest(path)}


def _blocked_state(status: str, detail: str, errors: list[str]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "id": None,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "c1_dataset": None,
        "c1_readiness": None,
        "trial_counts": {
            "registered": 12,
            "evaluated": 0,
            "passed": 0,
            "rejected": 0,
            "incomplete": 12,
        },
        "best_trial": None,
        "mechanisms": [
            {
                "id": name,
                "status": "blocked",
                "evaluated_trials": 0,
                "passing_trials": 0,
                "stopped": False,
            }
            for name in MECHANISM_IDS
        ],
        "source_integrity": {"passed": False, "errors": errors},
        "baseline_improved": False,
        "eligible_for_live": False,
        "detail": detail,
    }


def run_crypto_accuracy_mechanisms(
    c1_output: Path = DEFAULT_C1_OUTPUT,
    c1_db_path: Path = DEFAULT_C1_DB,
    output: Path = DEFAULT_OUTPUT,
    *,
    contract: CryptoMechanismContract = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    """Materialize C2, or publish an honest collecting/blocked state."""
    c1_output, c1_db_path, output = Path(c1_output), Path(c1_db_path), Path(output)
    existing_state_path = output / "state.json"
    if existing_state_path.exists():
        try:
            existing_state = json.loads(existing_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            existing_state = None
        if isinstance(existing_state, dict) and existing_state.get("status") in {
            "mechanism_race_rejected",
            "mechanism_race_passed_research_only",
        }:
            existing_id = existing_state.get("id")
            frozen_check = verify_crypto_accuracy_mechanisms(
                output, run_id=str(existing_id) if existing_id else None
            )
            if not frozen_check["passed"]:
                raise ValueError("frozen final C2 result failed integrity verification")
            return existing_state
    latest_path = c1_output / "latest.json"
    if not latest_path.exists():
        state = _blocked_state(
            "blocked_missing_c1_dataset",
            "C2 is blocked because the frozen C1 dataset is unavailable.",
            ["C1 latest pointer is missing"],
        )
        write_json(output / "state.json", state)
        return state
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        dataset_id = str(latest["id"])
        manifest_path = c1_output / "runs" / dataset_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        state = _blocked_state(
            "blocked_invalid_c1_integrity",
            "C2 is blocked because the C1 pointer or manifest is invalid.",
            [f"{type(exc).__name__}: {exc}"],
        )
        write_json(output / "state.json", state)
        return state
    verification = verify_crypto_dataset(c1_output, c1_db_path, dataset_id=dataset_id)
    if not verification.get("passed"):
        state = _blocked_state(
            "blocked_invalid_c1_integrity",
            "C2 is blocked because C1 source or artifact integrity failed.",
            list(verification.get("errors") or ["C1 verification failed"]),
        )
        write_json(output / "state.json", state)
        return state
    if manifest.get("version") != C1_VERSION:
        state = _blocked_state(
            "blocked_invalid_c1_integrity",
            "C2 is blocked because the C1 protocol version changed.",
            ["unexpected C1 version"],
        )
        write_json(output / "state.json", state)
        return state

    daily_artifact = (manifest.get("artifacts") or {}).get("daily") or {}
    label_artifact = (manifest.get("artifacts") or {}).get("labels") or {}
    daily_path, labels_path = (
        Path(daily_artifact.get("path", "")),
        Path(label_artifact.get("path", "")),
    )
    if not daily_path.exists() or not labels_path.exists():
        state = _blocked_state(
            "blocked_invalid_c1_integrity",
            "C2 is blocked because a required C1 artifact is missing.",
            ["daily or label artifact missing"],
        )
        write_json(output / "state.json", state)
        return state
    daily, labels = _read_parquet(daily_path), _read_parquet(labels_path)
    features = build_feature_panel(daily, contract)
    membership = manifest.get("membership") or {}
    coverage = manifest.get("coverage_summary") or {}
    label_statuses = coverage.get("label_statuses") or {}
    decision_sessions = sorted(features.decision_session.unique())
    window_sessions = decision_sessions[: contract.development_sessions]
    minimum_pair_sessions = int(membership.get("minimum_pair_point_in_time_sessions") or 0)
    ready_pairs = int(membership.get("pairs_meeting_minimum_sessions") or 0)
    required_pairs = int(membership.get("required_active_pairs") or 0)
    resolved_labels = int(label_statuses.get("resolved") or 0)
    latest_closed = date.fromisoformat(str(manifest["latest_closed_session"]))
    window_mature = False
    if len(window_sessions) == contract.development_sessions:
        final_decision = date.fromisoformat(window_sessions[-1])
        maximum_hold = max(item.max_hold_sessions for item in DEFAULT_GEOMETRIES)
        window_mature = latest_closed >= final_decision + timedelta(days=maximum_hold)
    history_ready = bool(
        required_pairs
        and ready_pairs == required_pairs
        and minimum_pair_sessions >= contract.minimum_pair_sessions
        and len(window_sessions) == contract.development_sessions
    )

    implementation_sha = digest(Path(__file__))
    protocol_sha = _json_hash(
        {"contract_sha256": contract.sha256, "implementation_sha256": implementation_sha}
    )
    run_id = f"{dataset_id}-{protocol_sha[:10]}"
    target = output / "runs" / run_id
    if target.exists():
        verification_c2 = verify_crypto_accuracy_mechanisms(output, run_id=run_id)
        if not verification_c2["passed"]:
            raise ValueError("existing C2 artifact failed integrity verification")
        state = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        write_json(output / "state.json", state)
        write_json(output / "latest.json", {"id": run_id, "path": str(target / "manifest.json")})
        return state

    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "runs" / f".{run_id}.{uuid4().hex}.tmp"
    temporary.mkdir(parents=True, exist_ok=False)
    features_path = temporary / "features.parquet"
    trials_path = temporary / "trials.json"
    controls_path = temporary / "control_distributions.parquet"
    _write_parquet(features, features_path)
    trials: list[dict[str, Any]] = []
    distributions = pd.DataFrame(
        columns=[
            "mechanism",
            "geometry",
            "control",
            "repetition",
            "observed_accuracy",
            "mean_net_r",
            "mean_after_tax_r",
        ]
    )
    evaluation_summary: dict[str, Any] | None = None
    if history_ready and window_mature:
        trials, distributions, evaluation_summary = evaluate_mechanism_race(
            features.loc[features.decision_session.isin(window_sessions)].copy(),
            labels.loc[labels.decision_session.astype(str).isin(window_sessions)].copy(),
            contract,
        )
        status = (
            "mechanism_race_passed_research_only"
            if evaluation_summary["passing_trials"]
            else "mechanism_race_rejected"
        )
    elif history_ready:
        status = "collecting_c1_resolved_labels"
    else:
        status = "collecting_c1_point_in_time_history"
    write_json(trials_path, trials)
    _write_parquet(distributions, controls_path)
    evaluated = len(trials)
    passed = sum(bool(row.get("passed")) for row in trials)
    best = (evaluation_summary or {}).get("nominee")
    best_trial = None
    if best is not None:
        selected = best["selected"]
        best_trial = {
            "mechanism": best["mechanism"],
            "geometry": best["geometry"],
            "status": best["status"],
            "resolved_calls": selected["resolved_calls"],
            "active_sessions": selected["active_sessions"],
            "observed_accuracy": selected["observed_accuracy"],
            "wilson95_lower": selected["wilson95_lower"],
            "mean_net_r": selected["mean_net_r"],
            "minimum_control_advantage_r": min(best["net_r_advantage"].values()),
        }
    mechanism_summaries = []
    for name in contract.mechanism_ids:
        mechanism_trials = [row for row in trials if row["mechanism"] == name]
        passing = sum(bool(row["passed"]) for row in mechanism_trials)
        mechanism_summaries.append(
            {
                "id": name,
                "status": (
                    "passed_research_only"
                    if passing
                    else "rejected_stopped"
                    if mechanism_trials
                    else "collecting"
                ),
                "evaluated_trials": len(mechanism_trials),
                "passing_trials": passing,
                "stopped": bool(mechanism_trials and not passing),
            }
        )
    c1_pin = {
        "id": dataset_id,
        "status": manifest.get("status"),
        "manifest_path": str(manifest_path),
        "manifest_sha256": digest(manifest_path),
        "contract_sha256": manifest.get("contract_sha256"),
        "protocol_sha256": manifest.get("protocol_sha256"),
        "source_sha256": manifest.get("source_sha256"),
        "universe_event_sha256": membership.get("latest_event_sha256"),
        "configuration_sha256": (manifest.get("configuration") or {}).get("sha256"),
        "implementation_sha256": (manifest.get("implementation") or {}).get("sha256"),
        "daily_sha256": daily_artifact.get("sha256"),
        "daily_path": str(daily_path),
        "labels_sha256": label_artifact.get("sha256"),
        "labels_path": str(labels_path),
    }
    c1_readiness = {
        "minimum_pair_sessions": minimum_pair_sessions,
        "required_pair_sessions": contract.minimum_pair_sessions,
        "ready_pairs": ready_pairs,
        "required_pairs": required_pairs,
        "resolved_labels": resolved_labels,
        "minimum_resolved_labels": contract.minimum_resolved_calls,
        "resolved_sessions": (
            int(labels.loc[labels.status.eq("resolved"), "decision_session"].nunique())
            if "status" in labels
            else 0
        ),
        "minimum_active_sessions": contract.minimum_active_sessions,
        "development_sessions_frozen": len(window_sessions),
        "development_window_mature": window_mature,
    }
    final_features = target / features_path.name
    final_trials = target / trials_path.name
    final_controls = target / controls_path.name
    state = {
        "version": VERSION,
        "id": run_id,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "contract": contract.to_dict(),
        "contract_sha256": contract.sha256,
        "protocol_sha256": protocol_sha,
        "implementation": {"path": str(Path(__file__)), "sha256": implementation_sha},
        "c1_dataset": c1_pin,
        "c1_readiness": c1_readiness,
        "trial_counts": {
            "registered": 12,
            "evaluated": evaluated,
            "passed": passed,
            "rejected": evaluated - passed,
            "incomplete": 12 - evaluated,
        },
        "best_trial": best_trial,
        "mechanisms": mechanism_summaries,
        "artifacts": {
            "features": _artifact(features_path, len(features), final_features),
            "trials": _artifact(trials_path, len(trials), final_trials),
            "control_distributions": _artifact(controls_path, len(distributions), final_controls),
        },
        "source_integrity": {"passed": True, "errors": []},
        "evidence_scope": "crypto_only_never_pooled_with_nse_or_bse",
        "baseline_improved": False,
        "eligible_for_live": False,
        "next_checkpoint": (
            "C3_precision_selector" if passed else "C2_collect_or_stop_rejected_families"
        ),
        "detail": (
            "C2 found a development-only mechanism candidate; this authorizes only C3."
            if passed
            else "C2 completed and stopped every rejected mechanism family."
            if evaluated
            else "C2 is implemented, but real accuracy is not available and is not a pass; "
            "the frozen C1 point-in-time window is still collecting."
        ),
    }
    try:
        write_json(temporary / "manifest.json", state)
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    write_json(output / "state.json", state)
    write_json(output / "latest.json", {"id": run_id, "path": str(target / "manifest.json")})
    return state


def verify_crypto_accuracy_mechanisms(
    output: Path = DEFAULT_OUTPUT,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    output = Path(output)
    errors: list[str] = []
    try:
        if run_id is None:
            latest = json.loads((output / "latest.json").read_text(encoding="utf-8"))
            run_id = str(latest["id"])
        manifest_path = output / "runs" / run_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    if manifest.get("version") != VERSION:
        errors.append("C2 version changed")
    if _json_hash(manifest.get("contract")) != manifest.get("contract_sha256"):
        errors.append("C2 contract changed")
    if manifest.get("baseline_improved") is not False:
        errors.append("C2 cannot improve the canonical baseline")
    if manifest.get("eligible_for_live") is not False:
        errors.append("C2 cannot grant live authority")
    for name, artifact in (manifest.get("artifacts") or {}).items():
        path = Path(artifact.get("path", ""))
        if not path.exists():
            errors.append(f"{name} artifact is missing")
        elif digest(path) != artifact.get("sha256"):
            errors.append(f"{name} artifact hash changed")
    implementation = manifest.get("implementation") or {}
    implementation_path = Path(implementation.get("path", ""))
    if not implementation_path.exists() or digest(implementation_path) != implementation.get(
        "sha256"
    ):
        errors.append("C2 implementation changed")
    c1 = manifest.get("c1_dataset") or {}
    for name, path_key, hash_key in (
        ("manifest", "manifest_path", "manifest_sha256"),
        ("daily", "daily_path", "daily_sha256"),
        ("labels", "labels_path", "labels_sha256"),
    ):
        source_path = Path(c1.get(path_key, ""))
        if not source_path.exists():
            errors.append(f"C1 {name} source is missing")
        elif digest(source_path) != c1.get(hash_key):
            errors.append(f"C1 {name} source hash changed")
    return {"passed": not errors, "errors": errors, "id": run_id}
