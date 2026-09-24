"""Freeze a pre-evaluation liquidity universe and plan M1 coverage without networking."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.broker.indstocks.ratelimit import DAILY_CAP
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_data import frame_fingerprint
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json
from tradedesk_lab.nse_universe import _coverage, _daily_quality, _epoch, _instrument_reason


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def _dates(values: list[str], label: str) -> list[date]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a nonempty date list")
    result = [date.fromisoformat(value) for value in values]
    if result != sorted(set(result)):
        raise ValueError(f"{label} must be sorted and unique")
    return result


def _manifest(output: Path, dataset_id: str) -> tuple[dict, Path]:
    if not isinstance(dataset_id, str) or not re.fullmatch(r"[a-f0-9]{32}", dataset_id):
        raise ValueError("Invalid AEM dataset identifier")
    base = (output / "aem/datasets").resolve()
    path = (base / dataset_id / "manifest.json").resolve()
    if not path.is_relative_to(base):
        raise ValueError("AEM manifest escapes the dataset directory")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("id") != dataset_id:
        raise ValueError("AEM manifest identifier mismatch")
    return value, path


def freeze_universe_plan(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_id: str,
    shortlist_size: int = 50,
) -> dict:
    """Plan only: select by liquidity known before the saved evaluation begins.

    Uses manifest JSON, never deserializes a dataset or reads its trade outcomes. Current
    metadata and revised candle history still preclude a genuine vintage/OOS claim.
    """
    if (
        not isinstance(shortlist_size, int)
        or isinstance(shortlist_size, bool)
        or not 1 <= shortlist_size <= 500
    ):
        raise ValueError("shortlist_size must be between 1 and 500")
    root, output = Path(root), Path(output)
    manifest, manifest_path = _manifest(output, dataset_id)
    contract = AemContract(**manifest["contract"])
    if manifest.get("contract_sha256") != contract.sha256:
        raise ValueError("AEM manifest contract hash mismatch")
    calendar = _dates(manifest["source"]["benchmark_calendar"], "benchmark_calendar")
    evaluation = _dates(manifest["source"]["evaluation_dates"], "evaluation_dates")
    if not set(evaluation).issubset(calendar):
        raise ValueError("evaluation_dates must belong to the benchmark calendar")
    if [day for day in calendar if evaluation[0] <= day <= evaluation[-1]] != evaluation:
        raise ValueError("evaluation_dates must be consecutive benchmark sessions")
    prior = [day for day in calendar if day < evaluation[0]]
    warmup_sessions = max(20, contract.rvol_lookback_sessions)
    if len(prior) < max(warmup_sessions, contract.liquidity_sessions):
        raise ValueError("insufficient prior benchmark sessions for warmup")
    freeze_on = prior[-1]
    sessions = prior[-warmup_sessions:] + evaluation
    run_id = uuid4().hex
    path = output / "aem_universe/runs" / run_id / "report.json"
    database = root / "data/tradedesk.duckdb"
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "artifact_path": str(path),
        "status": "frozen_development_universe_plan_only",
        "eligible_for_live": False,
        "dataset_id": dataset_id,
        "dataset_manifest_sha256": digest(manifest_path),
        "database": str(database),
        "freeze_on": str(freeze_on),
        "evaluation_dates": list(map(str, evaluation)),
        "warmup_dates": list(map(str, sessions[: -len(evaluation)])),
        "contract_sha256": contract.sha256,
        "shortlist_size_requested": shortlist_size,
        "selection_rule": {
            "minimum_daily_sessions": max(60, contract.minimum_daily_sessions),
            "liquidity_sessions": contract.liquidity_sessions,
            "minimum_median_turnover_inr": contract.minimum_median_turnover_inr,
            "ranking": "preperiod median turnover descending; symbol/code tie-break",
            "setup_eligibility_used": False,
            "intraday_availability_used": False,
            "trade_outcomes_used": False,
        },
        "limitations": [
            "This is a preperiod-selected development cohort, not untouched OOS evidence.",
            "Top-liquidity stocks are not representative of all NSE stocks or all liquidity bands.",
            "Current metadata is not historical membership; survivorship bias remains.",
            "Candles may be revised; timestamp slicing cannot restore their original vintage.",
            "Local stored instruments are not proof of complete exchange listing coverage.",
            "Future new listings and liquidity entrants are excluded by a fixed initial cohort.",
            "Suspensions, surveillance, spreads, circuits and tradability remain unverified.",
            "M1 warmup covers at least twenty prior sessions; missing slots may need earlier data. "
            "AEM price indicators consume full available prior history.",
            "Coverage assumes regular 09:15-15:30 sessions; review special sessions separately.",
            "No credentials, API calls, downloads, source writes or order placement occur.",
        ],
    }
    try:
        with duckdb.connect(str(database), read_only=True) as con:
            con.execute("BEGIN TRANSACTION")
            _populate(con, report, prior, sessions, contract, shortlist_size)
            con.execute("ROLLBACK")
    except (OSError, duckdb.Error) as error:
        report.update(
            status="blocked_source_unavailable",
            error=str(error),
            recovery="Retry after the local writer releases DuckDB; no live source copy was made.",
        )
    path.parent.mkdir(parents=True, exist_ok=False)
    write_json(path, report)
    return report


def _populate(con, report, prior, sessions, contract, shortlist_size):
    freeze_on = prior[-1]
    cutoff = _epoch(freeze_on + timedelta(days=1))
    metadata = (
        con.execute(
            "WITH d AS (SELECT DISTINCT scrip_code FROM candles WHERE interval='1day' AND ts<?) "
            "SELECT coalesce(i.scrip_code,d.scrip_code) AS scrip_code, "
            "i.exch,i.kind,i.trading_symbol,i.series,i.instrument_name "
            "FROM instruments i FULL OUTER JOIN d USING (scrip_code) ORDER BY scrip_code",
            [cutoff],
        )
        .df()
        .replace({np.nan: None})
        .to_dict("records")
    )
    audit, permitted, sources = [], [], []
    for row in metadata:
        reason = _instrument_reason(row)
        if reason:
            audit.append(
                {
                    "scrip_code": row["scrip_code"],
                    "symbol": row.get("trading_symbol") or row["scrip_code"],
                    "status": "rejected",
                    "reasons": [reason],
                }
            )
        else:
            permitted.append(row)
    for offset in range(0, len(permitted), 100):
        batch = permitted[offset : offset + 100]
        placeholders = ",".join("?" for _ in batch)
        data = con.execute(
            "SELECT scrip_code,ts,open,high,low,close,volume FROM candles "
            f"WHERE interval='1day' AND ts<? AND scrip_code IN ({placeholders}) "
            "ORDER BY scrip_code,ts",
            [cutoff, *[row["scrip_code"] for row in batch]],
        ).df()
        data.index = pd.DatetimeIndex(
            pd.to_datetime(data.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        )
        grouped = {
            code: group.drop(columns="scrip_code")
            for code, group in data.groupby("scrip_code", sort=False)
        }
        for row in batch:
            code = row["scrip_code"]
            frame = grouped.get(code, data.iloc[:0].drop(columns="scrip_code"))
            sources.append({"scrip_code": code, "daily": frame_fingerprint(frame)})
            reason = _daily_quality(frame, prior, freeze_on)
            if reason is None and len(frame) < max(60, contract.minimum_daily_sessions):
                reason = "insufficient_daily_warmup"
            median = None
            if reason is None:
                recent = frame.iloc[-contract.liquidity_sessions :]
                median = float((recent.close * recent.volume).median())
                if median < contract.minimum_median_turnover_inr:
                    reason = "insufficient_preperiod_liquidity"
            audit.append(
                {
                    "scrip_code": code,
                    "symbol": row.get("trading_symbol") or code,
                    "status": "rejected" if reason else "preperiod_liquidity_candidate",
                    "reasons": [reason] if reason else [],
                    "daily_sessions_as_of": len(frame),
                    "last_daily_session": str(frame.index[-1].date()) if len(frame) else None,
                    "median_turnover_inr": median,
                }
            )
    audit.sort(key=lambda row: row["scrip_code"])
    candidates = sorted(
        [row for row in audit if row["status"] == "preperiod_liquidity_candidate"],
        key=lambda row: (-row["median_turnover_inr"], row["symbol"], row["scrip_code"]),
    )
    for rank, row in enumerate(candidates, 1):
        row["liquidity_rank"] = rank
    shortlist = candidates[:shortlist_size]
    coverage = [
        {
            "scrip_code": row["scrip_code"],
            "symbol": row["symbol"],
            "intervals": [_coverage(con, row["scrip_code"], Interval.M1, sessions)],
        }
        for row in shortlist
    ]
    grouped_windows = defaultdict(list)
    for row in coverage:
        for window in row["intervals"][0]["request_windows"]:
            grouped_windows[(window["start"], window["end_exclusive"])].append(row["scrip_code"])
    batches = [
        {"start": start, "end_exclusive": end, "scrip_codes": codes[offset : offset + 5]}
        for (start, end), codes in sorted(grouped_windows.items())
        for offset in range(0, len(codes), 5)
    ]
    checks = [row["intervals"][0] for row in coverage]
    one_code = sum(row["planned_missing_window_requests"] for row in checks)
    upper = sum(row["full_span_request_upper_bound_before_retries"] for row in checks)
    selection_source = {
        "freeze_on": str(freeze_on),
        "selection_rule": report["selection_rule"],
        "preperiod_calendar": list(map(str, prior)),
        "metadata": metadata,
        "daily_frames": sources,
    }
    report.update(
        summary={
            "instrument_records_considered": len(metadata),
            "nse_cash_eq_instruments_considered": len(permitted),
            "liquidity_candidates": len(candidates),
            "shortlisted": len(shortlist),
            "rejected": len(audit) - len(candidates),
            "rejection_reasons": dict(
                Counter(reason for row in audit for reason in row["reasons"])
            ),
        },
        candidates=candidates,
        shortlist=shortlist,
        instrument_audit=audit,
        selection_source=selection_source,
        selection_source_sha256=_sha(selection_source),
        selection_sha256=_sha({"source": selection_source, "shortlist": shortlist}),
        coverage=coverage,
        backfill_plan={
            "mode": "plan_only_no_network",
            "interval": Interval.M1.value,
            "timezone": "Asia/Kolkata",
            "start": str(sessions[0]),
            "end_exclusive": str(sessions[-1] + timedelta(days=1)),
            "start_epoch_seconds": _epoch(sessions[0]),
            "end_exclusive_epoch_seconds": _epoch(sessions[-1] + timedelta(days=1)),
            "evaluation_sessions": len(report["evaluation_dates"]),
            "warmup_sessions": len(report["warmup_dates"]),
            "total_sessions": len(sessions),
            "expected_regular_m1_bars": len(shortlist) * len(sessions) * 375,
            "max_codes_per_request": 5,
            "max_window_calendar_days": Interval.M1.max_window_days,
            "api_data_requests_per_second": 5,
            "api_daily_cap": DAILY_CAP,
            "planned_missing_window_requests_one_code": one_code,
            "planned_missing_window_requests_batched": len(batches),
            "full_span_request_upper_bound_one_code_before_retries": upper,
            "minimum_daily_budgets_one_code": math.ceil(upper / DAILY_CAP),
            "request_batches": batches,
            "assumptions": [
                "All budgets exclude retry/auth overhead and other account consumers.",
                "The production limiter counts per instance, not aggregate account-wide use.",
                "Use one coordinated data client after writer release; do not stop live service.",
                "An isolated lab staging store must not overwrite production candles.",
                "Incremental load_history skips earlier holes; fetch explicit missing windows.",
                "Client candles_history can pad narrow recent windows by up to six days; retain "
                "only requested bounds and verify exact per-session slots after any future fetch.",
            ],
        },
    )
    report["plan_sha256"] = _sha(
        {
            "selection_sha256": report["selection_sha256"],
            "coverage": coverage,
            "sessions": list(map(str, sessions)),
        }
    )
