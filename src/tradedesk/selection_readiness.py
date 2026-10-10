"""M18-A market-specific selection-dataset readiness.

This module deliberately stops before model fitting.  It replays one preregistered M17
execution anchor against the exact sealed M17 candidates, joins only causal decision-time
features and reports whether each chronological block has enough resolved positive and
negative examples for a later selector experiment.

Historical readiness is research evidence only.  It cannot change an active model,
improve the production baseline or authorize a live call.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.intraday_contract_race import (
    DEFAULT_OUTPUT_ROOT as M17_OUTPUT_ROOT,
)
from tradedesk.intraday_contract_race import (
    PATH_BUNDLE_VERSION,
    RESEARCH_CAPITAL,
    RISK_BUDGET,
    _append_registry,
    _atomic_json,
    _frame_from_payload,
    _hash_file,
    _interval_disagreement_audit,
    _load_source_frame,
    _market_bundle,
    _protocol_amendment_bindings,
    _read_manifest_rows,
    _same_timestamp_grid,
    _validate_interval_frame,
    _write_gzip_jsonl,
)
from tradedesk.intraday_contract_race import (
    load_manifest as load_m17_manifest,
)
from tradedesk.leader_discovery import FEATURE_COLUMNS
from tradedesk.models import TradeType, price_decimal
from tradedesk.prediction_ledger import canonical_sha256

VERSION = "selection-readiness-m18a-v1"
MANIFEST_VERSION = "selection-readiness-manifest-v1"
DATASET_VERSION = "selection-readiness-dataset-v1"
PROTOCOL_PATH = Path("docs/self-learning-m18-selection-readiness-protocol.md")
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/selection_readiness")

MarketName = Literal["nse", "bse", "crypto"]
MARKETS: tuple[MarketName, ...] = ("nse", "bse", "crypto")
SPLIT_ORDER = (
    "development_train",
    "purge_1",
    "calibration",
    "purge_2",
    "internal_diagnostic",
)
SPLIT_SIZES = (42, 3, 12, 3, 12)
NON_PURGE_SPLITS = (
    "development_train",
    "calibration",
    "internal_diagnostic",
)
FIXED_CONTRACT = {"entry_rule": "next_open", "target_r": 0.75}
MINIMUM_COVERAGE = 0.85
READINESS_REQUIREMENTS: dict[str, dict[str, int]] = {
    "development_train": {
        "minimum_resolved_sessions": 36,
        "minimum_resolved_rows": 300,
        "minimum_positive_labels": 40,
        "minimum_negative_labels": 40,
    },
    "calibration": {
        "minimum_resolved_sessions": 10,
        "minimum_resolved_rows": 80,
        "minimum_positive_labels": 12,
        "minimum_negative_labels": 12,
    },
    "internal_diagnostic": {
        "minimum_resolved_sessions": 10,
        "minimum_resolved_rows": 80,
        "minimum_positive_labels": 12,
        "minimum_negative_labels": 12,
    },
}


def _protocol_sha256() -> str:
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(f"frozen M18-A protocol missing: {PROTOCOL_PATH}")
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def _partition_sessions(sessions: Sequence[str]) -> dict[str, list[str]]:
    ordered = sorted(set(sessions))
    if len(ordered) != sum(SPLIT_SIZES):
        raise ValueError(f"M18-A requires exactly {sum(SPLIT_SIZES)} sessions, got {len(ordered)}")
    output: dict[str, list[str]] = {}
    start = 0
    for name, size in zip(SPLIT_ORDER, SPLIT_SIZES, strict=True):
        output[name] = ordered[start : start + size]
        start += size
    return output


def _session_split(split: Mapping[str, Sequence[str]]) -> dict[str, str]:
    return {session: block for block in SPLIT_ORDER for session in split.get(block, ())}


def _deduplicate_m17_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        key = (str(source["session"]), str(source["scrip_code"]))
        value = dict(source)
        value["roles"] = sorted(set(str(role) for role in source.get("roles", ())))
        if key not in deduplicated:
            deduplicated[key] = value
            continue
        existing = deduplicated[key]
        comparable = {name: item for name, item in value.items() if name != "roles"}
        existing_comparable = {name: item for name, item in existing.items() if name != "roles"}
        if comparable != existing_comparable:
            raise ValueError(f"conflicting M17 duplicate candidate: {key}")
        existing["roles"] = sorted(set(existing["roles"]) | set(value["roles"]))
    return [deduplicated[key] for key in sorted(deduplicated)]


def _manifest_path(output_root: Path, market: str) -> Path:
    return output_root / market / "manifest.json"


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError("unsupported M18-A manifest version")
    if manifest.get("market") not in MARKETS:
        raise ValueError("unsupported M18-A market")
    if manifest.get("markets_pooled") is not False:
        raise ValueError("M18-A markets must never be pooled")
    if manifest.get("protocol_sha256") != _protocol_sha256():
        raise ValueError("M18-A protocol binding mismatch")
    if tuple(manifest.get("features") or ()) != tuple(FEATURE_COLUMNS):
        raise ValueError("M18-A feature contract mismatch")
    if manifest.get("fixed_contract") != FIXED_CONTRACT:
        raise ValueError("M18-A fixed execution contract mismatch")
    split = manifest.get("split") or {}
    if set(split) != set(SPLIT_ORDER):
        raise ValueError("M18-A chronological split names mismatch")
    for name, size in zip(SPLIT_ORDER, SPLIT_SIZES, strict=True):
        if len(split.get(name, ())) != size:
            raise ValueError(f"M18-A {name} split size mismatch")
    flat = [session for name in SPLIT_ORDER for session in split[name]]
    if flat != sorted(set(flat)):
        raise ValueError("M18-A sessions are not unique chronological partitions")
    if manifest.get("m17_protocol_amendments") != _protocol_amendment_bindings():
        raise ValueError("M18-A M17 path amendment binding mismatch")
    if manifest.get("authority") != "research_only":
        raise ValueError("M18-A authority mismatch")
    if manifest.get("eligible_for_live") is not False:
        raise ValueError("M18-A cannot authorize live use")
    if manifest.get("active_model_changed") is not False:
        raise ValueError("M18-A cannot change the active model")
    if manifest.get("baseline_accuracy_improved") is not False:
        raise ValueError("M18-A cannot improve the baseline")
    supplied = manifest.get("artifact_sha256")
    body = dict(manifest)
    body.pop("artifact_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M18-A manifest hash mismatch")


def seal_selection_readiness_manifest(
    market: str,
    *,
    m17_root: Path = M17_OUTPUT_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    sealed_at: datetime | None = None,
) -> dict[str, Any]:
    """Bind M18-A to M17 inputs without opening any path or outcome."""

    if market not in MARKETS:
        raise ValueError(f"unsupported M18-A market: {market}")
    latest_path = _manifest_path(output_root, market)
    if latest_path.exists():
        existing = json.loads(latest_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("M18-A manifest is not an object")
        _validate_manifest(existing)
        return existing

    m17 = load_m17_manifest(market, output_root=m17_root)
    rows = _deduplicate_m17_rows(_read_manifest_rows(m17))
    split = _partition_sessions([str(row["session"]) for row in rows])
    observed_at = sealed_at or datetime.now(IST)
    input_binding = {
        "m17_experiment_id": m17["experiment_id"],
        "m17_manifest_sha256": m17["artifact_sha256"],
        "m17_protocol_sha256": m17["protocol_sha256"],
        "m17_acquisition_rows_path": m17["acquisition_rows"]["path"],
        "m17_acquisition_rows_sha256": m17["acquisition_rows"]["sha256"],
        "m17_acquisition_rows": len(rows),
        "causal_source_path": m17["source"]["path"],
        "causal_source_sha256": m17["source"]["sha256"],
        "db_path": m17["db_path"],
    }
    experiment_id = canonical_sha256(
        {
            "version": VERSION,
            "market": market,
            "protocol_sha256": _protocol_sha256(),
            "input_binding": input_binding,
            "split": split,
            "fixed_contract": FIXED_CONTRACT,
            "features": FEATURE_COLUMNS,
        }
    )
    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": experiment_id,
        "sealed_at": observed_at.isoformat(),
        "protocol_path": str(PROTOCOL_PATH),
        "protocol_sha256": _protocol_sha256(),
        "input_binding": input_binding,
        "m17_protocol_amendments": _protocol_amendment_bindings(),
        "split": split,
        "features": list(FEATURE_COLUMNS),
        "fixed_contract": dict(FIXED_CONTRACT),
        "minimum_coverage": MINIMUM_COVERAGE,
        "readiness_requirements": READINESS_REQUIREMENTS,
        "consumed_historical_evidence": True,
        "authority": "research_only",
        "eligible_for_live": False,
        "active_model_changed": False,
        "baseline_accuracy_improved": False,
    }
    manifest["artifact_sha256"] = canonical_sha256(manifest)
    _validate_manifest(manifest)
    _atomic_json(latest_path, manifest)
    _append_registry(
        output_root / market / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": experiment_id,
            "event": "manifest_sealed",
            "at": observed_at.isoformat(),
            "artifact_sha256": manifest["artifact_sha256"],
            "outcomes_opened": False,
        },
    )
    return manifest


def load_selection_readiness_manifest(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    path = _manifest_path(output_root, market)
    if not path.exists():
        raise FileNotFoundError(f"M18-A manifest missing for {market}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M18-A manifest is not an object")
    _validate_manifest(value)
    return value


def _finite_features(source: Mapping[str, Any] | None) -> tuple[dict[str, float | None], str]:
    features: dict[str, float | None] = {}
    missing: list[str] = []
    for name in FEATURE_COLUMNS:
        value = None if source is None else source.get(name)
        try:
            number = math.nan if value is None else float(value)
        except (TypeError, ValueError):
            number = math.nan
        if not math.isfinite(number):
            features[name] = None
            missing.append(name)
        else:
            features[name] = number
    return features, "valid" if not missing else "invalid:" + ",".join(missing)


class _PreloadedPathStore:
    """CandleStore-compatible read view loaded with one integrity-preserving SQL join."""

    def __init__(self, frames: Mapping[tuple[str, str], pd.DataFrame]) -> None:
        self.frames = frames

    def load(
        self,
        scrip_code: str,
        interval: Interval,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        del adjusted
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty.index = pd.DatetimeIndex([], tz=IST, name="ts")
        frame = self.frames.get((scrip_code, interval.value), empty)
        selected = frame
        if start is not None:
            selected = selected[selected.index >= pd.Timestamp(start)]
        if end is not None:
            selected = selected[selected.index < pd.Timestamp(end)]
        return selected.copy()


def _preload_path_store(
    store: CandleStore, candidates: Sequence[Mapping[str, Any]]
) -> _PreloadedPathStore:
    """Load only the sealed candidate windows, avoiding thousands of identical queries."""

    windows = pd.DataFrame(
        [
            {
                "scrip_code": str(row["scrip_code"]),
                "start_ts": int(datetime.fromisoformat(str(row["window_start"])).timestamp()),
                "end_ts": int(datetime.fromisoformat(str(row["window_end"])).timestamp()),
            }
            for row in candidates
            if row.get("window_status") == "sealed"
        ]
    ).drop_duplicates()
    if windows.empty:
        return _PreloadedPathStore({})
    store.con.register("_m18_windows", windows)
    try:
        candles = store.con.execute(
            """
            SELECT DISTINCT
                c.scrip_code, c.interval, c.ts,
                c.open, c.high, c.low, c.close, c.volume
            FROM candles c
            JOIN _m18_windows w
              ON c.scrip_code = w.scrip_code
             AND c.ts >= w.start_ts
             AND c.ts < w.end_ts
            WHERE c.interval IN (?, ?, ?)
            ORDER BY c.scrip_code, c.interval, c.ts
            """,
            [Interval.M1.value, Interval.M5.value, Interval.M15.value],
        ).df()
    finally:
        store.con.unregister("_m18_windows")

    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for (code, interval), group in candles.groupby(["scrip_code", "interval"], sort=False):
        frame = group[["ts", "open", "high", "low", "close", "volume"]].copy()
        index = pd.to_datetime(frame.pop("ts"), unit="s", utc=True).dt.tz_convert(IST)
        frame.index = pd.DatetimeIndex(index, name="ts")
        frame["volume"] = frame["volume"].astype("int64")
        frames[(str(code), str(interval))] = frame
    return _PreloadedPathStore(frames)


def _frame_payload_fast(frame: pd.DataFrame) -> list[list[Any]]:
    """Produce the exact M17 path payload without pandas' per-row Series overhead."""

    output: list[list[Any]] = []
    for timestamp, row in zip(frame.index, frame.itertuples(index=False), strict=True):
        value: Any = row
        output.append(
            [
                timestamp.isoformat(),
                float(value.open),
                float(value.high),
                float(value.low),
                float(value.close),
                int(value.volume),
            ]
        )
    return output


