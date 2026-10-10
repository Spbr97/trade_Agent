"""M17 full-universe intraday-path and execution-contract feasibility race.

The full liquid universe is ranked before intraday availability is inspected.  Only the
sealed primary, momentum and matched-random candidates are then acquired at M1/M5/M15.
This keeps collection sparse without allowing path availability to alter a selection.

All output is research-only.  Historical M17 evidence overlaps M15/M16 development data,
so even a complete historical pass may only register a fresh forward observer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.leader_discovery import (
    FEATURE_COLUMNS,
    extract_causal_universe,
    spec_for,
)
from tradedesk.markets import bse_market, crypto_market, nse_market
from tradedesk.models import TradeType, price_decimal
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "intraday-contract-m17-v1"
MANIFEST_VERSION = "intraday-acquisition-manifest-v1"
PATH_BUNDLE_VERSION = "intraday-path-bundle-v1"
MODEL_VERSION = "intraday-contract-ranker-v1"
REGISTRATION_VERSION = "intraday-contract-registration-v1"
PROTOCOL_PATH = Path("docs/self-learning-m17-intraday-contract-protocol.md")
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/intraday_contract_race")

SESSION_COUNT = 180
TRAIN_SESSIONS = 102
PURGE_SESSIONS = 3
VALIDATION_SESSIONS = 36
DIAGNOSTIC_SESSIONS = 36
RANKER_SEED = 17
RANDOM_CANDIDATE_SEED = 1701
RANDOM_REPETITIONS = 2_000
RANDOM_REPLAY_SEED = 1717
RANDOM_CANDIDATES_PER_SESSION = 20
RESEARCH_CAPITAL = 100_000.0
RISK_BUDGET = 250.0
ENTRY_WINDOW_MINUTES = 120
MIN_PATHS = 32
MIN_FILLS = 24
PROSPECTIVE_MIN_CALLS = 60
PROSPECTIVE_MIN_SESSIONS = 40

MarketName = Literal["nse", "bse", "crypto"]
EntryRule = Literal["prior_close_pullback", "opening_range_breakout", "next_open"]

CONTRACTS: tuple[tuple[EntryRule, float], ...] = (
    ("prior_close_pullback", 0.50),
    ("prior_close_pullback", 0.75),
    ("opening_range_breakout", 0.50),
    ("opening_range_breakout", 0.75),
    ("next_open", 0.50),
    ("next_open", 0.75),
)

DB_PATHS: dict[str, Path] = {
    "nse": Path("data/tradedesk.duckdb"),
    "bse": Path("data/bse.duckdb"),
    "crypto": Path("data/crypto.duckdb"),
}


def _protocol_sha256() -> str:
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(f"frozen M17 protocol missing: {PROTOCOL_PATH}")
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _append_registry(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")


def _write_gzip_jsonl(
    directory: Path, prefix: str, rows: Iterable[Mapping[str, Any]]
) -> tuple[Path, str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{prefix}.{os.getpid()}.tmp.gz"
    count = 0
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                count += 1
        digest = _hash_file(temporary)
        destination = directory / f"{prefix}-{digest}.jsonl.gz"
        if destination.exists():
            if _hash_file(destination) != digest:
                raise ValueError(f"existing M17 {prefix} artifact digest mismatch")
        else:
            os.replace(temporary, destination)
        return destination, digest, count
    finally:
        temporary.unlink(missing_ok=True)


def _json_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _ranker() -> Pipeline:
    return Pipeline(
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
                    random_state=RANKER_SEED,
                    n_jobs=1,
                ),
            ),
        ]
    )


def _model_frame(extracted: pd.DataFrame) -> pd.DataFrame:
    frame = extracted.rename(
        columns={"session_date": "session", "opportunity_label": "label"}
    ).copy()
    frame["session"] = pd.to_datetime(frame["session"]).dt.date.map(date.isoformat)
    required = {"session", "scrip_code", "symbol", "label", "close", "atr_14_pct"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("M17 source missing columns: " + ", ".join(sorted(missing)))
    frame.sort_values(["session", "scrip_code"], inplace=True)
    return frame


def _split_sessions(sessions: Sequence[str]) -> dict[str, list[str]]:
    ordered = sorted(set(sessions))
    if len(ordered) != SESSION_COUNT:
        raise ValueError(f"M17 requires exactly {SESSION_COUNT} sessions, got {len(ordered)}")
    train_end = TRAIN_SESSIONS
    validation_start = train_end + PURGE_SESSIONS
    validation_end = validation_start + VALIDATION_SESSIONS
    diagnostic_start = validation_end + PURGE_SESSIONS
    split = {
        "train": ordered[:train_end],
        "purge_1": ordered[train_end:validation_start],
        "validation": ordered[validation_start:validation_end],
        "purge_2": ordered[validation_end:diagnostic_start],
        "diagnostic": ordered[diagnostic_start:],
    }
    if len(split["diagnostic"]) != DIAGNOSTIC_SESSIONS:
        raise AssertionError("M17 split constants do not total 180 sessions")
    return split


def _source_rows(frame: pd.DataFrame, market: str) -> Iterable[dict[str, Any]]:
    for row in frame.itertuples(index=False):
        yield {
            "version": VERSION,
            "market": market,
            "session": row.session,
            "scrip_code": row.scrip_code,
            "symbol": row.symbol,
            "features": {
                name: _json_number(getattr(row, name)) for name in FEATURE_COLUMNS
            },
            "opportunity_label": int(row.label),
            "decision_close": float(row.close),
            "atr_14_pct": float(row.atr_14_pct),
        }


_NEXT_SESSION_SQL = """
WITH daily AS (
    SELECT
        scrip_code,
        ts,
        CAST(timezone('Asia/Kolkata', to_timestamp(ts)) AS DATE) AS session_date,
        lead(ts, 1) OVER (PARTITION BY scrip_code ORDER BY ts) AS next_ts
    FROM candles
    WHERE interval = '1day'
), matched AS (
    SELECT d.scrip_code, d.session_date, d.next_ts
    FROM daily d
    JOIN _m17_keys k
      ON k.scrip_code = d.scrip_code AND k.session_date = d.session_date
)
SELECT * FROM matched ORDER BY session_date, scrip_code
"""


def _attach_windows(
    db_path: Path, market: str, candidate_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    keys = pd.DataFrame(
        {
            "scrip_code": [row["scrip_code"] for row in candidate_rows],
            "session_date": [date.fromisoformat(row["session"]) for row in candidate_rows],
        }
    ).drop_duplicates()
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        connection.register("_m17_keys", keys)
        windows = connection.execute(_NEXT_SESSION_SQL).df()
        connection.unregister("_m17_keys")
    finally:
        connection.close()
    by_key = {
        (
            str(row.scrip_code),
            pd.Timestamp(row.session_date).date().isoformat(),
        ): row.next_ts
        for row in windows.itertuples(index=False)
    }
    output: list[dict[str, Any]] = []
    for row in candidate_rows:
        next_ts = by_key.get((row["scrip_code"], row["session"]))
        item = dict(row)
        if next_ts is None or pd.isna(next_ts):
            item.update(
                {
                    "window_status": "unavailable_no_next_daily_session",
                    "window_start": None,
                    "window_end": None,
                }
            )
        elif market == "crypto":
            start = datetime.fromtimestamp(int(next_ts), tz=IST)
            item.update(
                {
                    "window_status": "sealed",
                    "window_start": start.isoformat(),
                    "window_end": (start + timedelta(days=1)).isoformat(),
                }
            )
        else:
            next_day = datetime.fromtimestamp(int(next_ts), tz=IST).date()
            start = datetime.combine(next_day, time(9, 15), tzinfo=IST)
            end = datetime.combine(next_day, time(15, 30), tzinfo=IST)
            item.update(
                {
                    "window_status": "sealed",
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                }
            )
        output.append(item)
    return output


def _save_model(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        joblib.dump(dict(payload), temporary)
        digest = _hash_file(temporary)
        if path.exists():
            if _hash_file(path) != digest:
                raise ValueError("existing M17 model artifact digest mismatch")
        else:
            os.replace(temporary, path)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError("unsupported M17 manifest version")
    if manifest.get("market") not in DB_PATHS:
        raise ValueError("unsupported M17 manifest market")
    if manifest.get("markets_pooled") is not False:
        raise ValueError("M17 markets must never be pooled")
    if manifest.get("protocol_sha256") != _protocol_sha256():
        raise ValueError("M17 protocol binding mismatch")
    if manifest.get("authority") != "research_only":
        raise ValueError("M17 manifest authority mismatch")
    if manifest.get("eligible_for_live") is not False:
        raise ValueError("M17 manifest cannot authorize live use")
    acquisition = manifest.get("acquisition_rows") or {}
    rows = int(acquisition.get("rows", 0))
    sealed = int(acquisition.get("sealed_windows", 0))
    unavailable = int(acquisition.get("unavailable_windows", 0))
    if rows <= 0 or sealed <= 0 or sealed + unavailable != rows:
        raise ValueError("M17 manifest contains no valid sealed acquisition population")
    supplied = manifest.get("artifact_sha256")
    body = dict(manifest)
    body.pop("artifact_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M17 manifest hash mismatch")


def build_acquisition_manifest(
    *,
    market: str,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Seal candidates and exact path windows without reading any intraday row."""

    spec_for(market)
    market_root = output_root / market
    latest_path = market_root / "manifest.json"
    if latest_path.exists():
        existing = json.loads(latest_path.read_text(encoding="utf-8"))
        validate_manifest(existing)
        return existing

    source_db = db_path or DB_PATHS[market]
    observed_at = now or datetime.now(IST)
    extracted, source = extract_causal_universe(
        source_db, market, now=observed_at, session_count=SESSION_COUNT
    )
    frame = _model_frame(extracted)
    split = _split_sessions(list(frame["session"]))
    train = frame[frame["session"].isin(split["train"])].copy()
    evaluation = frame[
        frame["session"].isin(split["validation"] + split["diagnostic"])
    ].copy()
    model = _ranker()
    model.fit(train[list(FEATURE_COLUMNS)], train["label"].to_numpy(dtype=int))
    evaluation["ranker_score"] = model.predict_proba(
        evaluation[list(FEATURE_COLUMNS)]
    )[:, 1]

    rng = np.random.default_rng(RANDOM_CANDIDATE_SEED)
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for session, group in evaluation.groupby("session", sort=True):
        ranked = group.sort_values(
            ["ranker_score", "scrip_code"], ascending=[False, True]
        )
        for rank, row in enumerate(ranked.head(3).itertuples(index=False), start=1):
            key = (str(session), str(row.scrip_code))
            selected.setdefault(
                key,
                {
                    "session": str(session),
                    "scrip_code": str(row.scrip_code),
                    "symbol": str(row.symbol),
                    "decision_close": float(row.close),
                    "atr_14_pct": float(row.atr_14_pct),
                    "ranker_score": float(row.ranker_score),
                    "return_20_rank": float(row.return_20_rank),
                    "roles": [],
                },
            )["roles"].append(f"ranker_top_{rank}")

        momentum = group.sort_values(
            ["return_20_rank", "scrip_code"], ascending=[False, True]
        ).iloc[0]
        key = (str(session), str(momentum["scrip_code"]))
        selected.setdefault(
            key,
            {
                "session": str(session),
                "scrip_code": str(momentum["scrip_code"]),
                "symbol": str(momentum["symbol"]),
                "decision_close": float(momentum["close"]),
                "atr_14_pct": float(momentum["atr_14_pct"]),
                "ranker_score": float(momentum["ranker_score"]),
                "return_20_rank": float(momentum["return_20_rank"]),
                "roles": [],
            },
        )["roles"].append("momentum_top_1")

        random_size = min(RANDOM_CANDIDATES_PER_SESSION, len(group))
        random_indices = rng.choice(len(group), size=random_size, replace=False)
        random_rows = group.iloc[np.sort(random_indices)].sort_values("scrip_code")
        for random_rank, row in enumerate(random_rows.itertuples(index=False), start=1):
            key = (str(session), str(row.scrip_code))
            selected.setdefault(
                key,
                {
                    "session": str(session),
                    "scrip_code": str(row.scrip_code),
                    "symbol": str(row.symbol),
                    "decision_close": float(row.close),
                    "atr_14_pct": float(row.atr_14_pct),
                    "ranker_score": float(row.ranker_score),
                    "return_20_rank": float(row.return_20_rank),
                    "roles": [],
                },
            )["roles"].append(f"random_{random_rank:02d}")

    candidate_rows = sorted(selected.values(), key=lambda row: (row["session"], row["scrip_code"]))
    for row in candidate_rows:
        row["roles"] = sorted(set(row["roles"]))
        row["block"] = (
            "validation" if row["session"] in split["validation"] else "diagnostic"
        )
    candidate_rows = _attach_windows(source_db, market, candidate_rows)

    source_path, source_sha, source_count = _write_gzip_jsonl(
        market_root / "datasets", "causal-source", _source_rows(frame, market)
    )
    manifest_rows_path, manifest_rows_sha, manifest_row_count = _write_gzip_jsonl(
        market_root / "manifests",
        "acquisition",
        (
            {
                "version": MANIFEST_VERSION,
                "market": market,
                **row,
            }
            for row in candidate_rows
        ),
    )
    protocol_sha = _protocol_sha256()
    experiment_id = canonical_sha256(
        {
            "version": VERSION,
            "market": market,
            "protocol_sha256": protocol_sha,
            "source_sha256": source_sha,
            "manifest_rows_sha256": manifest_rows_sha,
            "split": split,
        }
    )
    model_path = market_root / "models" / f"{experiment_id}-development.joblib"
    model_sha = _save_model(
        model_path,
        {
            "version": MODEL_VERSION,
            "market": market,
            "experiment_id": experiment_id,
            "protocol_sha256": protocol_sha,
            "source_sha256": source_sha,
            "features": FEATURE_COLUMNS,
            "model": model,
        },
    )
    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": experiment_id,
        "sealed_at": observed_at.isoformat(),
        "protocol_path": str(PROTOCOL_PATH),
        "protocol_sha256": protocol_sha,
        "db_path": str(source_db),
        "source": {
            **source,
            "path": str(source_path),
            "sha256": source_sha,
            "rows": source_count,
        },
        "split": split,
        "ranker": {
            "path": str(model_path),
            "sha256": model_sha,
            "version": MODEL_VERSION,
            "seed": RANKER_SEED,
        },
        "acquisition_rows": {
            "path": str(manifest_rows_path),
            "sha256": manifest_rows_sha,
            "rows": manifest_row_count,
            "sealed_windows": sum(
                row["window_status"] == "sealed" for row in candidate_rows
            ),
            "unavailable_windows": sum(
                row["window_status"] != "sealed" for row in candidate_rows
            ),
        },
        "intervals": [Interval.M1.value, Interval.M5.value, Interval.M15.value],
        "random_candidates_per_session": RANDOM_CANDIDATES_PER_SESSION,
        "authority": "research_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
    }
    manifest["artifact_sha256"] = canonical_sha256(manifest)
    validate_manifest(manifest)
    _atomic_json(latest_path, manifest)
    _append_registry(
        market_root / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": experiment_id,
            "event": "manifest_sealed",
            "at": observed_at.isoformat(),
            "artifact_sha256": manifest["artifact_sha256"],
        },
    )
    return manifest


