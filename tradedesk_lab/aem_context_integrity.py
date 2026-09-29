"""Causal decision-time join of the frozen AEM population to index context.

The output is feature-only research data: outcome/label columns are deliberately not
copied.  A context bar is usable only after its one-minute interval has completed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

JOIN_VERSION = "aem-index-context-join-v1"
WINDOWS = (1, 3, 5, 15)
SYMBOL_SLUGS = {
    "NIFTY 50": "nifty50",
    "BANK NIFTY": "bank_nifty",
    "Nifty Financial": "nifty_financial",
}
SAFE_EVENT_COLUMNS = (
    "event_id",
    "scrip_code",
    "session_date",
    "decision",
    "available_at",
)
OUTCOME_COLUMNS = {
    "status",
    "label",
    "strict_success",
    "target_hit",
    "outcome",
    "entry_at",
    "exit_at",
    "net_pnl",
    "gross_r",
    "net_r",
}


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _ist_timestamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("context decision timestamp must be timezone-aware")
    stamp = stamp.tz_convert(IST)
    if stamp != stamp.floor("min"):
        raise ValueError("context decision timestamp must align to an exact minute")
    return stamp


def _valid_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(frame):
        raise ValueError("context candles are missing OHLCV columns")
    out = frame.sort_index().copy()
    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        raise ValueError("context candle timestamps must be timezone-aware")
    index = index.tz_convert(IST)
    if index.hasnans or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("context candle timestamps are invalid")
    if not (index == index.floor("min")).all():
        raise ValueError("context candle timestamps must align to exact minutes")
    out.index = index
    try:
        values = out[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("context candles contain nonnumeric OHLCV") from exc
    if (
        not np.isfinite(values).all()
        or (values[:, :4] <= 0).any()
        or (values[:, 4] < 0).any()
        or (values[:, 2] > np.minimum(values[:, 0], values[:, 3])).any()
        or (values[:, 1] < np.maximum(values[:, 0], values[:, 3])).any()
    ):
        raise ValueError("context candles contain invalid OHLCV")
    return out


def _index_features(frame: pd.DataFrame, decision_at: pd.Timestamp, slug: str) -> dict:
    session_open = decision_at.normalize() + pd.Timedelta(hours=9, minutes=15)
    last_closed_open = decision_at - pd.Timedelta(minutes=1)
    if last_closed_open < session_open:
        raise ValueError("context decision precedes the first completed regular bar")
    expected = pd.date_range(session_open, last_closed_open, freq="min")
    prefix = frame.loc[(frame.index >= session_open) & (frame.index <= last_closed_open)]
    missing = expected.difference(prefix.index)
    if len(missing):
        first = pd.Timestamp(missing[0]).strftime("%H:%M")
        raise ValueError(f"missing_context_prefix:{slug}:{first}")
    if not prefix.index.equals(expected):
        raise ValueError(f"unexpected_context_prefix:{slug}")

    last = prefix.iloc[-1]
    result: dict[str, Any] = {
        f"{slug}_last_closed_at": pd.Timestamp(prefix.index[-1]).isoformat(),
        f"{slug}_bars_available": len(prefix),
        f"{slug}_session_return": float(last.close / prefix.iloc[0].open - 1.0),
    }
    for window in WINDOWS:
        available = len(prefix) >= window
        result[f"{slug}_return_{window}m_available"] = available
        result[f"{slug}_return_{window}m"] = (
            float(last.close / prefix.iloc[-window].open - 1.0) if available else None
        )

    if len(prefix) >= 15:
        bar_returns = (
            prefix.iloc[-15:].close.to_numpy(dtype=float)
            / prefix.iloc[-15:].open.to_numpy(dtype=float)
            - 1.0
        )
        result[f"{slug}_volatility_15m"] = float(np.std(bar_returns, ddof=0))
    else:
        result[f"{slug}_volatility_15m"] = None

    zero_volume = int((prefix.volume == 0).sum())
    volume_complete = zero_volume == 0
    result[f"{slug}_zero_volume_bars"] = zero_volume
    result[f"{slug}_volume_complete"] = volume_complete
    result[f"{slug}_vwap_available"] = volume_complete
    if volume_complete:
        typical = (prefix.high + prefix.low + prefix.close) / 3.0
        cumulative = (typical * prefix.volume).cumsum() / prefix.volume.cumsum()
        current_vwap = float(cumulative.iloc[-1])
        previous_vwap = float(cumulative.iloc[-2]) if len(cumulative) > 1 else current_vwap
        result[f"{slug}_vwap_distance"] = float(last.close / current_vwap - 1.0)
        result[f"{slug}_vwap_slope"] = float(current_vwap / previous_vwap - 1.0)
    else:
        # Partial index volume would bias a nominal VWAP. Fail the feature closed.
        result[f"{slug}_vwap_distance"] = None
        result[f"{slug}_vwap_slope"] = None
    return result


def context_snapshot(
    frames: dict[str, pd.DataFrame],
    *,
    decision_at: Any,
    symbols: dict[str, str],
) -> dict:
    """Return causal price context or an explicit join exclusion."""

    when = _ist_timestamp(decision_at)
    result: dict[str, Any] = {
        "context_joined": False,
        "context_exclusion_reason": None,
        "context_cutoff": (when - pd.Timedelta(minutes=1)).isoformat(),
    }
    features: dict[str, Any] = {}
    try:
        for name, slug in SYMBOL_SLUGS.items():
            code = symbols.get(name)
            if not code or code not in frames:
                raise ValueError(f"missing_context_symbol:{name}")
            features.update(_index_features(frames[code], when, slug))
    except ValueError as exc:
        result["context_exclusion_reason"] = str(exc)
        return result

    returns_5m = [features[f"{slug}_return_5m"] for slug in SYMBOL_SLUGS.values()]
    if all(value is not None for value in returns_5m):
        array = np.asarray(returns_5m, dtype=float)
        features["context_dispersion_5m"] = float(np.std(array, ddof=0))
        features["market_bank_divergence_5m"] = float(array[1] - array[0])
        features["market_financial_divergence_5m"] = float(array[2] - array[0])
        features["context_direction_agreement_5m"] = bool(
            np.all(array > 0) or np.all(array < 0)
        )
    else:
        features.update(
            context_dispersion_5m=None,
            market_bank_divergence_5m=None,
            market_financial_divergence_5m=None,
            context_direction_agreement_5m=False,
        )
    for window in WINDOWS:
        features[f"context_return_{window}m_available"] = all(
            bool(features[f"{slug}_return_{window}m_available"])
            for slug in SYMBOL_SLUGS.values()
        )
    features["context_vwap_available"] = all(
        bool(features[f"{slug}_vwap_available"]) for slug in SYMBOL_SLUGS.values()
    )
    result.update(features, context_joined=True)
    return result


def join_context_events(
    events: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    *,
    symbols: dict[str, str],
    sessions: list[str],
    earliest_decision_time: str,
    latest_decision_time: str,
) -> tuple[pd.DataFrame, dict]:
    """Account for every event without copying any outcome into the feature artifact."""

    missing = set(SAFE_EVENT_COLUMNS) - set(events)
    if missing:
        raise ValueError(f"context events missing {sorted(missing)}")
    if events.empty or events.event_id.isna().any() or events.event_id.duplicated().any():
        raise ValueError("context event identifiers must be present and unique")
    if not set(events.decision).issubset({"TRADE", "NO_TRADE"}):
        raise ValueError("context events contain an unknown decision")
    allowed_sessions = set(sessions)
    if set(events.session_date.astype(str)) - allowed_sessions:
        raise ValueError("context event falls outside the frozen calendar")

    validated = {code: _valid_frame(frame) for code, frame in frames.items()}
    rows = []
    exclusions = Counter()
    for event in events.sort_values(["session_date", "event_id"]).itertuples(index=False):
        when = _ist_timestamp(event.available_at)
        if str(when.date()) != str(event.session_date):
            raise ValueError("context decision timestamp differs from event session")
        clock = when.strftime("%H:%M")
        if not earliest_decision_time <= clock <= latest_decision_time:
            raise ValueError("context decision falls outside the frozen window")
        snapshot = context_snapshot(validated, decision_at=when, symbols=symbols)
        row = {name: getattr(event, name) for name in SAFE_EVENT_COLUMNS}
        row.update(snapshot)
        rows.append(row)
        if not snapshot["context_joined"]:
            exclusions[str(snapshot["context_exclusion_reason"])] += 1
    joined = pd.DataFrame(rows)
    if set(joined) & OUTCOME_COLUMNS:
        raise AssertionError("outcome data entered the context feature artifact")
    audit = {
        "events_total": len(joined),
        "trades": int((joined.decision == "TRADE").sum()),
        "no_trades": int((joined.decision == "NO_TRADE").sum()),
        "joined": int(joined.context_joined.sum()),
        "excluded": int((~joined.context_joined).sum()),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "outcome_columns_present": sorted(set(joined) & OUTCOME_COLUMNS),
    }
    return joined, audit


def _load_context(database: Path, codes: list[str]) -> dict[str, pd.DataFrame]:
    with duckdb.connect(str(database), read_only=True) as con:
        marker = con.execute(
            "SELECT value FROM aem_stage_meta WHERE key='schema_version'"
        ).fetchone()
        if marker != ("1",):
            raise ValueError("unsupported context store schema")
        placeholders = ",".join("?" for _ in codes)
        rows = con.execute(
            f"SELECT scrip_code,ts,open,high,low,close,volume FROM candles "
            f"WHERE interval=? AND scrip_code IN ({placeholders}) ORDER BY scrip_code,ts",
            [Interval.M1.value, *codes],
        ).fetchall()
    frames = {}
    for code in codes:
        selected = [row[1:] for row in rows if row[0] == code]
        frame = pd.DataFrame(
            selected, columns=["ts", "open", "high", "low", "close", "volume"]
        )
        frame.index = pd.to_datetime(frame.pop("ts"), unit="s", utc=True).dt.tz_convert(IST)
        frames[code] = _valid_frame(frame)
    return frames


def _source_gaps(
    frames: dict[str, pd.DataFrame], codes: list[str], sessions: list[str]
) -> list[dict]:
    gaps = []
    for code in codes:
        frame = frames[code]
        for raw_day in sessions:
            day = date.fromisoformat(raw_day)
            start = pd.Timestamp(datetime.combine(day, time(9, 15), IST))
            expected = pd.date_range(start, periods=375, freq="min")
            actual = frame.index[frame.index.date == day]
            missing = expected.difference(actual)
            if len(missing):
                gaps.append(
                    {
                        "scrip_code": code,
                        "session": raw_day,
                        "missing_bars": len(missing),
                        "first_missing_ist": pd.Timestamp(missing[0]).strftime("%H:%M"),
                        "last_missing_ist": pd.Timestamp(missing[-1]).strftime("%H:%M"),
                    }
                )
    return gaps


def run_context_integrity(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_id: str,
) -> dict:
    """Build a deterministic feature-only join and an auditable integrity report."""

    if not re.fullmatch(r"[0-9a-f]{32}", dataset_id):
        raise ValueError("dataset id must be 32 lowercase hexadecimal characters")
    root, output = Path(root).resolve(), Path(output).resolve()
    dataset = output / "aem_staged/datasets" / dataset_id
    context = output / "aem_history/context" / dataset_id
    manifest_path = dataset / "manifest.json"
    events_path = dataset / "events.csv"
    plan_path = context / "plan.json"
    database = context / "candles.duckdb"
    research_plan = root / "docs/plan-aem-index-context-accuracy.md"
    for path in (manifest_path, events_path, plan_path, database, research_plan):
        if not path.is_file():
            raise ValueError(f"required context-integrity input is missing: {path.name}")
    if (context / "collector.lock").exists():
        raise ValueError("context collector is active or its lock requires review")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if manifest.get("id") != dataset_id or plan.get("dataset_id") != dataset_id:
        raise ValueError("context integrity dataset identity mismatch")
    if plan.get("dataset_manifest_sha256") != digest(manifest_path):
        raise ValueError("context plan manifest fingerprint mismatch")
    frozen_plan = dict(plan)
    plan_sha = frozen_plan.pop("sha256", None)
    if plan_sha != _sha(frozen_plan):
        raise ValueError("context plan fingerprint mismatch")
    if set(plan.get("symbols", {})) != set(SYMBOL_SLUGS):
        raise ValueError("context plan symbol registry mismatch")

    events = pd.read_csv(events_path, keep_default_na=False)
    if len(events) != manifest.get("events"):
        raise ValueError("frozen AEM event count changed")
    codes = list(plan["symbols"].values())
    frames = _load_context(database, codes)
    joined, audit = join_context_events(
        events,
        frames,
        symbols=plan["symbols"],
        sessions=plan["sessions"],
        earliest_decision_time=manifest["contract"]["earliest_decision_time"],
        latest_decision_time=manifest["contract"]["latest_decision_time"],
    )
    source_gaps = _source_gaps(frames, codes, plan["sessions"])

    run_id = uuid4().hex
    target = output / "aem_context/integrity/runs" / run_id
    target.mkdir(parents=True, exist_ok=False)
    features_path = target / "context_features.csv"
    joined.to_csv(features_path, index=False, float_format="%.12g", na_rep="")

    def available_count(column: str) -> int:
        if column not in joined:
            return 0
        values = joined.loc[joined.context_joined, column]
        return int(values.fillna(False).astype(bool).sum())

    availability = {
        f"return_{window}m_all_indices": available_count(
            f"context_return_{window}m_available"
        )
        for window in WINDOWS
    }
    availability["vwap_all_indices"] = available_count("context_vwap_available")
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "integrity_passed" if audit["excluded"] == 0 else "integrity_exclusions",
        "milestone": "AEM index context checkpoint 1: causal join integrity",
        "dataset_id": dataset_id,
        "join_version": JOIN_VERSION,
        "eligible_for_live": False,
        "baseline_improved": False,
        "source": {
            "manifest_sha256": digest(manifest_path),
            "events_csv_sha256": digest(events_path),
            "context_plan_sha256": plan_sha,
            "context_database_sha256": digest(database),
            "research_plan_sha256": digest(research_plan),
            "implementation_sha256": digest(Path(__file__)),
            "test_sha256": digest(root / "tests_lab/test_aem_context_integrity.py"),
        },
        "population": audit,
        "feature_availability": availability,
        "source_gaps": source_gaps,
        "causality": {
            "rule": "bar_open + one minute <= decision available_at",
            "latest_usable_bar": "decision available_at minus one minute",
            "outcome_columns_present": audit["outcome_columns_present"],
            "future_bar_dependency": False,
            "membership_join_used": False,
        },
        "artifacts": {
            "context_features": str(features_path),
            "context_features_sha256": digest(features_path),
        },
        "decision": {
            "price_context_ready_for_mechanism_check": audit["excluded"] == 0,
            "vwap_context_ready": availability["vwap_all_indices"] == audit["joined"],
            "run_selector": False,
            "change_canonical_baseline": False,
            "change_live_behavior": False,
        },
        "limitations": [
            "All 120 sessions are consumed development evidence.",
            "A causal join is data integrity, not evidence of predictive improvement.",
            "Index VWAP features fail closed whenever any completed bar has zero volume.",
            "Current sector/index membership is not joined to stocks.",
            "No production scanner, dashboard, management, risk, alert, broker, or "
            "order path reads this artifact.",
        ],
    }
    write_json(target / "report.json", report)
    write_json(
        output / "aem_context/integrity/latest.json",
        {"id": run_id, "path": str(target / "report.json"), "dataset_id": dataset_id},
    )
    return report
