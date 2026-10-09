"""Causal full-universe audit for moves the existing scanner did not identify.

This module deliberately does *not* create a signal.  It answers a narrower research
question after enough future bars exist: which liquid instruments became short-horizon
leaders, which of those were present in the scanner's contemporaneous candidate ledger,
and why were the others absent or rejected?

The distinction is load-bearing:

* ``opportunity_label`` is a hindsight diagnostic built from future highs.  It is never a
  trade result and never enters the production performance denominator.
* ``features`` use the decision close and earlier bars only.  Future columns are kept in a
  separate outcome object, making leakage visible and testable.
* every artifact is market-specific.  NSE, BSE and crypto are never pooled.
* the observer cannot change a model, alert, signal, price, or management decision.

The resulting dataset is intended for a later *pre-move separability* checkpoint.  A
leader ranker may be attempted only if this audit first shows that leaders differ from the
same-session liquid universe using causal features.  Earlier generic cross-sectional
ranking experiments remain rejected; this module does not relabel them as successful.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd

from tradedesk.broker.indstocks.models import IST
from tradedesk.prediction_ledger import canonical_sha256

AUDIT_VERSION = "missed-leader-audit-v1"
FEATURE_VERSION = "leader-causal-features-v1"
LABEL_VERSION = "three-session-max-high-top5-v1"
ACTIVATION_DATE = date(2026, 10, 9)
DEFAULT_OUTPUT_ROOT = Path("data/m14_m18/missed_leader_audit")
MarketName = Literal["nse", "bse", "crypto"]

# These names are part of the frozen feature contract.  None may refer to a lead/future
# column.  Cross-sectional ranks are calculated within one session after universe filters.
FEATURE_COLUMNS = (
    "return_1",
    "return_3",
    "return_5",
    "return_20",
    "gap_return",
    "range_pct",
    "close_location",
    "volume_ratio_20",
    "turnover_ratio_20",
    "distance_sma_20",
    "distance_sma_50",
    "distance_prior_high_20",
    "atr_14_pct",
    "avg_turnover_20",
    "return_5_rank",
    "return_20_rank",
    "volume_ratio_20_rank",
    "distance_prior_high_20_rank",
)
OUTCOME_COLUMNS = (
    "forward_close_return_1",
    "forward_close_return_3",
    "forward_close_return_5",
    "forward_max_high_return_3",
    "forward_min_low_return_3",
    "opportunity_percentile",
    "opportunity_label",
)


@dataclass(frozen=True)
class LeaderAuditSpec:
    market: MarketName
    code_prefix: str
    exchange: str
    minimum_price: float
    minimum_average_turnover: float
    minimum_primary_move: float
    leader_percentile: float = 0.95
    primary_horizon_sessions: int = 3
    minimum_history_sessions: int = 60
    excluded_codes: tuple[str, ...] = ()


SPECS: dict[MarketName, LeaderAuditSpec] = {
    "nse": LeaderAuditSpec("nse", "NSE_", "NSE", 50.0, 50_000_000.0, 0.02),
    "bse": LeaderAuditSpec("bse", "BSE_", "BSE", 50.0, 50_000_000.0, 0.02),
    "crypto": LeaderAuditSpec(
        "crypto",
        "CDX_",
        "CDX",
        0.01,
        2_500_000.0,
        0.04,
        excluded_codes=("CDX_USDTINR", "CDX_USDCINR"),
    ),
}


def spec_for(market: str) -> LeaderAuditSpec:
    try:
        return SPECS[market]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"unsupported leader-audit market: {market}") from exc


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.where(denominator.abs() > 1e-12)


def _latest_source_timestamp(
    connection: duckdb.DuckDBPyConnection,
    spec: LeaderAuditSpec,
    now: datetime,
) -> int:
    sql = """
        SELECT max(c.ts)
        FROM candles c
        JOIN instruments i ON i.scrip_code = c.scrip_code
        WHERE c.interval = '1day'
          AND c.scrip_code LIKE ?
          AND i.kind = 'equity'
          AND upper(i.exch) = ?
    """
    params: list[Any] = [f"{spec.code_prefix}%", spec.exchange]
    if spec.market == "crypto":
        # CoinDCX stores and updates today's still-forming UTC daily bar.  The same closure
        # rule as CandleStore.last_closed_ts keeps that moving bar out of both features and
        # labels.  Equity daily rows are loaded only after the exchange close, so max(ts)
        # is already settled there.
        sql += " AND c.ts + 86400 <= ?"
        params.append(int(now.timestamp()))
    row = connection.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"no closed daily candles available for {spec.market}")
    return int(row[0])


_EXTRACTION_SQL = """
WITH raw AS (
    SELECT
        c.scrip_code,
        coalesce(nullif(i.trading_symbol, ''), c.scrip_code) AS symbol,
        c.ts,
        CAST(timezone('Asia/Kolkata', to_timestamp(c.ts)) AS DATE) AS session_date,
        c.open,
        c.high,
        c.low,
        c.close,
        CAST(c.volume AS DOUBLE) AS volume,
        lag(c.close, 1) OVER w AS close_lag_1,
        lag(c.close, 3) OVER w AS close_lag_3,
        lag(c.close, 5) OVER w AS close_lag_5,
        lag(c.close, 20) OVER w AS close_lag_20,
        lag(c.close, 50) OVER w AS close_lag_50,
        lead(c.close, 1) OVER w AS close_lead_1,
        lead(c.close, 3) OVER w AS close_lead_3,
        lead(c.close, 5) OVER w AS close_lead_5,
        max(c.high) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 1 FOLLOWING AND 3 FOLLOWING
        ) AS high_forward_3,
        min(c.low) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 1 FOLLOWING AND 3 FOLLOWING
        ) AS low_forward_3,
        avg(c.close) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS sma_20,
        avg(c.close) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
        ) AS sma_50,
        avg(c.volume) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS avg_volume_20,
        avg(c.close * c.volume) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS avg_turnover_20,
        max(c.high) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS prior_high_20,
        count(c.close) OVER (
            PARTITION BY c.scrip_code ORDER BY c.ts
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS history_sessions
    FROM candles c
    JOIN instruments i ON i.scrip_code = c.scrip_code
    WHERE c.interval = '1day'
      AND c.scrip_code LIKE ?
      AND i.kind = 'equity'
      AND upper(i.exch) = ?
      AND c.ts BETWEEN ? AND ?
    WINDOW w AS (PARTITION BY c.scrip_code ORDER BY c.ts)
), true_ranges AS (
    SELECT
        *,
        greatest(
            high - low,
            abs(high - close_lag_1),
            abs(low - close_lag_1)
        ) AS true_range
    FROM raw
), rolled AS (
    SELECT
        *,
        avg(true_range) OVER (
            PARTITION BY scrip_code ORDER BY ts
            ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
        ) AS atr_14
    FROM true_ranges
)
SELECT * FROM rolled ORDER BY session_date, scrip_code
"""


def _prepare_causal_frame(
    frame: pd.DataFrame,
    spec: LeaderAuditSpec,
    *,
    require_mature_outcomes: bool,
) -> pd.DataFrame:
    """Apply the shared liquid-universe filters and decision-close feature contract."""

    frame = frame.copy()
    frame["session_date"] = pd.to_datetime(frame["session_date"]).dt.date
    frame = frame[~frame["scrip_code"].isin(spec.excluded_codes)].copy()
    eligible = (
        (frame["history_sessions"] >= spec.minimum_history_sessions)
        & (frame["close"] >= spec.minimum_price)
        & (frame["avg_turnover_20"] >= spec.minimum_average_turnover)
        & (frame["close"] > 0)
    )
    if require_mature_outcomes:
        eligible &= (
            frame["high_forward_3"].notna()
            & frame["low_forward_3"].notna()
            & frame["close_lead_3"].notna()
        )
    frame = frame[eligible].copy()
    if frame.empty:
        state = "mature liquid" if require_mature_outcomes else "liquid decision"
        raise ValueError(f"no {state} rows available for {spec.market}")

    # Every value here is available at the session close. Outcome columns are computed
    # separately by extract_causal_universe and never enter this feature helper.
    frame["return_1"] = _safe_ratio(frame["close"], frame["close_lag_1"]) - 1.0
    frame["return_3"] = _safe_ratio(frame["close"], frame["close_lag_3"]) - 1.0
    frame["return_5"] = _safe_ratio(frame["close"], frame["close_lag_5"]) - 1.0
    frame["return_20"] = _safe_ratio(frame["close"], frame["close_lag_20"]) - 1.0
    frame["gap_return"] = _safe_ratio(frame["open"], frame["close_lag_1"]) - 1.0
    frame["range_pct"] = _safe_ratio(frame["high"] - frame["low"], frame["close"])
    frame["close_location"] = _safe_ratio(
        frame["close"] - frame["low"], frame["high"] - frame["low"]
    ).fillna(0.5)
    frame["volume_ratio_20"] = _safe_ratio(frame["volume"], frame["avg_volume_20"])
    frame["turnover_ratio_20"] = _safe_ratio(
        frame["close"] * frame["volume"], frame["avg_turnover_20"]
    )
    frame["distance_sma_20"] = _safe_ratio(frame["close"], frame["sma_20"]) - 1.0
    frame["distance_sma_50"] = _safe_ratio(frame["close"], frame["sma_50"]) - 1.0
    frame["distance_prior_high_20"] = (
        _safe_ratio(frame["close"], frame["prior_high_20"]) - 1.0
    )
    frame["atr_14_pct"] = _safe_ratio(frame["atr_14"], frame["close"])

    for column in (
        "return_5",
        "return_20",
        "volume_ratio_20",
        "distance_prior_high_20",
    ):
        frame[f"{column}_rank"] = frame.groupby("session_date")[column].rank(
            method="average", pct=True
        )
    return frame


def extract_causal_universe(
    db_path: Path,
    market: str,
    *,
    now: datetime | None = None,
    session_count: int = 60,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract mature, liquid cross-sections without using a future value as a feature."""

    if session_count < 1:
        raise ValueError("session_count must be positive")
    spec = spec_for(market)
    observed_at = now or datetime.now(IST)
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        latest_ts = _latest_source_timestamp(connection, spec, observed_at)
        # 520 calendar days supplies roughly 350 crypto / 355 equity sessions, comfortably
        # above the 60-session feature history plus the requested audit sessions.
        history_start = latest_ts - int(timedelta(days=520).total_seconds())
        frame = connection.execute(
            _EXTRACTION_SQL,
            [f"{spec.code_prefix}%", spec.exchange, history_start, latest_ts],
        ).df()
    finally:
        connection.close()
    if frame.empty:
        raise ValueError(f"no daily rows extracted for {market}")

    frame = _prepare_causal_frame(frame, spec, require_mature_outcomes=True)

    frame["forward_close_return_1"] = (
        _safe_ratio(frame["close_lead_1"], frame["close"]) - 1.0
    )
    frame["forward_close_return_3"] = (
        _safe_ratio(frame["close_lead_3"], frame["close"]) - 1.0
    )
    frame["forward_close_return_5"] = (
        _safe_ratio(frame["close_lead_5"], frame["close"]) - 1.0
    )
    frame["forward_max_high_return_3"] = (
        _safe_ratio(frame["high_forward_3"], frame["close"]) - 1.0
    )
    frame["forward_min_low_return_3"] = (
        _safe_ratio(frame["low_forward_3"], frame["close"]) - 1.0
    )
    frame["opportunity_percentile"] = frame.groupby("session_date")[
        "forward_max_high_return_3"
    ].rank(method="average", pct=True)
    frame["opportunity_label"] = (
        (frame["opportunity_percentile"] >= spec.leader_percentile)
        & (frame["forward_max_high_return_3"] >= spec.minimum_primary_move)
    )

    sessions = sorted(frame["session_date"].unique())[-session_count:]
    frame = frame[frame["session_date"].isin(sessions)].copy()
    frame.sort_values(["session_date", "scrip_code"], inplace=True)
    source = {
        "db_path": str(db_path),
        "latest_closed_session": datetime.fromtimestamp(latest_ts, tz=IST).date().isoformat(),
        "query_history_start": datetime.fromtimestamp(history_start, tz=IST).date().isoformat(),
        "mature_session_start": sessions[0].isoformat(),
        "mature_session_end": sessions[-1].isoformat(),
        "mature_sessions": len(sessions),
        "raw_rows_considered": int(len(frame)),
    }
    return frame, source