def _read_manifest_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = Path(str(manifest["acquisition_rows"]["path"]))
    if _hash_file(path) != manifest["acquisition_rows"]["sha256"]:
        raise ValueError("M17 acquisition row artifact binding mismatch")
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"M17 manifest row {line_number} is not an object")
            if value.get("market") != manifest["market"]:
                raise ValueError("M17 manifest row market mismatch")
            rows.append(value)
    if len(rows) != int(manifest["acquisition_rows"]["rows"]):
        raise ValueError("M17 manifest row count mismatch")
    return rows


def load_manifest(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    path = output_root / market / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"M17 acquisition manifest missing for {market}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M17 manifest is not an object")
    validate_manifest(value)
    _read_manifest_rows(value)
    return value


def _aggregate_candles(
    candles: Sequence[Candle], interval: Interval, *, origin: datetime
) -> list[Candle]:
    """Losslessly derive a coarser interval from M1 with buckets anchored to the window."""

    if interval not in {Interval.M5, Interval.M15}:
        raise ValueError("M17 aggregation supports only M5/M15")
    if any(candle.interval is not Interval.M1 for candle in candles):
        raise ValueError("M17 aggregation source must be M1")
    seconds = interval.seconds
    buckets: dict[int, list[Candle]] = {}
    for candle in sorted(candles, key=lambda value: value.ts):
        offset = int((candle.ts - origin).total_seconds())
        if offset < 0:
            continue
        buckets.setdefault(offset // seconds, []).append(candle)
    output: list[Candle] = []
    expected = seconds // Interval.M1.seconds
    for bucket, values in sorted(buckets.items()):
        if len(values) != expected:
            continue
        timestamps = [value.ts for value in values]
        first = origin + timedelta(seconds=bucket * seconds)
        required = [first + timedelta(minutes=index) for index in range(expected)]
        if timestamps != required:
            continue
        output.append(
            Candle(
                scrip_code=values[0].scrip_code,
                interval=interval,
                ts=first,
                open=values[0].open,
                high=max(value.high for value in values),
                low=min(value.low for value in values),
                close=values[-1].close,
                volume=sum(value.volume for value in values),
            )
        )
    return output


async def _fetch_with_invalid_code_isolation(
    client: Any,
    interval: Interval,
    codes: Sequence[str],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, list[Candle]], list[dict[str, str]]]:
    """Keep one obsolete scrip code from discarding every healthy code in its API batch."""

    if not codes:
        return {}, []
    try:
        return await client.candles_history(interval, codes, start, end), []
    except Exception as exc:
        if "invalid scrip" not in str(exc).lower():
            raise
        if len(codes) == 1:
            return {codes[0]: []}, [
                {
                    "scrip_code": codes[0],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            ]
        midpoint = len(codes) // 2
        left, left_errors = await _fetch_with_invalid_code_isolation(
            client, interval, codes[:midpoint], start, end
        )
        right, right_errors = await _fetch_with_invalid_code_isolation(
            client, interval, codes[midpoint:], start, end
        )
        return {**left, **right}, left_errors + right_errors


def _complete_codes(
    db_path: Path,
    codes: Sequence[str],
    interval: Interval,
    *,
    start: datetime,
    end: datetime,
) -> set[str]:
    expected = _expected_index(start, end, interval.seconds // 60)
    with CandleStore(db_path) as store:
        return {
            code
            for code in codes
            if store.load(code, interval, start=start, end=end).index.equals(expected)
        }


async def acquire_manifest_paths(
    *,
    market: str,
    client: Any,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Fetch only sealed sparse windows; repeated calls are safe and idempotent."""

    manifest = load_manifest(market, output_root=output_root)
    rows = _read_manifest_rows(manifest)
    source_db = db_path or Path(str(manifest["db_path"]))
    now = observed_at or datetime.now(IST)

    groups: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        if row["window_status"] != "sealed":
            continue
        groups.setdefault((row["window_start"], row["window_end"]), set()).add(
            row["scrip_code"]
        )

    if market == "crypto" and hasattr(client, "register_pairs"):
        with CandleStore(source_db) as store:
            all_codes = sorted({code for codes in groups.values() for code in codes})
            client.register_pairs(store.custom_symbols(all_codes))

    ledger: list[dict[str, Any]] = []
    observed_intervals = (
        (Interval.M1, Interval.M15)
        if market == "crypto"
        else (Interval.M1, Interval.M5, Interval.M15)
    )
    for (start_text, end_text), code_set in sorted(groups.items()):
        start = datetime.fromisoformat(start_text)
        end = datetime.fromisoformat(end_text)
        codes = sorted(code_set)
        for interval in observed_intervals:
            already_present = _complete_codes(
                source_db, codes, interval, start=start, end=end
            )
            requested_codes = [code for code in codes if code not in already_present]
            record: dict[str, Any] = {
                "version": VERSION,
                "market": market,
                "experiment_id": manifest["experiment_id"],
                "requested_at": now.isoformat(),
                "window_start": start_text,
                "window_end": end_text,
                "interval": interval.value,
                "codes": codes,
                "requested_codes": requested_codes,
                "already_present_codes": sorted(already_present),
                "source": "observed_api",
            }
            try:
                fetched, isolated_errors = await _fetch_with_invalid_code_isolation(
                    client, interval, requested_codes, start, end
                )
                candles = [
                    candle
                    for code in requested_codes
                    for candle in fetched.get(code, [])
                    if start <= candle.ts < end
                ]
                with CandleStore(source_db) as store:
                    stored = store.upsert_candles(candles)
                record.update(
                    {
                        "status": "completed" if not isolated_errors else "partial",
                        "returned_bars": len(candles),
                        "stored_bars": stored,
                        "isolated_errors": isolated_errors,
                        "bars_by_code": {
                            code: len(
                                [
                                    candle
                                    for candle in fetched.get(code, [])
                                    if start <= candle.ts < end
                                ]
                            )
                            for code in requested_codes
                        },
                    }
                )
            except Exception as exc:  # acquisition errors are evidence, never passes
                record.update(
                    {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "returned_bars": 0,
                        "stored_bars": 0,
                    }
                )
            ledger.append(record)

        if market == "crypto":
            derived_record: dict[str, Any] = {
                "version": VERSION,
                "market": market,
                "experiment_id": manifest["experiment_id"],
                "requested_at": now.isoformat(),
                "window_start": start_text,
                "window_end": end_text,
                "interval": Interval.M5.value,
                "codes": codes,
                "source": "derived_from_observed_m1",
            }
            try:
                already_present = _complete_codes(
                    source_db, codes, Interval.M5, start=start, end=end
                )
                to_derive = [code for code in codes if code not in already_present]
                derived: list[Candle] = []
                missing_m1: list[str] = []
                expected_m1 = _expected_index(start, end, 1)
                with CandleStore(source_db) as store:
                    for code in to_derive:
                        frame = store.load(code, Interval.M1, start=start, end=end)
                        if not frame.index.equals(expected_m1):
                            missing_m1.append(code)
                            continue
                        aggregated = _aggregate_frame(frame, 5, start=start)
                        derived.extend(
                            Candle(
                                scrip_code=code,
                                interval=Interval.M5,
                                ts=timestamp.to_pydatetime(),
                                open=float(bar["open"]),
                                high=float(bar["high"]),
                                low=float(bar["low"]),
                                close=float(bar["close"]),
                                volume=int(bar["volume"]),
                            )
                            for timestamp, bar in aggregated.iterrows()
                        )
                    stored = store.upsert_candles(derived)
                derived_record.update(
                    {
                        "status": "completed" if not missing_m1 else "partial",
                        "already_present_codes": sorted(already_present),
                        "derived_codes": sorted(set(to_derive) - set(missing_m1)),
                        "missing_m1_codes": missing_m1,
                        "returned_bars": len(derived),
                        "stored_bars": stored,
                    }
                )
            except Exception as exc:
                derived_record.update(
                    {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "returned_bars": 0,
                        "stored_bars": 0,
                    }
                )
            ledger.append(derived_record)

    market_root = output_root / market
    ledger_path, ledger_sha, ledger_rows = _write_gzip_jsonl(
        market_root / "acquisition", "requests", ledger
    )
    errors = sum(record["status"] != "completed" for record in ledger)
    summary: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "experiment_id": manifest["experiment_id"],
        "generated_at": now.isoformat(),
        "manifest_sha256": manifest["artifact_sha256"],
        "ledger_path": str(ledger_path),
        "ledger_sha256": ledger_sha,
        "request_records": ledger_rows,
        "completed_records": ledger_rows - errors,
        "error_records": errors,
        "returned_bars": sum(int(record.get("returned_bars", 0)) for record in ledger),
        "status": "acquired" if errors == 0 else "acquisition_incomplete",
        "authority": "research_only",
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
    }
    summary["artifact_sha256"] = canonical_sha256(summary)
    _atomic_json(market_root / "acquisition.json", summary)
    _append_registry(
        market_root / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": manifest["experiment_id"],
            "event": "acquisition_attempted",
            "at": now.isoformat(),
            "status": summary["status"],
            "artifact_sha256": summary["artifact_sha256"],
        },
    )
    return summary


def _expected_index(start: datetime, end: datetime, minutes: int) -> pd.DatetimeIndex:
    return pd.date_range(
        start=start,
        end=end - timedelta(minutes=minutes),
        freq=f"{minutes}min",
    )


def _frame_payload(frame: pd.DataFrame) -> list[list[Any]]:
    return [
        [
            timestamp.isoformat(),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            int(row.volume),
        ]
        for timestamp, row in frame.iterrows()
    ]


def _aggregate_frame(
    frame: pd.DataFrame, minutes: int, *, start: datetime
) -> pd.DataFrame:
    group_size = minutes
    records: list[dict[str, Any]] = []
    for offset in range(0, len(frame), group_size):
        group = frame.iloc[offset : offset + group_size]
        if len(group) != group_size:
            continue
        records.append(
            {
                "ts": start + timedelta(minutes=offset),
                "open": float(group.iloc[0]["open"]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group.iloc[-1]["close"]),
                "volume": int(group["volume"].sum()),
            }
        )
    if not records:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(records).set_index("ts")


def validate_intraday_path(
    m1: pd.DataFrame,
    m5: pd.DataFrame,
    m15: pd.DataFrame,
    *,
    start: datetime,
    end: datetime,
) -> tuple[bool, str]:
    """Require an exact M1 grid and lossless M5/M15 agreement."""

    frames = {1: m1, 5: m5, 15: m15}
    required_columns = ["open", "high", "low", "close", "volume"]
    for minutes, frame in frames.items():
        if list(frame.columns) != required_columns:
            return False, f"m{minutes}_columns_mismatch"
        if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
            return False, f"m{minutes}_unordered_or_duplicate"
        expected = _expected_index(start, end, minutes)
        if not frame.index.equals(expected):
            return False, f"m{minutes}_timestamp_grid_mismatch"
        numeric = frame[required_columns].to_numpy(dtype=float)
        if not np.isfinite(numeric).all():
            return False, f"m{minutes}_nonfinite_value"
        if (
            (frame["open"] <= 0).any()
            or (frame["high"] <= 0).any()
            or (frame["low"] <= 0).any()
            or (frame["close"] <= 0).any()
            or (frame["volume"] < 0).any()
        ):
            return False, f"m{minutes}_invalid_value"
        if (
            (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()
        ):
            return False, f"m{minutes}_invalid_ohlc_geometry"

    for minutes, observed in ((5, m5), (15, m15)):
        aggregated = _aggregate_frame(m1, minutes, start=start)
        if not aggregated.index.equals(observed.index):
            return False, f"m{minutes}_aggregate_index_mismatch"
        for column in ("open", "high", "low", "close"):
            if not np.allclose(
                aggregated[column].to_numpy(dtype=float),
                observed[column].to_numpy(dtype=float),
                rtol=1e-8,
                atol=1e-8,
            ):
                return False, f"m{minutes}_{column}_aggregate_mismatch"
        if not np.array_equal(
            aggregated["volume"].to_numpy(dtype=np.int64),
            observed["volume"].to_numpy(dtype=np.int64),
        ):
            return False, f"m{minutes}_volume_aggregate_mismatch"
    return True, "complete_and_consistent"


def _load_path_record(store: CandleStore, row: Mapping[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": PATH_BUNDLE_VERSION,
        "market": row["market"],
        "block": row["block"],
        "session": row["session"],
        "scrip_code": row["scrip_code"],
        "symbol": row["symbol"],
        "roles": list(row["roles"]),
        "decision_close": float(row["decision_close"]),
        "atr_14_pct": float(row["atr_14_pct"]),
        "ranker_score": float(row["ranker_score"]),
        "return_20_rank": float(row["return_20_rank"]),
        "window_start": row["window_start"],
        "window_end": row["window_end"],
    }
    if row["window_status"] != "sealed":
        return {
            **base,
            "path_status": "invalid_or_unavailable",
            "path_detail": row["window_status"],
            "m1": [],
            "m5": [],
            "m15": [],
        }
    start = datetime.fromisoformat(str(row["window_start"]))
    end = datetime.fromisoformat(str(row["window_end"]))
    m1 = store.load(row["scrip_code"], Interval.M1, start=start, end=end)
    m5 = store.load(row["scrip_code"], Interval.M5, start=start, end=end)
    m15 = store.load(row["scrip_code"], Interval.M15, start=start, end=end)
    valid, detail = validate_intraday_path(m1, m5, m15, start=start, end=end)
    record = {
        **base,
        "path_status": "valid" if valid else "invalid_or_unavailable",
        "path_detail": detail,
        "m1": _frame_payload(m1),
        "m5": _frame_payload(m5),
        "m15": _frame_payload(m15),
    }
    record["path_sha256"] = canonical_sha256(record)
    return record


def _frame_from_payload(rows: Sequence[Sequence[Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(
        rows, columns=["ts", "open", "high", "low", "close", "volume"]
    )
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST)
    frame.set_index("ts", inplace=True)
    frame.index.name = "ts"
    frame["volume"] = frame["volume"].astype("int64")
    return frame


def _market_bundle(market: str) -> Any:
    settings = load_config(".")
    return {"nse": nse_market, "bse": bse_market, "crypto": crypto_market}[market](
        settings
    )


def _entry_candidate(
    m1: pd.DataFrame, entry_rule: EntryRule, decision_close: float
) -> tuple[int | None, float | None, str]:
    if m1.empty:
        return None, None, "invalid_empty_path"
    if entry_rule == "next_open":
        return 0, float(m1.iloc[0]["open"]), "next_open"
    if entry_rule == "prior_close_pullback":
        first_open = float(m1.iloc[0]["open"])
        if first_open <= decision_close:
            return None, None, "never_triggered_no_positive_gap"
        for index in range(min(ENTRY_WINDOW_MINUTES, len(m1))):
            bar = m1.iloc[index]
            if float(bar["low"]) <= decision_close:
                raw_entry = min(float(bar["open"]), decision_close)
                return index, raw_entry, "prior_close_pullback"
        return None, None, "never_triggered_pullback"
    if entry_rule == "opening_range_breakout":
        if len(m1) < 16:
            return None, None, "invalid_opening_range"
        trigger = float(m1.iloc[:15]["high"].max())
        for index in range(15, min(ENTRY_WINDOW_MINUTES, len(m1))):
            bar = m1.iloc[index]
            if float(bar["open"]) >= trigger:
                return index, float(bar["open"]), "opening_range_gap"
            if float(bar["high"]) >= trigger:
                return index, trigger, "opening_range_breakout"
        return None, None, "never_triggered_opening_range"
    raise ValueError(f"unsupported M17 entry rule: {entry_rule}")


def replay_contract(
    path: Mapping[str, Any], *, entry_rule: EntryRule, target_r: float
) -> dict[str, Any]:
    """Resolve one frozen M17 contract from an already integrity-checked M1 path."""

    if path.get("path_status") != "valid":
        return {
            "status": "invalid_or_unavailable",
            "event": path.get("path_detail", "invalid_path"),
            "strict_success": None,
            "net_r": None,
        }
    market = str(path["market"])
    bundle = _market_bundle(market)
    m1 = _frame_from_payload(path["m1"])
    entry_index, raw_entry, entry_event = _entry_candidate(
        m1, entry_rule, float(path["decision_close"])
    )
    if entry_index is None or raw_entry is None:
        return {
            "status": "never_triggered",
            "event": entry_event,
            "strict_success": None,
            "net_r": None,
        }

    slippage = float(bundle.costs.slippage_pct)
    entry = raw_entry * (1.0 + slippage)
    atr = float(path["decision_close"]) * float(path["atr_14_pct"])
    floor = 0.04 if market == "crypto" else 0.01
    risk_distance = max(0.50 * atr, floor * entry)
    stop = entry - risk_distance
    target = entry + target_r * risk_distance
    if not all(math.isfinite(value) for value in (entry, stop, target)) or stop <= 0:
        return {
            "status": "invalid_geometry",
            "event": "invalid_geometry",
            "strict_success": None,
            "net_r": None,
        }

    raw_qty = min(RISK_BUDGET / risk_distance, RESEARCH_CAPITAL / entry)
    if market == "crypto":
        step = float(bundle.qty_step)
        quantity = math.floor(raw_qty / step) * step
    else:
        quantity = float(math.floor(raw_qty))
    if quantity <= 0 or quantity * entry < float(bundle.min_notional_inr):
        return {
            "status": "not_sizeable",
            "event": "not_sizeable",
            "strict_success": None,
            "net_r": None,
        }

    event = "timeout"
    exit_raw = float(m1.iloc[-1]["close"])
    exit_index = len(m1) - 1
    for index in range(entry_index, len(m1)):
        bar = m1.iloc[index]
        open_price = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        if index > entry_index and open_price <= stop:
            event, exit_raw, exit_index = "gap_stop", open_price, index
            break
        if index > entry_index and open_price >= target:
            event, exit_raw, exit_index = "gap_target", open_price, index
            break
        # Conservative same-minute ordering: a stop wins any ambiguous M1 bar.
        if low <= stop:
            event, exit_raw, exit_index = "stop", stop, index
            break
        if high >= target:
            event, exit_raw, exit_index = "target", target, index
            break

    exit_execution = exit_raw * (1.0 - slippage)
    net_r = float(
        bundle.costs.net_r_multiple(
            trade_type=TradeType.INTRADAY,
            qty=quantity,
            entry=price_decimal(entry),
            stop=price_decimal(stop),
            exit_price=price_decimal(exit_execution),
        )
    )
    return {
        "status": "resolved",
        "event": event,
        "entry_event": entry_event,
        "strict_success": event in {"target", "gap_target"},
        "entry_index": entry_index,
        "exit_index": exit_index,
        "entry": entry,
        "stop": stop,
        "target": target,
        "quantity": quantity,
        "exit_price_before_slippage": exit_raw,
        "exit_execution_price": exit_execution,
        "gross_r": (exit_raw - entry) / risk_distance,
        "net_r": net_r,
    }


def _wilson(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    probability = successes / total
    denominator = 1.0 + z * z / total
    centre = (probability + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1.0 - probability) / total
            + z * z / (4.0 * total * total)
        )
        / denominator
    )
    return centre - margin, centre + margin


def _maximum_losing_streak(values: Sequence[bool]) -> int:
    maximum = 0
    current = 0
    for value in values:
        current = 0 if value else current + 1
        maximum = max(maximum, current)
    return maximum


def _contract_metrics(
    rows: Sequence[Mapping[str, Any]], *, total_sessions: int
) -> dict[str, Any]:
    path_valid = [row for row in rows if row["path_status"] == "valid"]
    resolved = [row for row in rows if row["replay"]["status"] == "resolved"]
    no_calls = [row for row in rows if row["replay"]["status"] == "never_triggered"]
    successes = sum(bool(row["replay"]["strict_success"]) for row in resolved)
    lower, upper = _wilson(successes, len(resolved))
    ordered = sorted(resolved, key=lambda row: (row["session"], row["scrip_code"]))
    events = Counter(str(row["replay"]["event"]) for row in resolved)
    return {
        "selections": len(rows),
        "total_sessions": total_sessions,
        "valid_paths": len(path_valid),
        "invalid_paths": len(rows) - len(path_valid),
        "filled": len(resolved),
        "filled_sessions": len({row["session"] for row in resolved}),
        "never_triggered": len(no_calls),
        "no_call_frequency": len(no_calls) / len(path_valid) if path_valid else None,
        "strict_successes": successes,
        "strict_accuracy": successes / len(resolved) if resolved else None,
        "wilson_95_lower": lower,
        "wilson_95_upper": upper,
        "mean_net_r": (
            float(np.mean([float(row["replay"]["net_r"]) for row in resolved]))
            if resolved
            else None
        ),
        "median_net_r": (
            float(np.median([float(row["replay"]["net_r"]) for row in resolved]))
            if resolved
            else None
        ),
        "maximum_losing_streak": _maximum_losing_streak(
            [bool(row["replay"]["strict_success"]) for row in ordered]
        ),
        "events": dict(sorted(events.items())),
    }


def _has_role(path: Mapping[str, Any], role: str) -> bool:
    return role in set(path.get("roles", []))


def _score_paths(
    paths: Sequence[Mapping[str, Any]],
    *,
    role: str,
    entry_rule: EntryRule,
    target_r: float,
) -> list[dict[str, Any]]:
    selected = [path for path in paths if _has_role(path, role)]
    return [
        {
            **path,
            "replay": replay_contract(path, entry_rule=entry_rule, target_r=target_r),
        }
        for path in selected
    ]


def _score_random_paths(
    paths: Sequence[Mapping[str, Any]], *, entry_rule: EntryRule, target_r: float
) -> list[dict[str, Any]]:
    selected = [
        path
        for path in paths
        if any(str(role).startswith("random_") for role in path.get("roles", []))
    ]
    return [
        {
            **path,
            "replay": replay_contract(path, entry_rule=entry_rule, target_r=target_r),
        }
        for path in selected
    ]


def _matched_random_control(
    paths: Sequence[Mapping[str, Any]],
    *,
    entry_rule: EntryRule,
    target_r: float,
    observed: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    scored = _score_random_paths(paths, entry_rule=entry_rule, target_r=target_r)
    expected_counts: dict[str, int] = {}
    for path in paths:
        if any(str(role).startswith("random_") for role in path.get("roles", [])):
            session = str(path["session"])
            expected_counts[session] = expected_counts.get(session, 0) + 1
    groups = {
        str(session): group.to_dict(orient="records")
        for session, group in pd.DataFrame(scored).groupby("session")
    }
    expected_sessions = sorted(
        {str(path["session"]) for path in paths if _has_role(path, "ranker_top_1")}
    )
    if len(groups) != len(expected_sessions):
        return {
            "status": "not_available",
            "detail": "Matched-random sessions are incomplete; unavailable is not a pass.",
            "sessions": len(groups),
        }
    for session in expected_sessions:
        candidates = groups.get(session, [])
        expected_count = expected_counts.get(session, 0)
        if expected_count < 1 or expected_count > RANDOM_CANDIDATES_PER_SESSION or len(
            candidates
        ) != expected_count or any(
            row["path_status"] != "valid" for row in candidates
        ):
            return {
                "status": "not_available",
                "detail": (
                    "A sealed matched-random candidate path is missing or invalid; "
                    "unavailable is not a pass."
                ),
                "session": session,
                "expected_candidate_count": expected_count,
                "candidate_count": len(candidates),
                "valid_paths": sum(row["path_status"] == "valid" for row in candidates),
            }

    rng = np.random.default_rng(seed)
    accuracies = np.zeros(RANDOM_REPETITIONS, dtype=float)
    net_rs = np.zeros(RANDOM_REPETITIONS, dtype=float)
    fills = np.zeros(RANDOM_REPETITIONS, dtype=int)
    for repetition in range(RANDOM_REPETITIONS):
        chosen = [
            groups[session][int(rng.integers(0, len(groups[session])))]
            for session in expected_sessions
        ]
        resolved = [row for row in chosen if row["replay"]["status"] == "resolved"]
        fills[repetition] = len(resolved)
        if resolved:
            accuracies[repetition] = float(
                np.mean([bool(row["replay"]["strict_success"]) for row in resolved])
            )
            net_rs[repetition] = float(
                np.mean([float(row["replay"]["net_r"]) for row in resolved])
            )
    observed_accuracy = float(observed.get("strict_accuracy") or 0.0)
    observed_net_r = float(observed.get("mean_net_r") or 0.0)
    return {
        "status": "available",
        "repetitions": RANDOM_REPETITIONS,
        "seed": seed,
        "sessions": len(expected_sessions),
        "mean_fills": float(fills.mean()),
        "mean_accuracy": float(accuracies.mean()),
        "mean_net_r": float(net_rs.mean()),
        "p95_accuracy": float(np.quantile(accuracies, 0.95)),
        "p95_net_r": float(np.quantile(net_rs, 0.95)),
        "accuracy_tail_probability": float(
            (1 + int(np.sum(accuracies >= observed_accuracy)))
            / (RANDOM_REPETITIONS + 1)
        ),
        "net_r_tail_probability": float(
            (1 + int(np.sum(net_rs >= observed_net_r))) / (RANDOM_REPETITIONS + 1)
        ),
    }


def _contract_id(entry_rule: EntryRule, target_r: float) -> str:
    return f"{entry_rule}-target-{target_r:.2f}r"


def _eligible_validation(metrics: Mapping[str, Any]) -> bool:
    return bool(
        int(metrics["valid_paths"]) >= MIN_PATHS
        and int(metrics["filled"]) >= MIN_FILLS
        and int(metrics["filled_sessions"]) >= MIN_FILLS
        and float(metrics.get("no_call_frequency") or 1.0) <= 1.0 / 3.0
        and float(metrics.get("mean_net_r") or 0.0) > 0.0
    )


def _winner_key(
    metrics: Mapping[str, Any], contract_index: int
) -> tuple[float, float, float, int, int]:
    return (
        float(metrics.get("strict_accuracy") or -1.0),
        float(metrics.get("wilson_95_lower") or -1.0),
        float(metrics.get("mean_net_r") or -math.inf),
        int(metrics.get("filled") or 0),
        -contract_index,
    )


def _validate_report(report: Mapping[str, Any]) -> None:
    if report.get("version") != VERSION:
        raise ValueError("unsupported M17 report version")
    if report.get("market") not in DB_PATHS:
        raise ValueError("unsupported M17 report market")
    if report.get("markets_pooled") is not False:
        raise ValueError("M17 markets must never be pooled")
    if report.get("active_model_changed") is not False:
        raise ValueError("M17 cannot change the active model")
    if report.get("eligible_for_live") is not False:
        raise ValueError("M17 cannot authorize live use")
    if report.get("baseline_accuracy_improved") is not False:
        raise ValueError("M17 historical evidence cannot improve the baseline")
    supplied = report.get("artifact_sha256")
    body = dict(report)
    body.pop("artifact_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M17 report hash mismatch")


def _load_source_frame(manifest: Mapping[str, Any]) -> pd.DataFrame:
    path = Path(str(manifest["source"]["path"]))
    if _hash_file(path) != manifest["source"]["sha256"]:
        raise ValueError("M17 causal source binding mismatch")
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            records.append(
                {
                    "session": row["session"],
                    "scrip_code": row["scrip_code"],
                    "symbol": row["symbol"],
                    "label": int(row["opportunity_label"]),
                    **row["features"],
                }
            )
    return pd.DataFrame(records)


def _registration(
    manifest: Mapping[str, Any],
    *,
    winner: Mapping[str, Any],
    output_root: Path,
    registered_at: datetime,
) -> dict[str, Any]:
    market = str(manifest["market"])
    market_root = output_root / market
    frame = _load_source_frame(manifest)
    model = _ranker()
    model.fit(frame[list(FEATURE_COLUMNS)], frame["label"].to_numpy(dtype=int))
    model_path = market_root / "models" / f"{manifest['experiment_id']}-prospective.joblib"
    model_sha = _save_model(
        model_path,
        {
            "version": MODEL_VERSION,
            "purpose": "prospective_observation",
            "market": market,
            "experiment_id": manifest["experiment_id"],
            "protocol_sha256": manifest["protocol_sha256"],
            "source_sha256": manifest["source"]["sha256"],
            "features": FEATURE_COLUMNS,
            "contract": dict(winner),
            "model": model,
        },
    )
    registration: dict[str, Any] = {
        "version": REGISTRATION_VERSION,
        "market": market,
        "experiment_id": manifest["experiment_id"],
        "registered_at": registered_at.isoformat(),
        "starts_strictly_after": manifest["source"]["latest_closed_session"],
        "protocol_sha256": manifest["protocol_sha256"],
        "manifest_sha256": manifest["artifact_sha256"],
        "model_path": str(model_path),
        "model_sha256": model_sha,
        "contract": dict(winner),
        "minimum_calls": PROSPECTIVE_MIN_CALLS,
        "minimum_sessions": PROSPECTIVE_MIN_SESSIONS,
        "authority": "research_observation_only",
        "eligible_for_live": False,
    }
    registration["cohort_id"] = canonical_sha256(registration)
    registration["artifact_sha256"] = canonical_sha256(registration)
    registration_path = market_root / "registration.json"
    if registration_path.exists():
        existing = json.loads(registration_path.read_text(encoding="utf-8"))
        if existing.get("cohort_id") != registration["cohort_id"]:
            raise ValueError("a different M17 cohort is already registered")
        return existing
    _atomic_json(registration_path, registration)
    return registration


def run_intraday_contract_race(
    *,
    market: str,
    db_path: Path | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Open M17 outcomes once path readiness passes and seal the historical result."""

    manifest = load_manifest(market, output_root=output_root)
    market_root = output_root / market
    latest_path = market_root / "latest.json"
    if latest_path.exists():
        existing = json.loads(latest_path.read_text(encoding="utf-8"))
        _validate_report(existing)
        return existing

    source_db = db_path or Path(str(manifest["db_path"]))
    rows = _read_manifest_rows(manifest)
    with CandleStore(source_db) as store:
        paths = [_load_path_record(store, row) for row in rows]

    primary = [path for path in paths if _has_role(path, "ranker_top_1")]
    readiness: dict[str, Any] = {}
    for block in ("validation", "diagnostic"):
        block_paths = [path for path in primary if path["block"] == block]
        readiness[block] = {
            "selections": len(block_paths),
            "valid_paths": sum(path["path_status"] == "valid" for path in block_paths),
            "invalid_paths": sum(
                path["path_status"] != "valid" for path in block_paths
            ),
        }
    if any(readiness[block]["valid_paths"] < MIN_PATHS for block in readiness):
        pending: dict[str, Any] = {
            "version": VERSION,
            "market": market,
            "experiment_id": manifest["experiment_id"],
            "generated_at": (observed_at or datetime.now(IST)).isoformat(),
            "status": "acquisition_incomplete",
            "terminal": False,
            "readiness": readiness,
            "minimum_valid_paths_per_block": MIN_PATHS,
            "authority": "research_only",
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
            "active_model_changed": False,
            "detail": (
                "M17 has not opened the contract race because required primary intraday "
                "paths are incomplete; unavailable is not a pass."
            ),
        }
        pending["artifact_sha256"] = canonical_sha256(pending)
        _atomic_json(market_root / "readiness.json", pending)
        return pending

    path_bundle_path, path_bundle_sha, path_count = _write_gzip_jsonl(
        market_root / "paths", "sealed-paths", paths
    )
    blocks = {
        name: [path for path in paths if path["block"] == name]
        for name in ("validation", "diagnostic")
    }
    validation_cards: dict[str, Any] = {}
    eligible: list[tuple[int, EntryRule, float, dict[str, Any]]] = []
    for index, (entry_rule, target_r) in enumerate(CONTRACTS):
        scored = _score_paths(
            blocks["validation"],
            role="ranker_top_1",
            entry_rule=entry_rule,
            target_r=target_r,
        )
        metrics = _contract_metrics(scored, total_sessions=VALIDATION_SESSIONS)
        contract_id = _contract_id(entry_rule, target_r)
        validation_cards[contract_id] = {
            "entry_rule": entry_rule,
            "target_r": target_r,
            "metrics": metrics,
            "eligible": _eligible_validation(metrics),
        }
        if _eligible_validation(metrics):
            eligible.append((index, entry_rule, target_r, metrics))

    winner: dict[str, Any] | None = None
    if eligible:
        index, entry_rule, target_r, metrics = max(
            eligible, key=lambda item: _winner_key(item[3], item[0])
        )
        winner = {
            "contract_id": _contract_id(entry_rule, target_r),
            "entry_rule": entry_rule,
            "target_r": target_r,
            "validation_metrics": metrics,
            "tie_order_index": index,
        }

    generated_at = observed_at or datetime.now(IST)
    diagnostic: dict[str, Any] | None = None
    gates: dict[str, bool] = {
        "validation_positive_contract_selected": winner is not None
    }
    registration: dict[str, Any] | None = None
    status = "development_rejected"
    if winner is not None:
        entry_rule = winner["entry_rule"]
        target_r = float(winner["target_r"])
        diagnostic_primary = _score_paths(
            blocks["diagnostic"],
            role="ranker_top_1",
            entry_rule=entry_rule,
            target_r=target_r,
        )
        diagnostic_metrics = _contract_metrics(
            diagnostic_primary, total_sessions=DIAGNOSTIC_SESSIONS
        )
        momentum = _score_paths(
            blocks["diagnostic"],
            role="momentum_top_1",
            entry_rule=entry_rule,
            target_r=target_r,
        )
        momentum_metrics = _contract_metrics(momentum, total_sessions=DIAGNOSTIC_SESSIONS)
        anchor = _score_paths(
            blocks["diagnostic"],
            role="ranker_top_1",
            entry_rule="next_open",
            target_r=0.75,
        )
        anchor_metrics = _contract_metrics(anchor, total_sessions=DIAGNOSTIC_SESSIONS)
        random_control = _matched_random_control(
            blocks["diagnostic"],
            entry_rule=entry_rule,
            target_r=target_r,
            observed=diagnostic_metrics,
            seed=RANDOM_REPLAY_SEED,
        )
        random_available = random_control.get("status") == "available"
        random_accuracy = float(random_control.get("mean_accuracy") or 0.0)
        random_net_r = float(random_control.get("mean_net_r") or 0.0)
        validation_accuracy = float(
            winner["validation_metrics"].get("strict_accuracy") or 0.0
        )
        diagnostic_accuracy = float(diagnostic_metrics.get("strict_accuracy") or 0.0)
        gates.update(
            {
                "diagnostic_at_least_32_valid_paths": diagnostic_metrics["valid_paths"]
                >= MIN_PATHS,
                "diagnostic_at_least_24_fills": diagnostic_metrics["filled"] >= MIN_FILLS,
                "diagnostic_at_least_24_filled_sessions": diagnostic_metrics[
                    "filled_sessions"
                ]
                >= MIN_FILLS,
                "diagnostic_accuracy_at_least_55pct": diagnostic_accuracy >= 0.55,
                "diagnostic_wilson_lower_at_least_35pct": float(
                    diagnostic_metrics.get("wilson_95_lower") or 0.0
                )
                >= 0.35,
                "diagnostic_mean_net_r_positive": float(
                    diagnostic_metrics.get("mean_net_r") or 0.0
                )
                > 0.0,
                "diagnostic_median_net_r_positive": float(
                    diagnostic_metrics.get("median_net_r") or 0.0
                )
                > 0.0,
                "diagnostic_max_losing_streak_at_most_6": diagnostic_metrics[
                    "maximum_losing_streak"
                ]
                <= 6,
                "accuracy_advantage_over_random_at_least_10pp": random_available
                and diagnostic_accuracy - random_accuracy >= 0.10,
                "net_r_advantage_over_random_at_least_010r": random_available
                and float(diagnostic_metrics.get("mean_net_r") or 0.0) - random_net_r
                >= 0.10,
                "random_net_r_tail_at_most_010": random_available
                and float(random_control.get("net_r_tail_probability") or 1.0) <= 0.10,
                "diagnostic_no_call_frequency_at_most_one_third": float(
                    diagnostic_metrics.get("no_call_frequency") or 1.0
                )
                <= 1.0 / 3.0,
                "validation_diagnostic_accuracy_gap_at_most_20pp": abs(
                    validation_accuracy - diagnostic_accuracy
                )
                <= 0.20,
            }
        )
        diagnostic = {
            "metrics": diagnostic_metrics,
            "momentum_control": momentum_metrics,
            "m15_style_anchor": anchor_metrics,
            "matched_random": random_control,
        }
        if all(gates.values()):
            registration = _registration(
                manifest,
                winner={
                    "contract_id": winner["contract_id"],
                    "entry_rule": entry_rule,
                    "target_r": target_r,
                },
                output_root=output_root,
                registered_at=generated_at,
            )
            status = "prospective_registered"

    report: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": manifest["experiment_id"],
        "generated_at": generated_at.isoformat(),
        "status": status,
        "terminal": True,
        "evidence_class": "consumed_historical_development",
        "manifest": {
            "path": str(market_root / "manifest.json"),
            "sha256": manifest["artifact_sha256"],
        },
        "path_bundle": {
            "path": str(path_bundle_path),
            "sha256": path_bundle_sha,
            "rows": path_count,
            "readiness": readiness,
        },
        "validation_contracts": validation_cards,
        "selected_contract": winner,
        "diagnostic": diagnostic,
        "development_gates": gates,
        "all_development_registration_gates_passed": bool(gates) and all(gates.values()),
        "registration": registration,
        "prospective": {
            "status": "registered" if registration else "not_registered",
            "resolved_calls": 0,
            "minimum_calls": PROSPECTIVE_MIN_CALLS,
            "minimum_sessions": PROSPECTIVE_MIN_SESSIONS,
            "qualification_passed": False,
        },
        "active_model_changed": False,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "research_only",
        "detail": (
            "The historical M17 gate passed and a fresh research-only observer was "
            "registered; baseline improvement still requires prospective evidence."
            if registration
            else "No frozen M17 contract cleared every historical accuracy, availability, "
            "economic and random-control gate; no observer or live authority was created."
        ),
    }
    report["artifact_sha256"] = canonical_sha256(report)
    _validate_report(report)
    experiment_path = market_root / "experiments" / f"{manifest['experiment_id']}.json"
    _atomic_json(experiment_path, report)
    _atomic_json(latest_path, report)
    (market_root / "readiness.json").unlink(missing_ok=True)
    _append_registry(
        market_root / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": manifest["experiment_id"],
            "event": "historical_result_sealed",
            "at": generated_at.isoformat(),
            "status": status,
            "artifact_sha256": report["artifact_sha256"],
        },
    )
    return report


def load_intraday_contract_status(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    """Return compact, fail-closed M17 acquisition/development/forward evidence."""

    if market not in DB_PATHS:
        raise ValueError(f"unsupported M17 market: {market}")
    market_root = output_root / market
    base: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "status": "not_started",
        "manifest": {"status": "not_sealed"},
        "acquisition": {"status": "not_started"},
        "development": {
            "status": "not_run",
            "selected_contract": None,
            "gates_passed": 0,
            "gates_total": 0,
        },
        "prospective": {
            "status": "not_registered",
            "resolved_calls": 0,
            "minimum_calls": PROSPECTIVE_MIN_CALLS,
            "minimum_sessions": PROSPECTIVE_MIN_SESSIONS,
            "qualification_passed": False,
        },
        "baseline_accuracy_improved": False,
        "active_model_changed": False,
        "eligible_for_live": False,
        "authority": "research_only",
        "detail": "No M17 manifest exists — unavailable is not a pass.",
    }
    if not (market_root / "manifest.json").exists():
        return base
    try:
        manifest = load_manifest(market, output_root=output_root)
        base["manifest"] = {
            "status": "sealed",
            "experiment_id": manifest["experiment_id"],
            "rows": manifest["acquisition_rows"]["rows"],
            "sealed_windows": manifest["acquisition_rows"]["sealed_windows"],
            "unavailable_windows": manifest["acquisition_rows"]["unavailable_windows"],
            "sha256": manifest["artifact_sha256"],
        }
        base["status"] = "manifest_sealed"
        base["detail"] = (
            "M17 candidates and windows are sealed; path acquisition or evaluation is pending."
        )

        acquisition_path = market_root / "acquisition.json"
        if acquisition_path.exists():
            acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
            body = dict(acquisition)
            supplied = body.pop("artifact_sha256", None)
            if supplied != canonical_sha256(body):
                raise ValueError("M17 acquisition summary hash mismatch")
            ledger_path = Path(str(acquisition["ledger_path"]))
            if _hash_file(ledger_path) != acquisition["ledger_sha256"]:
                raise ValueError("M17 acquisition ledger binding mismatch")
            base["acquisition"] = {
                "status": acquisition["status"],
                "request_records": acquisition["request_records"],
                "completed_records": acquisition["completed_records"],
                "error_records": acquisition["error_records"],
                "returned_bars": acquisition["returned_bars"],
            }

        readiness_path = market_root / "readiness.json"
        if readiness_path.exists():
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
            body = dict(readiness)
            supplied = body.pop("artifact_sha256", None)
            if supplied != canonical_sha256(body):
                raise ValueError("M17 readiness hash mismatch")
            base["status"] = readiness["status"]
            base["development"] = {
                "status": "waiting_for_paths",
                "selected_contract": None,
                "readiness": readiness["readiness"],
                "minimum_valid_paths_per_block": readiness[
                    "minimum_valid_paths_per_block"
                ],
                "gates_passed": 0,
                "gates_total": 0,
            }
            base["detail"] = readiness["detail"]

        latest_path = market_root / "latest.json"
        if latest_path.exists():
            report = json.loads(latest_path.read_text(encoding="utf-8"))
            _validate_report(report)
            if report["manifest"]["sha256"] != manifest["artifact_sha256"]:
                raise ValueError("M17 result manifest binding mismatch")
            path_bundle = Path(str(report["path_bundle"]["path"]))
            if _hash_file(path_bundle) != report["path_bundle"]["sha256"]:
                raise ValueError("M17 result path-bundle binding mismatch")
            gates = report["development_gates"]
            selected = report.get("selected_contract")
            diagnostic_metrics = (
                (report.get("diagnostic") or {}).get("metrics") or {}
            )
            base.update(
                {
                    "status": report["status"],
                    "development": {
                        "status": report["status"],
                        "selected_contract": (
                            {
                                "contract_id": selected["contract_id"],
                                "entry_rule": selected["entry_rule"],
                                "target_r": selected["target_r"],
                            }
                            if selected
                            else None
                        ),
                        "diagnostic_metrics": diagnostic_metrics,
                        "readiness": report["path_bundle"]["readiness"],
                        "gates_passed": sum(bool(value) for value in gates.values()),
                        "gates_total": len(gates),
                        "all_gates_passed": report[
                            "all_development_registration_gates_passed"
                        ],
                    },
                    "prospective": report["prospective"],
                    "detail": report["detail"],
                }
            )
        return base
    except Exception as exc:
        return {
            **base,
            "status": "invalid_or_unreadable",
            "detail": f"M17 evidence is invalid — not a pass: {type(exc).__name__}: {exc}",
        }
