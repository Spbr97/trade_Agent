"""Preregistered M15 test of causal pre-move leader separability.

This is an isolated research experiment, not a signal generator.  It consumes the frozen
M14 feature/label contract, selects a model on a chronological validation block, opens one
locked historical test block, and replays top-ranked instruments through an executable
quick-profit contract.  Results can only be ``development_candidate`` or a fail-closed
rejection; live authority is impossible here.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from tradedesk.broker.indstocks.models import IST
from tradedesk.config import load_config
from tradedesk.leader_discovery import (
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    LABEL_VERSION,
    extract_causal_universe,
    spec_for,
)
from tradedesk.markets import bse_market, crypto_market, nse_market
from tradedesk.models import TradeType, price_decimal
from tradedesk.prediction_ledger import canonical_sha256

EXPERIMENT_VERSION = "leader-separability-m15-v1"
DATASET_VERSION = "leader-separability-dataset-v1"
REPLAY_VERSION = "leader-executable-replay-v1"
PROTOCOL_VERSION = "leader-separability-protocol-v1"
PROTOCOL_PATH = Path("docs/self-learning-m15-leader-separability-protocol.md")
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/leader_separability")

SESSION_COUNT = 180
TRAIN_SESSIONS = 102
PURGE_SESSIONS = 3
VALIDATION_SESSIONS = 36
TEST_SESSIONS = 36
RANDOM_REPETITIONS = 2_000
RANDOM_SEED = 15
RESEARCH_CAPITAL = 100_000.0
RISK_BUDGET = 250.0
TARGET_R = 0.75
MarketName = Literal["nse", "bse", "crypto"]

DB_PATHS: dict[str, Path] = {
    "nse": Path("data/tradedesk.duckdb"),
    "bse": Path("data/bse.duckdb"),
    "crypto": Path("data/crypto.duckdb"),
}


@dataclass(frozen=True)
class SplitManifest:
    train: tuple[str, ...]
    purge_after_train: tuple[str, ...]
    validation: tuple[str, ...]
    purge_after_validation: tuple[str, ...]
    locked_test: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _protocol_sha256() -> str:
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(f"frozen M15 protocol missing: {PROTOCOL_PATH}")
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def _split_manifest(sessions: Sequence[date]) -> SplitManifest:
    ordered = sorted(set(sessions))
    if len(ordered) != SESSION_COUNT:
        raise ValueError(
            f"M15 requires exactly {SESSION_COUNT} mature sessions, got {len(ordered)}"
        )
    cursor = 0

    def take(count: int) -> tuple[str, ...]:
        nonlocal cursor
        result = tuple(item.isoformat() for item in ordered[cursor : cursor + count])
        cursor += count
        return result

    manifest = SplitManifest(
        train=take(TRAIN_SESSIONS),
        purge_after_train=take(PURGE_SESSIONS),
        validation=take(VALIDATION_SESSIONS),
        purge_after_validation=take(PURGE_SESSIONS),
        locked_test=take(TEST_SESSIONS),
    )
    if cursor != SESSION_COUNT:
        raise AssertionError("frozen M15 split does not consume exactly 180 sessions")
    return manifest


def _model_frame(extracted: pd.DataFrame) -> pd.DataFrame:
    required = {
        "session_date",
        "scrip_code",
        "symbol",
        "opportunity_label",
        *FEATURE_COLUMNS,
    }
    missing = required - set(extracted.columns)
    if missing:
        raise ValueError("M15 source missing columns: " + ", ".join(sorted(missing)))
    frame = extracted[
        ["session_date", "scrip_code", "symbol", *FEATURE_COLUMNS, "opportunity_label"]
    ].copy()
    frame["session"] = frame["session_date"].map(
        lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]
    )
    frame["label"] = frame["opportunity_label"].astype(int)
    frame.sort_values(["session", "scrip_code"], inplace=True)
    if frame.duplicated(["session", "scrip_code"]).any():
        raise ValueError("duplicate market/session/instrument row in M15 source")
    return frame[["session", "scrip_code", "symbol", *FEATURE_COLUMNS, "label"]]


def _dataset_payload(row: Any, market: str) -> dict[str, Any]:
    return {
        "version": DATASET_VERSION,
        "market": market,
        "session": row.session,
        "scrip_code": row.scrip_code,
        "symbol": row.symbol,
        "features": {
            name: (
                None
                if pd.isna(getattr(row, name))
                else float(getattr(row, name))
            )
            for name in FEATURE_COLUMNS
        },
        "label": int(row.label),
    }


def _write_gzip_rows(
    directory: Path,
    prefix: str,
    rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    frame: pd.DataFrame | None = None,
    market: str | None = None,
) -> tuple[Path, str]:
    """Write deterministic gzip JSONL under its own content digest."""

    if (rows is None) == (frame is None):
        raise ValueError("supply exactly one of rows or frame")
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{prefix}.tmp.gz"
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0
            ) as compressed:
                if frame is not None:
                    if market is None:
                        raise ValueError("market is required for dataset frame serialization")
                    values = (
                        _dataset_payload(row, market)
                        for row in frame.itertuples(index=False)
                    )
                else:
                    values = iter(rows or ())
                for value in values:
                    line = json.dumps(
                        value,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    compressed.write((line + "\n").encode("utf-8"))
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        destination = directory / f"{prefix}-{digest}.jsonl.gz"
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError(f"existing {prefix} artifact digest mismatch")
        else:
            os.replace(temporary, destination)
        return destination, digest
    finally:
        temporary.unlink(missing_ok=True)


def _build_models() -> dict[str, Pipeline]:
    return {
        "regularized_logistic": Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.1,
                        penalty="l2",
                        class_weight="balanced",
                        max_iter=2_000,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        ),
        "bounded_random_forest": Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=200,
                        max_depth=4,
                        min_samples_leaf=25,
                        max_features="sqrt",
                        class_weight="balanced_subsample",
                        random_state=RANDOM_SEED,
                        n_jobs=1,
                    ),
                ),
            ]
        ),
    }


def _select(frame: pd.DataFrame, scores: np.ndarray, count: int) -> pd.DataFrame:
    ranked = frame[["session", "scrip_code", "symbol", "label"]].copy()
    ranked["score"] = scores
    ranked.sort_values(
        ["session", "score", "scrip_code"],
        ascending=[True, False, True],
        inplace=True,
    )
    return ranked[ranked.groupby("session").cumcount() < count].copy()


def _selection_metrics(
    frame: pd.DataFrame, scores: np.ndarray, count: int
) -> tuple[dict[str, Any], pd.DataFrame]:
    selected = _select(frame, scores, count)
    positives = int(frame["label"].sum())
    successes = int(selected["label"].sum())
    prevalence = float(frame["label"].mean()) if len(frame) else None
    precision = successes / len(selected) if len(selected) else None
    return (
        {
            "policy": f"top_{count}_per_session",
            "sessions": int(frame["session"].nunique()),
            "selected": len(selected),
            "successes": successes,
            "precision": precision,
            "leader_recall": successes / positives if positives else None,
            "prevalence": prevalence,
            "lift_over_prevalence": (
                precision / prevalence
                if precision is not None and prevalence not in {None, 0.0}
                else None
            ),
            "calls_per_session": (
                len(selected) / frame["session"].nunique()
                if frame["session"].nunique()
                else None
            ),
            "no_call_frequency": 0.0,
        },
        selected,
    )


def _scorecard(frame: pd.DataFrame, scores: np.ndarray) -> dict[str, Any]:
    top_one, _ = _selection_metrics(frame, scores, 1)
    top_three, _ = _selection_metrics(frame, scores, 3)
    labels = frame["label"].to_numpy(dtype=int)
    auc = float(roc_auc_score(labels, scores)) if len(set(labels)) == 2 else None
    return {
        "rows": len(frame),
        "sessions": int(frame["session"].nunique()),
        "leaders": int(labels.sum()),
        "brier": float(brier_score_loss(labels, scores)),
        "roc_auc": auc,
        "top_1": top_one,
        "top_3": top_three,
    }


def _validation_key(name: str, card: Mapping[str, Any]) -> tuple[float, float, float, int]:
    top_one = card["top_1"]
    top_three = card["top_3"]
    return (
        float(top_one.get("precision") or -1.0),
        float(top_three.get("precision") or -1.0),
        -float(card.get("brier") or math.inf),
        1 if name == "regularized_logistic" else 0,
    )


def _random_opportunity_control(
    frame: pd.DataFrame,
    *,
    count: int,
    observed_precision: float,
    repetitions: int = RANDOM_REPETITIONS,
    seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    groups = [group["label"].to_numpy(dtype=int) for _, group in frame.groupby("session")]
    rng = np.random.default_rng(seed + count)
    distribution = np.empty(repetitions, dtype=float)
    for index in range(repetitions):
        successes = 0
        selections = 0
        for labels in groups:
            size = min(count, len(labels))
            picks = rng.choice(len(labels), size=size, replace=False)
            successes += int(labels[picks].sum())
            selections += size
        distribution[index] = successes / selections if selections else 0.0
    return {
        "repetitions": repetitions,
        "mean_precision": float(distribution.mean()),
        "p05_precision": float(np.quantile(distribution, 0.05)),
        "p95_precision": float(np.quantile(distribution, 0.95)),
        "tail_probability": float(
            (1 + int(np.sum(distribution >= observed_precision))) / (repetitions + 1)
        ),
        "seed": seed + count,
    }


_PATH_SQL = """
WITH bars AS (
    SELECT
        scrip_code,
        ts,
        CAST(timezone('Asia/Kolkata', to_timestamp(ts)) AS DATE) AS session_date,
        open,
        high,
        low,
        close
    FROM candles
    WHERE interval = '1day' AND ts BETWEEN ? AND ?
), sequenced AS (
    SELECT
        *,
        lead(open, 1) OVER w AS open_1,
        lead(high, 1) OVER w AS high_1,
        lead(low, 1) OVER w AS low_1,
        lead(close, 1) OVER w AS close_1,
        lead(open, 2) OVER w AS open_2,
        lead(high, 2) OVER w AS high_2,
        lead(low, 2) OVER w AS low_2,
        lead(close, 2) OVER w AS close_2,
        lead(open, 3) OVER w AS open_3,
        lead(high, 3) OVER w AS high_3,
        lead(low, 3) OVER w AS low_3,
        lead(close, 3) OVER w AS close_3
    FROM bars
    WINDOW w AS (PARTITION BY scrip_code ORDER BY ts)
)
SELECT s.*
FROM sequenced s
JOIN _m15_keys k
  ON k.scrip_code = s.scrip_code AND k.session_date = s.session_date