def extract_decision_universe(
    db_path: Path,
    market: str,
    *,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract the newest closed session without requiring any future outcome bar.

    This is the forward-only counterpart to :func:`extract_causal_universe`. It uses the
    exact same liquidity rules and causal feature helper, then returns only the newest
    eligible closed session. Callers must persist a decision before a later bar exists.
    """

    spec = spec_for(market)
    observed_at = now or datetime.now(IST)
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        latest_ts = _latest_source_timestamp(connection, spec, observed_at)
        history_start = latest_ts - int(timedelta(days=520).total_seconds())
        frame = connection.execute(
            _EXTRACTION_SQL,
            [f"{spec.code_prefix}%", spec.exchange, history_start, latest_ts],
        ).df()
    finally:
        connection.close()
    if frame.empty:
        raise ValueError(f"no closed daily rows extracted for {market}")

    frame = _prepare_causal_frame(frame, spec, require_mature_outcomes=False)
    decision_session = max(frame["session_date"])
    frame = frame[frame["session_date"] == decision_session].copy()
    frame.sort_values("scrip_code", inplace=True)
    if frame.empty:
        raise ValueError(f"no liquid decision rows available for {market}")
    source = {
        "db_path": str(db_path),
        "latest_closed_session": datetime.fromtimestamp(
            latest_ts, tz=IST
        ).date().isoformat(),
        "decision_session": decision_session.isoformat(),
        "query_history_start": datetime.fromtimestamp(
            history_start, tz=IST
        ).date().isoformat(),
        "universe_rows": int(len(frame)),
    }
    return frame, source


def load_tracker_records(path: Path, market: str) -> tuple[list[dict[str, Any]], str]:
    """Load live records for coverage attribution; backfill is never called prospective."""

    if not path.exists():
        return [], canonical_sha256([])
    content = path.read_bytes()
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(content.decode("utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid tracker JSON at line {line_number}") from exc
        if row.get("market") == market and row.get("source", "backfill") == "live":
            records.append(row)
    return records, hashlib.sha256(content).hexdigest()


def _reason_buckets(reasons: Iterable[Any]) -> list[str]:
    buckets: set[str] = set()
    for value in reasons:
        reason = str(value).lower()
        if "retired" in reason or "research-only" in reason:
            buckets.add("retired_or_research_only")
        evidence_terms = (
            "resolved trades",
            "out-of-sample",
            "win rate",
            "wilson",
            "random-timing",
            "random timing",
        )
        if any(part in reason for part in evidence_terms):
            buckets.add("insufficient_evidence")
        if "regime" in reason or "breadth" in reason or "vix" in reason:
            buckets.add("market_context")
        if any(part in reason for part in ("r:r", "overhead", "room", "cost", "reward")):
            buckets.add("trade_economics")
        if "grade" in reason or "score" in reason:
            buckets.add("score_below_grade")
        if any(part in reason for part in ("turnover", "liquid", "price")):
            buckets.add("liquidity_or_price")
        if "result" in reason or "event" in reason:
            buckets.add("event_risk")
    return sorted(buckets) or ["other_filter"]


def _call_state(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "identification_state": "no_setup_candidate",
            "execution_state": "no_call",
            "rejection_buckets": ["no_pattern_candidate"],
            "setups": [],
        }
    evidence = {str(row.get("evidence_class") or "") for row in records}
    if "qualified_call" in evidence:
        identification = "qualified"
    elif "shadow_call" in evidence or any(bool(row.get("shadow")) for row in records):
        identification = "shadow"
    elif "rejected_call" in evidence or any(row.get("rejected_for") for row in records):
        identification = "rejected"
    else:
        identification = "candidate_unclassified"

    states = {str(row.get("outcome_state") or "") for row in records}
    outcomes = {str(row.get("outcome") or "") for row in records}
    if "target" in outcomes:
        execution = "target"
    elif outcomes & {"stop", "gap_stop"}:
        execution = "stopped"
    elif "resolved_call" in states:
        execution = "resolved_other"
    elif "never_triggered" in states:
        execution = "never_triggered"
    elif "invalid_call" in states:
        execution = "invalid"
    else:
        execution = "pending"
    reasons = [reason for row in records for reason in (row.get("rejected_for") or [])]
    return {
        "identification_state": identification,
        "execution_state": execution,
        "rejection_buckets": _reason_buckets(reasons) if reasons else [],
        "setups": sorted({str(row.get("setup")) for row in records if row.get("setup")}),
    }


def build_audit_rows(
    frame: pd.DataFrame,
    *,
    market: str,
    tracker_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project extraction output into explicitly separated features/outcomes/tracking."""

    spec_for(market)
    missing = set(FEATURE_COLUMNS + OUTCOME_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("leader audit frame missing columns: " + ", ".join(sorted(missing)))
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    observed_sessions: set[str] = set()
    for record in tracker_records:
        session = str(record.get("armed_on") or "")[:10]
        code = str(record.get("scrip_code") or "")
        if session and code:
            observed_sessions.add(session)
            by_key[(session, code)].append(record)

    rows: list[dict[str, Any]] = []
    for source in frame.to_dict(orient="records"):
        session_value = source["session_date"]
        session = (
            session_value.isoformat()
            if hasattr(session_value, "isoformat")
            else str(session_value)[:10]
        )
        code = str(source["scrip_code"])
        call = _call_state(by_key.get((session, code), []))
        if session not in observed_sessions:
            call = {
                "identification_state": "tracker_not_observed",
                "execution_state": "not_observed",
                "rejection_buckets": [],
                "setups": [],
            }
        features = {name: _finite(source.get(name)) for name in FEATURE_COLUMNS}
        outcomes: dict[str, Any] = {
            name: (
                bool(source.get(name))
                if name == "opportunity_label"
                else _finite(source.get(name))
            )
            for name in OUTCOME_COLUMNS
        }
        rows.append(
            {
                "market": market,
                "session": session,
                "scrip_code": code,
                "symbol": str(source.get("symbol") or code),
                "features": features,
                "opportunity": outcomes,
                "tracking": call,
            }
        )
    return rows


def _session_summary(rows: Sequence[Mapping[str, Any]], session: str) -> dict[str, Any]:
    universe = [row for row in rows if row.get("session") == session]
    leaders = [
        row for row in universe if bool((row.get("opportunity") or {}).get("opportunity_label"))
    ]
    observed = any(
        (row.get("tracking") or {}).get("identification_state") != "tracker_not_observed"
        for row in universe
    )
    counts = Counter(
        str((row.get("tracking") or {}).get("identification_state")) for row in leaders
    )
    misses = Counter()
    for row in leaders:
        tracking = row.get("tracking") or {}
        state = str(tracking.get("identification_state"))
        if state == "no_setup_candidate":
            misses["no_pattern_candidate"] += 1
        elif state not in {"qualified"}:
            buckets = tracking.get("rejection_buckets") or [state]
            for bucket in set(map(str, buckets)):
                misses[bucket] += 1
    denominator = len(leaders)

    def recall(numerator: int) -> float | None:
        return numerator / denominator if denominator and observed else None

    top = sorted(
        leaders,
        key=lambda row: float(
            (row.get("opportunity") or {}).get("forward_max_high_return_3") or -math.inf
        ),
        reverse=True,
    )[:15]
    return {
        "session": session,
        "tracker_observed": observed,
        "universe_count": len(universe),
        "leader_count": denominator,
        "candidate_count": (
            counts["qualified"]
            + counts["shadow"]
            + counts["rejected"]
            + counts["candidate_unclassified"]
        ),
        "candidate_recall": recall(
            counts["qualified"]
            + counts["shadow"]
            + counts["rejected"]
            + counts["candidate_unclassified"]
        ),
        "qualified_count": counts["qualified"],
        "qualified_recall": recall(counts["qualified"]),
        "shadow_count": counts["shadow"],
        "rejected_count": counts["rejected"],
        "no_setup_count": counts["no_setup_candidate"],
        "miss_reasons": dict(sorted(misses.items(), key=lambda item: (-item[1], item[0]))),
        "top_leaders": [
            {
                "symbol": row.get("symbol"),
                "scrip_code": row.get("scrip_code"),
                "forward_max_high_return_3": (row.get("opportunity") or {}).get(
                    "forward_max_high_return_3"
                ),
                "forward_close_return_3": (row.get("opportunity") or {}).get(
                    "forward_close_return_3"
                ),
                "identification_state": (row.get("tracking") or {}).get(
                    "identification_state"
                ),
                "execution_state": (row.get("tracking") or {}).get("execution_state"),
                "miss_reasons": (row.get("tracking") or {}).get("rejection_buckets") or (
                    ["no_pattern_candidate"]
                    if (row.get("tracking") or {}).get("identification_state")
                    == "no_setup_candidate"
                    else []
                ),
            }
            for row in top
        ],
    }


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _write_dataset(path: Path, rows: Sequence[Mapping[str, Any]]) -> str:
    """Stream deterministic gzip JSONL and return the stored-byte digest."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0
            ) as compressed:
                for row in rows:
                    line = (
                        json.dumps(
                            row,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    compressed.write(line.encode("utf-8"))
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, path)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def _freeze_prospective_sessions(
    rows: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    *,
    market: str,
    market_root: Path,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    """Freeze each observed post-activation cross-section before later model work.

    Resolver outcomes may legitimately mature later, so ``execution_state`` is excluded
    from the immutable projection.  Decision-time features, opportunity labels, candidate
    presence and prediction-time rejection reasons are included.  A candle correction or
    ledger rewrite therefore raises instead of replacing prospective evidence.
    """

    contract_sha256 = canonical_sha256(
        {
            "version": AUDIT_VERSION,
            "feature_version": FEATURE_VERSION,
            "label_version": LABEL_VERSION,
            "spec": asdict(spec_for(market)),
            "features": FEATURE_COLUMNS,
            "outcomes": OUTCOME_COLUMNS,
        }
    )
    freezes: list[dict[str, Any]] = []
    for summary in summaries:
        session = str(summary["session"])
        if date.fromisoformat(session) <= ACTIVATION_DATE or not summary["tracker_observed"]:
            continue
        session_rows = [row for row in rows if row.get("session") == session]
        immutable_rows = []
        for row in session_rows:
            tracking = dict(row.get("tracking") or {})
            tracking.pop("execution_state", None)
            immutable_rows.append(
                {
                    "market": row.get("market"),
                    "session": row.get("session"),
                    "scrip_code": row.get("scrip_code"),
                    "symbol": row.get("symbol"),
                    "features": row.get("features"),
                    "opportunity": row.get("opportunity"),
                    "tracking_at_prediction": tracking,
                }
            )
        payload: dict[str, Any] = {
            "version": "missed-leader-prospective-freeze-v1",
            "market": market,
            "markets_pooled": False,
            "session": session,
            "created_at": generated_at.isoformat(),
            "contract_sha256": contract_sha256,
            "row_count": len(immutable_rows),
            "causal_rows_sha256": canonical_sha256(immutable_rows),
            "active_model_changed": False,
            "eligible_for_live": False,
        }
        payload["artifact_sha256"] = canonical_sha256(payload)
        path = market_root / "sessions" / f"{session}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                raise ValueError(f"prospective freeze {session} is not an object")
            existing_hash = existing.get("artifact_sha256")
            if existing_hash != canonical_sha256(_report_without_hash(existing)):
                raise ValueError(f"prospective freeze {session} hash mismatch")
            for key in (
                "version",
                "market",
                "contract_sha256",
                "row_count",
                "causal_rows_sha256",
            ):
                if existing.get(key) != payload.get(key):
                    raise ValueError(
                        f"prospective freeze {session} changed at immutable field {key}"
                    )
            payload = existing
        else:
            _atomic_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        freezes.append(
            {
                "session": session,
                "path": str(path),
                "artifact_sha256": payload["artifact_sha256"],
                "causal_rows_sha256": payload["causal_rows_sha256"],
            }
        )
    return freezes


def _report_without_hash(report: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "artifact_sha256"}


def validate_audit_report(report: Mapping[str, Any]) -> None:
    if report.get("version") != AUDIT_VERSION:
        raise ValueError("unsupported missed-leader audit version")
    market = str(report.get("market"))
    spec_for(market)
    if report.get("markets_pooled") is not False:
        raise ValueError("leader evidence must never pool markets")
    if report.get("active_model_changed") is not False:
        raise ValueError("leader audit cannot change the active model")
    if report.get("eligible_for_live") is not False:
        raise ValueError("leader audit cannot authorize live calls")
    digest = report.get("artifact_sha256")
    if not isinstance(digest, str) or digest != canonical_sha256(_report_without_hash(report)):
        raise ValueError("missed-leader audit artifact hash mismatch")
    dataset = report.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ValueError("missed-leader audit dataset binding is missing")
    dataset_hash = dataset.get("sha256")
    if not isinstance(dataset_hash, str) or len(dataset_hash) != 64:
        raise ValueError("missed-leader dataset hash is invalid")


def run_missed_leader_audit(
    *,
    market: str,
    db_path: Path,
    tracker_path: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    now: datetime | None = None,
    session_count: int = 60,
) -> dict[str, Any]:
    """Build and persist one market-isolated observer artifact."""

    spec = spec_for(market)
    generated_at = now or datetime.now(IST)
    frame, source = extract_causal_universe(
        db_path, market, now=generated_at, session_count=session_count
    )
    tracker_records, tracker_hash = load_tracker_records(tracker_path, market)
    rows = build_audit_rows(frame, market=market, tracker_records=tracker_records)
    sessions = sorted({str(row["session"]) for row in rows})
    session_summaries = [_session_summary(rows, session) for session in sessions]
    observed_summaries = [item for item in session_summaries if item["tracker_observed"]]
    latest = observed_summaries[-1] if observed_summaries else session_summaries[-1]
    observed_leaders = sum(int(item["leader_count"]) for item in observed_summaries)
    observed_candidates = sum(int(item["candidate_count"]) for item in observed_summaries)
    observed_qualified = sum(int(item["qualified_count"]) for item in observed_summaries)
    observed_history = {
        "sessions": len(observed_summaries),
        "first_session": observed_summaries[0]["session"] if observed_summaries else None,
        "last_session": observed_summaries[-1]["session"] if observed_summaries else None,
        "leader_count": observed_leaders,
        "candidate_count": observed_candidates,
        "candidate_recall": (
            observed_candidates / observed_leaders if observed_leaders else None
        ),
        "qualified_count": observed_qualified,
        "qualified_recall": (
            observed_qualified / observed_leaders if observed_leaders else None
        ),
    }

    market_root = output_root / market
    dataset_path = market_root / "dataset.jsonl.gz"
    prospective_freezes = _freeze_prospective_sessions(
        rows,
        session_summaries,
        market=market,
        market_root=market_root,
        generated_at=generated_at,
    )
    # Write the mutable aggregate only after every existing immutable session freeze has
    # validated.  A mismatch cannot leave latest.json bound to a newly rewritten dataset.
    dataset_hash = _write_dataset(dataset_path, rows)
    purpose = (
        "prospective"
        if date.fromisoformat(str(latest["session"])) > ACTIVATION_DATE
        else "development_only"
    )
    report: dict[str, Any] = {
        "version": AUDIT_VERSION,
        "feature_version": FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "market": market,
        "markets_pooled": False,
        "generated_at": generated_at.isoformat(),
        "activation_date": ACTIVATION_DATE.isoformat(),
        "purpose": purpose,
        "status": "available" if latest["tracker_observed"] else "tracker_not_observed",
        "source": {
            **source,
            "tracker_path": str(tracker_path),
            "tracker_live_records": len(tracker_records),
            "tracker_snapshot_sha256": tracker_hash,
        },
        "contract": {
            **asdict(spec),
            "feature_columns": list(FEATURE_COLUMNS),
            "outcome_columns": list(OUTCOME_COLUMNS),
            "future_data_used_as_features": False,
            "opportunity_is_trade_performance": False,
        },
        "dataset": {
            "path": str(dataset_path),
            "rows": len(rows),
            "sessions": len(sessions),
            "sha256": dataset_hash,
            "content_encoding": "gzip",
            "format": "jsonl",
        },
        "prospective_session_freezes": prospective_freezes,
        "latest_mature_audit": latest,
        "observed_history": observed_history,
        "session_history": session_summaries,
        "active_model_changed": False,
        "eligible_for_live": False,
        "baseline_accuracy_improved": False,
        "authority": "diagnostic_only",
        "detail": (
            "Hindsight opportunity coverage only. This does not count as a call win, "
            "does not improve the measured baseline, and cannot promote a model."
        ),
    }
    report["artifact_sha256"] = canonical_sha256(report)
    validate_audit_report(report)
    latest_path = market_root / "latest.json"
    _atomic_text(latest_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def load_latest_audit(
    market: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    spec_for(market)
    path = output_root / market / "latest.json"
    if not path.exists():
        return {
            "version": AUDIT_VERSION,
            "market": market,
            "status": "not_available",
            "markets_pooled": False,
            "active_model_changed": False,
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
            "authority": "diagnostic_only",
            "detail": "No missed-leader audit artifact exists — unavailable is not a pass.",
        }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("latest audit must be an object")
        validate_audit_report(report)
        dataset = report["dataset"]
        dataset_path = Path(str(dataset["path"]))
        if not dataset_path.exists():
            raise ValueError("bound leader dataset is missing")
        actual = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        if actual != dataset["sha256"]:
            raise ValueError("bound leader dataset hash mismatch")
        return report
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "version": AUDIT_VERSION,
            "market": market,
            "status": "invalid_or_unreadable",
            "markets_pooled": False,
            "active_model_changed": False,
            "eligible_for_live": False,
            "baseline_accuracy_improved": False,
            "authority": "diagnostic_only",
            "detail": f"Missed-leader audit is invalid — not a pass: {exc}",
        }