def _aggregate_frame_fast(frame: pd.DataFrame, minutes: int, *, start: datetime) -> pd.DataFrame:
    """Vectorized equivalent of M17's positional, window-anchored aggregation."""

    groups = len(frame) // minutes
    if groups == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    values = frame.iloc[: groups * minutes]
    opens = values["open"].to_numpy(dtype=float).reshape(groups, minutes)[:, 0]
    highs = values["high"].to_numpy(dtype=float).reshape(groups, minutes).max(axis=1)
    lows = values["low"].to_numpy(dtype=float).reshape(groups, minutes).min(axis=1)
    closes = values["close"].to_numpy(dtype=float).reshape(groups, minutes)[:, -1]
    volumes = values["volume"].to_numpy(dtype="int64").reshape(groups, minutes).sum(axis=1)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.DatetimeIndex(
            [start + pd.Timedelta(minutes=index * minutes) for index in range(groups)],
            name="ts",
        ),
    )


def _validate_intraday_path_fast(
    m1: pd.DataFrame,
    m5: pd.DataFrame,
    m15: pd.DataFrame,
    *,
    start: datetime,
    end: datetime,
) -> tuple[bool, str]:
    for minutes, frame in {1: m1, 5: m5, 15: m15}.items():
        valid, detail = _validate_interval_frame(frame, minutes=minutes, start=start, end=end)
        if not valid:
            return valid, detail
    for minutes, observed in ((5, m5), (15, m15)):
        aggregated = _aggregate_frame_fast(m1, minutes, start=start)
        if not _same_timestamp_grid(
            pd.DatetimeIndex(aggregated.index), pd.DatetimeIndex(observed.index)
        ):
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
            aggregated["volume"].to_numpy(dtype="int64"),
            observed["volume"].to_numpy(dtype="int64"),
        ):
            return False, f"m{minutes}_volume_aggregate_mismatch"
    return True, "complete_and_consistent"