ORDER BY s.session_date, s.scrip_code
"""


def _load_replay_paths(db_path: Path, test: pd.DataFrame) -> pd.DataFrame:
    keys = test[["session", "scrip_code"]].copy()
    keys["session_date"] = pd.to_datetime(keys.pop("session")).dt.date
    first = min(keys["session_date"])
    last = max(keys["session_date"])
    start = int(datetime.combine(first - timedelta(days=2), time.min, tzinfo=IST).timestamp())
    end = int(datetime.combine(last + timedelta(days=15), time.max, tzinfo=IST).timestamp())
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        connection.register("_m15_keys", keys)
        paths = connection.execute(_PATH_SQL, [start, end]).df()
        connection.unregister("_m15_keys")
    finally:
        connection.close()
    paths["session"] = pd.to_datetime(paths["session_date"]).dt.date.map(date.isoformat)
    if paths.duplicated(["session", "scrip_code"]).any():
        raise ValueError("duplicate replay path for M15 test row")
    return paths


def _market_bundle(market: str) -> Any:
    settings = load_config(".")
    return {
        "nse": nse_market,
        "bse": bse_market,
        "crypto": crypto_market,
    }[market](settings)


def _replay_one(row: Mapping[str, Any], market: str, bundle: Any) -> dict[str, Any]:
    required = [
        "close",
        "open_1",
        "high_1",
        "low_1",
        "close_1",
        "open_2",
        "high_2",
        "low_2",
        "close_2",
        "open_3",
        "high_3",
        "low_3",
        "close_3",
        "atr_14_pct",
    ]
    if any(row.get(name) is None or not math.isfinite(float(row[name])) for name in required):
        return {"status": "invalid_missing_path", "strict_success": False, "net_r": 0.0}

    slippage = float(bundle.costs.slippage_pct)
    decision_close = float(row["close"])
    entry = float(row["open_1"]) * (1.0 + slippage)
    minimum_risk_pct = 0.04 if market == "crypto" else 0.02
    risk_distance = max(
        0.75 * decision_close * float(row["atr_14_pct"]),
        minimum_risk_pct * entry,
    )
    stop = entry - risk_distance
    target = entry + TARGET_R * risk_distance
    if entry <= 0 or stop <= 0 or target <= entry:
        return {"status": "invalid_geometry", "strict_success": False, "net_r": 0.0}

    qty_by_risk = RISK_BUDGET / risk_distance
    qty_by_capital = RESEARCH_CAPITAL / entry
    raw_qty = min(qty_by_risk, qty_by_capital)
    if market == "crypto":
        step = float(bundle.qty_step)
        quantity = math.floor(raw_qty / step) * step
    else:
        quantity = float(math.floor(raw_qty))
    if quantity <= 0 or quantity * entry < float(bundle.min_notional_inr):
        return {
            "status": "not_sizeable",
            "strict_success": False,
            "net_r": 0.0,
            "entry": entry,
            "stop": stop,
            "target": target,
            "quantity": quantity,
        }

    event = "timeout"
    exit_raw = float(row["close_3"])
    exit_session = 3
    for offset in (1, 2, 3):
        open_price = float(row[f"open_{offset}"])
        high = float(row[f"high_{offset}"])
        low = float(row[f"low_{offset}"])
        if offset > 1 and open_price <= stop:
            event, exit_raw, exit_session = "gap_stop", open_price, offset
            break
        if offset > 1 and open_price >= target:
            event, exit_raw, exit_session = "gap_target", open_price, offset
            break
        if low <= stop:
            event, exit_raw, exit_session = "stop", stop, offset
            break
        if high >= target:
            event, exit_raw, exit_session = "target", target, offset
            break
    exit_execution = exit_raw * (1.0 - slippage)
    gross_r = (exit_raw - entry) / risk_distance
    net_r = float(
        bundle.costs.net_r_multiple(
            trade_type=TradeType.DELIVERY,
            qty=quantity,
            entry=price_decimal(entry),
            stop=price_decimal(stop),
            exit_price=price_decimal(exit_execution),
        )
    )
    return {
        "status": "resolved",
        "event": event,
        "strict_success": event in {"target", "gap_target"},
        "entry": entry,
        "stop": stop,
        "target": target,
        "quantity": quantity,
        "exit_price_before_sell_slippage": exit_raw,
        "exit_execution_price": exit_execution,
        "exit_session": exit_session,
        "gross_r": gross_r,
        "net_r": net_r,
    }


def _build_replay_universe(
    test: pd.DataFrame, paths: pd.DataFrame, market: str
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    joined = test.merge(
        paths.drop(columns=["session_date"]),
        on=["session", "scrip_code"],
        how="left",
        validate="one_to_one",
    )
    bundle = _market_bundle(market)
    records: list[dict[str, Any]] = []
    for row in joined.to_dict(orient="records"):
        replay = _replay_one(row, market, bundle)
        records.append(
            {
                "version": REPLAY_VERSION,
                "market": market,
                "session": row["session"],
                "scrip_code": row["scrip_code"],
                "symbol": row["symbol"],
                "label": int(row["label"]),
                **replay,
            }
        )
    replay_frame = pd.DataFrame(records)
    return replay_frame, records


def _maximum_losing_streak(successes: Sequence[bool]) -> int:
    maximum = 0
    current = 0
    for success in successes:
        current = 0 if success else current + 1
        maximum = max(maximum, current)
    return maximum


def _replay_metrics(selected: pd.DataFrame, replay: pd.DataFrame) -> dict[str, Any]:
    merged = selected.merge(
        replay,
        on=["session", "scrip_code", "symbol", "label"],
        how="left",
        validate="one_to_one",
    )
    merged["strict_success"] = merged["strict_success"].fillna(False).astype(bool)
    merged["net_r"] = merged["net_r"].fillna(0.0).astype(float)
    event_series = merged.get("event", pd.Series(dtype=str)).fillna("invalid")
    events = Counter(str(value) for value in event_series)
    return {
        "selected": len(merged),
        "strict_successes": int(merged["strict_success"].sum()),
        "strict_accuracy": float(merged["strict_success"].mean()) if len(merged) else None,
        "mean_net_r": float(merged["net_r"].mean()) if len(merged) else None,
        "median_net_r": float(merged["net_r"].median()) if len(merged) else None,
        "maximum_losing_streak": _maximum_losing_streak(
            list(merged.sort_values(["session", "scrip_code"])["strict_success"])
        ),
        "events": dict(sorted(events.items())),
        "rows": merged.to_dict(orient="records"),
    }


def _random_replay_control(
    test: pd.DataFrame,
    replay: pd.DataFrame,
    *,
    observed_accuracy: float,
    observed_mean_net_r: float,
    repetitions: int = RANDOM_REPETITIONS,
) -> dict[str, Any]:
    merged = test[["session", "scrip_code"]].merge(
        replay[["session", "scrip_code", "strict_success", "net_r"]],
        on=["session", "scrip_code"],
        how="left",
        validate="one_to_one",
    )
    groups = []
    for _, group in merged.groupby("session"):
        success = group["strict_success"].fillna(False).to_numpy(dtype=bool)
        net_r = group["net_r"].fillna(0.0).to_numpy(dtype=float)
        groups.append((success, net_r))
    rng = np.random.default_rng(RANDOM_SEED + 100)
    accuracies = np.empty(repetitions, dtype=float)
    expectancies = np.empty(repetitions, dtype=float)
    for index in range(repetitions):
        selected_success: list[bool] = []
        selected_r: list[float] = []
        for success, net_r in groups:
            pick = int(rng.integers(0, len(success)))
            selected_success.append(bool(success[pick]))
            selected_r.append(float(net_r[pick]))
        accuracies[index] = float(np.mean(selected_success))
        expectancies[index] = float(np.mean(selected_r))
    return {
        "repetitions": repetitions,
        "mean_strict_accuracy": float(accuracies.mean()),
        "mean_net_r": float(expectancies.mean()),
        "p05_net_r": float(np.quantile(expectancies, 0.05)),
        "p95_net_r": float(np.quantile(expectancies, 0.95)),
        "accuracy_tail_probability": float(
            (1 + int(np.sum(accuracies >= observed_accuracy))) / (repetitions + 1)
        ),
        "net_r_tail_probability": float(
            (1 + int(np.sum(expectancies >= observed_mean_net_r))) / (repetitions + 1)
        ),
        "seed": RANDOM_SEED + 100,
    }


def _model_diagnostics(name: str, model: Pipeline) -> dict[str, float]:
    estimator = model.named_steps["model"]
    if name == "regularized_logistic":
        values = estimator.coef_[0]
    else:
        values = estimator.feature_importances_
    return {
        feature: float(value)
        for feature, value in sorted(
            zip(FEATURE_COLUMNS, values, strict=True),
            key=lambda pair: abs(float(pair[1])),
            reverse=True,
        )
    }


def _artifact_without_hash(report: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "artifact_sha256"}


def validate_separability_report(report: Mapping[str, Any]) -> None:
    if report.get("version") != EXPERIMENT_VERSION:
        raise ValueError("unsupported M15 experiment version")
    spec_for(str(report.get("market")))
    if report.get("markets_pooled") is not False:
        raise ValueError("M15 markets must never be pooled")
    if report.get("active_model_changed") is not False:
        raise ValueError("M15 cannot change the active model")
    if report.get("eligible_for_live") is not False:
        raise ValueError("M15 cannot authorize live calls")
    if report.get("baseline_accuracy_improved") is not False:
        raise ValueError("historical M15 cannot prove baseline improvement")
    digest = report.get("artifact_sha256")
    if not isinstance(digest, str) or digest != canonical_sha256(_artifact_without_hash(report)):
        raise ValueError("M15 report hash mismatch")


def _load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"M15 registry line {line_number} is not an object")
        records.append(value)
    return records


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_registered_result(
    registry: Sequence[Mapping[str, Any]], identity: str
) -> dict[str, Any] | None:
    matches = [record for record in registry if record.get("experiment_id") == identity]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"duplicate M15 registry identity: {identity}")
    report_path = Path(str(matches[0].get("report_path")))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("registered M15 report is not an object")
    validate_separability_report(report)
    return report


def run_leader_separability(
    *,
    market: str,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run or idempotently load one preregistered market-specific M15 experiment."""

    spec_for(market)
    source_db = db_path or DB_PATHS[market]
    observed_at = now or datetime.now(IST)
    extracted, source = extract_causal_universe(
        source_db,
        market,
        now=observed_at,
        session_count=SESSION_COUNT,
    )
    frame = _model_frame(extracted)
    sessions = sorted(pd.to_datetime(frame["session"]).dt.date.unique())
    split = _split_manifest(sessions)
    market_root = output_root / market
    dataset_path, dataset_sha = _write_gzip_rows(
        market_root / "datasets", "causal", frame=frame, market=market
    )
    protocol_sha = _protocol_sha256()
    identity = canonical_sha256(
        {
            "version": EXPERIMENT_VERSION,
            "market": market,
            "dataset_sha256": dataset_sha,
            "protocol_sha256": protocol_sha,
            "split": split.to_dict(),
        }
    )
    registry_path = market_root / "registry.jsonl"
    registry = _load_registry(registry_path)
    registered = _load_registered_result(registry, identity)
    if registered is not None:
        return registered

    train = frame[frame["session"].isin(split.train)].copy()
    validation = frame[frame["session"].isin(split.validation)].copy()
    locked_test = frame[frame["session"].isin(split.locked_test)].copy()
    x_train = train[list(FEATURE_COLUMNS)]
    y_train = train["label"].to_numpy(dtype=int)
    x_validation = validation[list(FEATURE_COLUMNS)]
    x_test = locked_test[list(FEATURE_COLUMNS)]

    contenders = _build_models()
    validation_cards: dict[str, dict[str, Any]] = {}
    for name, contender in contenders.items():
        contender.fit(x_train, y_train)
        scores = contender.predict_proba(x_validation)[:, 1]
        validation_cards[name] = _scorecard(validation, scores)
    winner = max(
        contenders,
        key=lambda name: _validation_key(name, validation_cards[name]),
    )
    selected_model = contenders[winner]

    # The locked block is opened only after the winner and policies are fixed above.
    locked_scores = selected_model.predict_proba(x_test)[:, 1]
    locked_card = _scorecard(locked_test, locked_scores)
    top_one_metrics, top_one_selected = _selection_metrics(locked_test, locked_scores, 1)
    top_three_metrics, top_three_selected = _selection_metrics(locked_test, locked_scores, 3)

    # M14 ranks are valid percentile-like scores. Clip defensively so a malformed source
    # cannot make the Brier calculation accept something outside probability bounds.
    momentum_scores = (
        locked_test["return_20_rank"].fillna(0.0).clip(0.0, 1.0).to_numpy(dtype=float)
    )
    momentum_card = _scorecard(locked_test, momentum_scores)
    random_top_one = _random_opportunity_control(
        locked_test,
        count=1,
        observed_precision=float(top_one_metrics["precision"] or 0.0),
    )
    random_top_three = _random_opportunity_control(
        locked_test,
        count=3,
        observed_precision=float(top_three_metrics["precision"] or 0.0),
    )

    paths = _load_replay_paths(source_db, locked_test)
    replay_frame, replay_records = _build_replay_universe(locked_test, paths, market)
    replay_path, replay_sha = _write_gzip_rows(
        market_root / "replays", "universe", rows=replay_records
    )
    executable_top_one = _replay_metrics(top_one_selected, replay_frame)
    executable_top_three = _replay_metrics(top_three_selected, replay_frame)
    random_replay = _random_replay_control(
        locked_test,
        replay_frame,
        observed_accuracy=float(executable_top_one["strict_accuracy"] or 0.0),
        observed_mean_net_r=float(executable_top_one["mean_net_r"] or 0.0),
    )
    # Rows are available in the separately hash-bound replay ledger; avoid duplicating the
    # entire selected ledger inside the summary JSON.
    executable_top_one.pop("rows", None)
    executable_top_three.pop("rows", None)

    validation_precision = float(
        validation_cards[winner]["top_1"]["precision"] or 0.0
    )
    test_precision = float(top_one_metrics["precision"] or 0.0)
    executable_advantage = float(executable_top_one["mean_net_r"] or 0.0) - float(
        random_replay["mean_net_r"]
    )
    gates = {
        "locked_primary_selections": top_one_metrics["selected"] == TEST_SESSIONS,
        "top_1_precision_at_least_20pct": test_precision >= 0.20,
        "top_1_lift_at_least_3x": float(
            top_one_metrics["lift_over_prevalence"] or 0.0
        )
        >= 3.0,
        "top_1_beats_momentum_by_10pp": test_precision
        - float(momentum_card["top_1"]["precision"] or 0.0)
        >= 0.10,
        "opportunity_random_tail_at_most_005": random_top_one["tail_probability"] <= 0.05,
        "top_3_precision_at_least_15pct": float(top_three_metrics["precision"] or 0.0)
        >= 0.15,
        "top_3_lift_at_least_2x": float(
            top_three_metrics["lift_over_prevalence"] or 0.0
        )
        >= 2.0,
        "executable_accuracy_at_least_50pct": float(
            executable_top_one["strict_accuracy"] or 0.0
        )
        >= 0.50,
        "executable_mean_net_r_positive": float(executable_top_one["mean_net_r"] or 0.0)
        > 0.0,
        "executable_advantage_at_least_010r": executable_advantage >= 0.10,
        "executable_random_tail_at_most_005": random_replay["net_r_tail_probability"]
        <= 0.05,
        "validation_test_precision_gap_at_most_20pp": abs(
            validation_precision - test_precision
        )
        <= 0.20,
    }
    all_gates = all(gates.values())

    model_path = market_root / "models" / f"{identity}.joblib"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_temporary = model_path.with_suffix(".joblib.tmp")
    joblib.dump(
        {
            "version": EXPERIMENT_VERSION,
            "market": market,
            "experiment_id": identity,
            "protocol_sha256": protocol_sha,
            "dataset_sha256": dataset_sha,
            "feature_version": FEATURE_VERSION,
            "label_version": LABEL_VERSION,
            "features": FEATURE_COLUMNS,
            "model_name": winner,
            "model": selected_model,
        },
        model_temporary,
    )
    os.replace(model_temporary, model_path)
    model_sha = _hash_file(model_path)

    report: dict[str, Any] = {
        "version": EXPERIMENT_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": identity,
        "generated_at": observed_at.isoformat(),
        "purpose": "consumed_historical_development",
        "status": "development_candidate" if all_gates else "rejected_no_separability",
        "source": {
            **source,
            "db_path": str(source_db),
            "feature_version": FEATURE_VERSION,
            "label_version": LABEL_VERSION,
            "dataset_path": str(dataset_path),
            "dataset_sha256": dataset_sha,
            "dataset_rows": len(frame),
        },
        "protocol": {
            "path": str(PROTOCOL_PATH),
            "sha256": protocol_sha,
            "session_count": SESSION_COUNT,
            "random_repetitions": RANDOM_REPETITIONS,
        },
        "split": split.to_dict(),
        "validation": {
            "contenders": validation_cards,
            "selected_model": winner,
            "selection_rule": (
                "top_1_precision, then top_3_precision, then lower Brier, "
                "then logistic simplicity"
            ),
        },
        "locked_test": {
            "model": locked_card,
            "momentum_control": momentum_card,
            "random_top_1": random_top_one,
            "random_top_3": random_top_three,
        },
        "executable_replay": {
            "version": REPLAY_VERSION,
            "ledger_path": str(replay_path),
            "ledger_sha256": replay_sha,
            "top_1": executable_top_one,
            "top_3": executable_top_three,
            "matched_random_top_1": random_replay,
            "mean_net_r_advantage_over_random": executable_advantage,
        },
        "model_bundle": {
            "name": winner,
            "path": str(model_path),
            "sha256": model_sha,
            "diagnostics": _model_diagnostics(winner, selected_model),
        },
        "gates": gates,
        "all_development_candidate_gates_passed": all_gates,
        "existing_setup_coverage": (
            "reported independently by M14; never used to select or replace a model row"
        ),
        "active_model_changed": False,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "research_only",
        "detail": (
            "Historical locked-test research only. Even a passing development result "
            "requires a separately registered prospective cohort."
        ),
    }
    report["artifact_sha256"] = canonical_sha256(report)
    validate_separability_report(report)
    experiment_path = market_root / "experiments" / f"{identity}.json"
    latest_path = market_root / "latest.json"
    _atomic_json(experiment_path, report)
    _atomic_json(latest_path, report)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with registry_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "experiment_id": identity,
                    "market": market,
                    "dataset_sha256": dataset_sha,
                    "protocol_sha256": protocol_sha,
                    "report_path": str(experiment_path),
                    "report_sha256": report["artifact_sha256"],
                    "status": report["status"],
                    "registered_at": observed_at.isoformat(),
                },
                sort_keys=True,
            )
            + "\n"
        )
    return report


def load_latest_separability(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    spec_for(market)
    path = output_root / market / "latest.json"
    unavailable = {
        "version": EXPERIMENT_VERSION,
        "market": market,
        "status": "not_available",
        "markets_pooled": False,
        "active_model_changed": False,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "research_only",
        "detail": "No M15 result exists — unavailable is not a pass.",
    }
    if not path.exists():
        return unavailable
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("M15 latest artifact is not an object")
        validate_separability_report(report)
        bindings = (
            (report["source"]["dataset_path"], report["source"]["dataset_sha256"]),
            (
                report["executable_replay"]["ledger_path"],
                report["executable_replay"]["ledger_sha256"],
            ),
            (report["model_bundle"]["path"], report["model_bundle"]["sha256"]),
        )
        for artifact_path, expected in bindings:
            target = Path(str(artifact_path))
            if not target.exists() or _hash_file(target) != expected:
                raise ValueError(f"bound M15 artifact mismatch: {target}")
        return report
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            **unavailable,
            "status": "invalid_or_unreadable",
            "detail": f"M15 result is invalid — not a pass: {exc}",
        }