def _load_path_record_fast(store: _PreloadedPathStore, row: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize the identical M17 path record using the preloaded read view."""

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
    m1 = store.load(str(row["scrip_code"]), Interval.M1, start=start, end=end)
    m5 = store.load(str(row["scrip_code"]), Interval.M5, start=start, end=end)
    observed_m15 = store.load(str(row["scrip_code"]), Interval.M15, start=start, end=end)
    m15 = observed_m15
    m15_source = "observed_api"
    observed_m15_audit: dict[str, Any] | None = None
    if row["market"] == "crypto":
        observed_valid, observed_detail = _validate_interval_frame(
            observed_m15, minutes=15, start=start, end=end
        )
        m15 = _aggregate_frame_fast(m1, 15, start=start)
        m15_source = "derived_from_observed_m1"
        observed_m15_audit = {
            "source": "observed_api_auxiliary_only",
            "frame_status": observed_detail,
            **(
                _interval_disagreement_audit(m15, observed_m15)
                if observed_valid and len(m15) == len(observed_m15)
                else {
                    "status": "invalid_or_unavailable",
                    "bars": len(observed_m15),
                    "mismatch_bars_by_field": None,
                }
            ),
        }
    valid, detail = _validate_intraday_path_fast(m1, m5, m15, start=start, end=end)
    if row["market"] == "crypto" and observed_m15_audit is not None:
        if observed_m15_audit["status"] == "invalid_or_unavailable":
            valid = False
            detail = f"observed_{observed_m15_audit['frame_status']}"
    record = {
        **base,
        "path_status": "valid" if valid else "invalid_or_unavailable",
        "path_detail": detail,
        "m15_source": m15_source,
        "observed_m15_audit": observed_m15_audit,
        "m1": _frame_payload_fast(m1),
        "m5": _frame_payload_fast(m5),
        "m15": _frame_payload_fast(m15),
    }
    record["path_sha256"] = canonical_sha256(record)
    return record


def _replay_fixed_contract(path: Mapping[str, Any], market_bundle: Any) -> dict[str, Any]:
    """Replay M17's frozen next-open/+0.75R anchor with one cached cost bundle."""

    if path.get("path_status") != "valid":
        return {
            "status": "invalid_or_unavailable",
            "event": path.get("path_detail", "invalid_path"),
            "strict_success": None,
            "net_r": None,
        }
    market = str(path["market"])
    m1 = _frame_from_payload(path["m1"])
    if m1.empty:
        return {
            "status": "invalid_or_unavailable",
            "event": "invalid_empty_path",
            "strict_success": None,
            "net_r": None,
        }

    entry_index = 0
    raw_entry = float(m1.iloc[0]["open"])
    slippage = float(market_bundle.costs.slippage_pct)
    entry = raw_entry * (1.0 + slippage)
    atr = float(path["decision_close"]) * float(path["atr_14_pct"])
    floor = 0.04 if market == "crypto" else 0.01
    risk_distance = max(0.50 * atr, floor * entry)
    stop = entry - risk_distance
    target = entry + 0.75 * risk_distance
    if not all(math.isfinite(value) for value in (entry, stop, target)) or stop <= 0:
        return {
            "status": "invalid_geometry",
            "event": "invalid_geometry",
            "strict_success": None,
            "net_r": None,
        }

    raw_qty = min(RISK_BUDGET / risk_distance, RESEARCH_CAPITAL / entry)
    if market == "crypto":
        step = float(market_bundle.qty_step)
        quantity = math.floor(raw_qty / step) * step
    else:
        quantity = float(math.floor(raw_qty))
    if quantity <= 0 or quantity * entry < float(market_bundle.min_notional_inr):
        return {
            "status": "not_sizeable",
            "event": "not_sizeable",
            "strict_success": None,
            "net_r": None,
        }

    event = "timeout"
    exit_raw = float(m1.iloc[-1]["close"])
    exit_index = len(m1) - 1
    opens = m1["open"].to_numpy(dtype=float)
    highs = m1["high"].to_numpy(dtype=float)
    lows = m1["low"].to_numpy(dtype=float)
    for index in range(entry_index, len(m1)):
        open_price = float(opens[index])
        high = float(highs[index])
        low = float(lows[index])
        if index > entry_index and open_price <= stop:
            event, exit_raw, exit_index = "gap_stop", open_price, index
            break
        if index > entry_index and open_price >= target:
            event, exit_raw, exit_index = "gap_target", open_price, index
            break
        if low <= stop:
            event, exit_raw, exit_index = "stop", stop, index
            break
        if high >= target:
            event, exit_raw, exit_index = "target", target, index
            break

    exit_execution = exit_raw * (1.0 - slippage)
    net_r = float(
        market_bundle.costs.net_r_multiple(
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
        "entry_event": "next_open",
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


def _block_metrics(rows: Sequence[Mapping[str, Any]], block: str) -> dict[str, Any]:
    selected = [row for row in rows if row["split"] == block]
    resolved = [row for row in selected if row["replay_status"] == "resolved"]
    eligible = [row for row in selected if row["model_eligible"] is True]
    positive = sum(row["precision_label"] is True for row in eligible)
    negative = sum(row["precision_label"] is False for row in eligible)
    total = len(selected)
    return {
        "sessions": len({str(row["session"]) for row in selected}),
        "resolved_sessions": len({str(row["session"]) for row in eligible}),
        "candidate_rows": total,
        "valid_resolved_paths": len(resolved),
        "valid_resolved_coverage": len(resolved) / total if total else None,
        "model_eligible_rows": len(eligible),
        "positive_labels": positive,
        "negative_labels": negative,
        "feature_invalid_rows": sum(row["feature_status"] != "valid" for row in selected),
        "path_invalid_or_unresolved_rows": total - len(resolved),
    }


def _readiness_gates(blocks: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for block in NON_PURGE_SPLITS:
        metrics = blocks[block]
        requirements = READINESS_REQUIREMENTS[block]
        values: tuple[tuple[str, Any, Any], ...] = (
            ("coverage", metrics["valid_resolved_coverage"], MINIMUM_COVERAGE),
            (
                "resolved_sessions",
                metrics["resolved_sessions"],
                requirements["minimum_resolved_sessions"],
            ),
            (
                "resolved_rows",
                metrics["model_eligible_rows"],
                requirements["minimum_resolved_rows"],
            ),
            (
                "positive_labels",
                metrics["positive_labels"],
                requirements["minimum_positive_labels"],
            ),
            (
                "negative_labels",
                metrics["negative_labels"],
                requirements["minimum_negative_labels"],
            ),
            ("finite_features", metrics["feature_invalid_rows"], 0),
        )
        for name, observed, required in values:
            passed = observed is not None and (
                observed == 0 if name == "finite_features" else observed >= required
            )
            gates.append(
                {
                    "block": block,
                    "gate": name,
                    "observed": observed,
                    "required": required,
                    "passed": passed,
                }
            )
    return gates


def _validate_summary(summary: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    if summary.get("version") != VERSION:
        raise ValueError("unsupported M18-A result version")
    if summary.get("market") != manifest.get("market"):
        raise ValueError("M18-A result market mismatch")
    if summary.get("experiment_id") != manifest.get("experiment_id"):
        raise ValueError("M18-A result experiment mismatch")
    if summary.get("manifest_sha256") != manifest.get("artifact_sha256"):
        raise ValueError("M18-A result manifest binding mismatch")
    if summary.get("protocol_sha256") != _protocol_sha256():
        raise ValueError("M18-A result protocol binding mismatch")
    if summary.get("authority") != "research_only":
        raise ValueError("M18-A result authority mismatch")
    for field in ("eligible_for_live", "active_model_changed", "baseline_accuracy_improved"):
        if summary.get(field) is not False:
            raise ValueError(f"M18-A result cannot set {field}")
    supplied = summary.get("artifact_sha256")
    body = dict(summary)
    body.pop("artifact_sha256", None)
    if supplied != canonical_sha256(body):
        raise ValueError("M18-A result hash mismatch")


def build_selection_readiness(
    market: str,
    *,
    m17_root: Path = M17_OUTPUT_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the sealed row-level dataset and evaluate readiness gates only."""

    manifest = load_selection_readiness_manifest(market, output_root=output_root)
    m17 = load_m17_manifest(market, output_root=m17_root)
    binding = manifest["input_binding"]
    if m17["artifact_sha256"] != binding["m17_manifest_sha256"]:
        raise ValueError("M18-A bound M17 manifest changed")
    if (
        _hash_file(Path(binding["m17_acquisition_rows_path"]))
        != binding["m17_acquisition_rows_sha256"]
    ):
        raise ValueError("M18-A bound M17 acquisition rows changed")
    if _hash_file(Path(binding["causal_source_path"])) != binding["causal_source_sha256"]:
        raise ValueError("M18-A bound causal source changed")

    candidates = _deduplicate_m17_rows(_read_manifest_rows(m17))
    source_frame = _load_source_frame(m17)
    source_lookup: dict[tuple[str, str], dict[str, Any]] = {
        (str(row["session"]), str(row["scrip_code"])): {
            str(name): value for name, value in row.items()
        }
        for row in source_frame.to_dict(orient="records")
    }
    split_by_session = _session_split(manifest["split"])
    records: list[dict[str, Any]] = []
    market_bundle = _market_bundle(market)
    with CandleStore(Path(binding["db_path"])) as store:
        path_store = _preload_path_store(store, candidates)
        load_path = partial(_load_path_record_fast, path_store)
        with ThreadPoolExecutor(max_workers=8) as executor:
            paths = list(executor.map(load_path, candidates))
        for candidate, path in zip(candidates, paths, strict=True):
            session = str(candidate["session"])
            code = str(candidate["scrip_code"])
            source = source_lookup.get((session, code))
            features, feature_status = _finite_features(source)
            replay = _replay_fixed_contract(path, market_bundle)
            resolved = replay.get("status") == "resolved"
            split_name = split_by_session[session]
            model_eligible = (
                split_name not in {"purge_1", "purge_2"} and resolved and feature_status == "valid"
            )
            strict_success = bool(replay["strict_success"]) if resolved else None
            net_r = float(replay["net_r"]) if resolved else None
            positive_after_cost = net_r > 0.0 if net_r is not None else None
            precision_label = (
                strict_success and positive_after_cost
                if strict_success is not None and positive_after_cost is not None
                else None
            )
            if split_name in {"purge_1", "purge_2"}:
                exclusion_reason = "purge_block"
            elif feature_status != "valid":
                exclusion_reason = feature_status
            elif not resolved:
                exclusion_reason = f"replay:{replay.get('status', 'unavailable')}"
            else:
                exclusion_reason = None
            records.append(
                {
                    "version": DATASET_VERSION,
                    "market": market,
                    "experiment_id": manifest["experiment_id"],
                    "session": session,
                    "scrip_code": code,
                    "symbol": str(candidate["symbol"]),
                    "roles": list(candidate.get("roles", ())),
                    "m17_block": str(candidate["block"]),
                    "split": split_name,
                    "decision_close": float(candidate["decision_close"]),
                    "window_start": candidate.get("window_start"),
                    "window_end": candidate.get("window_end"),
                    "feature_status": feature_status,
                    "features": features,
                    "path_status": path.get("path_status"),
                    "path_detail": path.get("path_detail"),
                    "path_sha256": path.get("path_sha256") or canonical_sha256(path),
                    "replay_status": replay.get("status"),
                    "replay_event": replay.get("event"),
                    "strict_success": strict_success,
                    "net_r": net_r,
                    "positive_after_cost": positive_after_cost,
                    "precision_label": precision_label,
                    "model_eligible": model_eligible,
                    "exclusion_reason": exclusion_reason,
                }
            )

    market_root = output_root / market
    dataset_path, dataset_sha, dataset_rows = _write_gzip_jsonl(
        market_root / "datasets", "selection-readiness", records
    )
    blocks = {name: _block_metrics(records, name) for name in SPLIT_ORDER}
    gates = _readiness_gates(blocks)
    passed = sum(gate["passed"] is True for gate in gates)
    status = "ready_for_development" if passed == len(gates) else "not_ready"
    blockers = [
        f"{gate['block']}:{gate['gate']}={gate['observed']} (requires {gate['required']})"
        for gate in gates
        if gate["passed"] is not True
    ]
    observed_at = generated_at or datetime.now(IST)
    summary: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "markets_pooled": False,
        "experiment_id": manifest["experiment_id"],
        "generated_at": observed_at.isoformat(),
        "status": status,
        "protocol_sha256": manifest["protocol_sha256"],
        "manifest_sha256": manifest["artifact_sha256"],
        "fixed_contract": dict(FIXED_CONTRACT),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset_sha,
            "rows": dataset_rows,
        },
        "blocks": blocks,
        "gates": gates,
        "gates_passed": passed,
        "gates_total": len(gates),
        "blockers": blockers,
        "consumed_historical_evidence": True,
        "prospective_observer_registered": False,
        "authority": "research_only",
        "eligible_for_live": False,
        "active_model_changed": False,
        "baseline_accuracy_improved": False,
        "detail": (
            "M18-A dataset is ready for a separately preregistered M18-B development run; "
            "this historical readiness is not performance proof."
            if status == "ready_for_development"
            else (
                "M18-A readiness gates did not all pass; unavailable or invalid "
                "evidence is never a pass."
            )
        ),
    }
    summary["artifact_sha256"] = canonical_sha256(summary)
    _validate_summary(summary, manifest)
    _atomic_json(market_root / "latest.json", summary)
    _append_registry(
        market_root / "registry.jsonl",
        {
            "version": VERSION,
            "market": market,
            "experiment_id": manifest["experiment_id"],
            "event": "dataset_built",
            "at": observed_at.isoformat(),
            "status": status,
            "gates_passed": passed,
            "gates_total": len(gates),
            "artifact_sha256": summary["artifact_sha256"],
        },
    )
    return summary


def load_selection_readiness_status(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    """Return compact fail-closed M18-A evidence for the dashboard."""

    if market not in MARKETS:
        raise ValueError(f"unsupported M18-A market: {market}")
    base: dict[str, Any] = {
        "version": VERSION,
        "market": market,
        "status": "not_started",
        "manifest": {"status": "not_sealed"},
        "blocks": {},
        "gates": [],
        "gates_passed": 0,
        "gates_total": len(NON_PURGE_SPLITS) * 6,
        "blockers": ["manifest_not_sealed"],
        "prospective_observer_registered": False,
        "authority": "research_only",
        "eligible_for_live": False,
        "active_model_changed": False,
        "baseline_accuracy_improved": False,
        "detail": "No M18-A manifest exists — unavailable is not a pass.",
    }
    if not _manifest_path(output_root, market).exists():
        return base
    try:
        manifest = load_selection_readiness_manifest(market, output_root=output_root)
        base["status"] = "manifest_sealed"
        base["manifest"] = {
            "status": "sealed",
            "experiment_id": manifest["experiment_id"],
            "sha256": manifest["artifact_sha256"],
            "sessions": sum(len(manifest["split"][name]) for name in SPLIT_ORDER),
            "candidate_rows": manifest["input_binding"]["m17_acquisition_rows"],
        }
        base["blockers"] = ["dataset_not_built"]
        base["detail"] = "M18-A inputs are sealed; dataset replay is pending."
        latest_path = output_root / market / "latest.json"
        if not latest_path.exists():
            return base
        summary = json.loads(latest_path.read_text(encoding="utf-8"))
        _validate_summary(summary, manifest)
        dataset = summary.get("dataset") or {}
        dataset_path = Path(str(dataset.get("path", "")))
        if not dataset_path.exists() or _hash_file(dataset_path) != dataset.get("sha256"):
            raise ValueError("M18-A dataset binding mismatch")
        return {
            **summary,
            "manifest": base["manifest"],
            "dataset": {
                "rows": dataset["rows"],
                "sha256": dataset["sha256"],
            },
        }
    except Exception as exc:
        return {
            **base,
            "status": "invalid_or_unavailable",
            "blockers": [f"integrity:{type(exc).__name__}"],
            "detail": f"M18-A evidence failed closed: {type(exc).__name__}: {exc}",
        }
