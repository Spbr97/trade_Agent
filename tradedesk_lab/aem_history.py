"""Bounded, resumable M1 acquisition into an isolated research store only.

The saved plan selects the cohort; downloads cannot change that selection. A staged
minute store is not an evaluation dataset, an order source, or evidence of an edge.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import duckdb

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk_lab.aem_universe_plan import _dates, _manifest, _sha
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json


def _write_checkpoint(path: Path, value: dict) -> None:
    """Keep atomic artifacts under the same no-alias boundary as the stage database."""
    for candidate in (path, path.with_suffix(path.suffix + ".tmp"), *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("checkpoint path aliases are prohibited")
        if candidate.exists() and candidate.is_file() and candidate.stat().st_nlink != 1:
            raise ValueError("checkpoint hard links are prohibited")
    write_json(path, value)


def load_collection_plan(output: Path, plan_id: str) -> tuple[dict, str]:
    """Verify original fingerprints AND the unhashed request list before any writes."""
    if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", plan_id):
        raise ValueError("invalid universe plan identifier")
    base = (output / "aem_universe/runs").resolve()
    path = (base / plan_id / "report.json").resolve()
    if not path.is_relative_to(base):
        raise ValueError("plan path escapes research directory")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("id") != plan_id or report.get("status") != (
        "frozen_development_universe_plan_only"
    ):
        raise ValueError("plan is not a frozen development universe")
    if report.get("eligible_for_live") is not False:
        raise ValueError("research-only plan required")
    warmup = _dates(report["warmup_dates"], "warmup_dates")
    evaluation = _dates(report["evaluation_dates"], "evaluation_dates")
    sessions = warmup + evaluation
    if sessions != sorted(set(sessions)) or date.fromisoformat(report["freeze_on"]) != warmup[-1]:
        raise ValueError("invalid preperiod session boundary")
    manifest, manifest_path = _manifest(output, report["dataset_id"])
    if digest(manifest_path) != report["dataset_manifest_sha256"]:
        raise ValueError("dataset manifest fingerprint changed")
    if manifest["contract_sha256"] != report["contract_sha256"]:
        raise ValueError("plan contract mismatch")
    if manifest["source"]["evaluation_dates"] != report["evaluation_dates"]:
        raise ValueError("plan evaluation calendar mismatch")
    calendar = _dates(manifest["source"]["benchmark_calendar"], "benchmark_calendar")
    if [day for day in calendar if sessions[0] <= day <= sessions[-1]] != sessions:
        raise ValueError("plan sessions differ from benchmark calendar")
    source = report["selection_source"]
    if _sha(source) != report["selection_source_sha256"]:
        raise ValueError("selection source fingerprint mismatch")
    if _sha({"source": source, "shortlist": report["shortlist"]}) != report["selection_sha256"]:
        raise ValueError("selection fingerprint mismatch")
    expected_hash = _sha(
        {
            "selection_sha256": report["selection_sha256"],
            "coverage": report["coverage"],
            "sessions": list(map(str, sessions)),
        }
    )
    if expected_hash != report["plan_sha256"]:
        raise ValueError("coverage plan fingerprint mismatch")
    codes = [row["scrip_code"] for row in report["shortlist"]]
    if (
        not codes
        or len(codes) != len(set(codes))
        or not all(
            isinstance(code, str) and re.fullmatch(r"NSE_[A-Za-z0-9]+", code) for code in codes
        )
    ):
        raise ValueError("invalid or duplicate selected NSE codes")
    plan = report["backfill_plan"]
    if (
        plan["interval"] != "1minute"
        or plan["timezone"] != "Asia/Kolkata"
        or plan["start"] != str(sessions[0])
        or plan["end_exclusive"] != str(sessions[-1] + timedelta(days=1))
        or plan["expected_regular_m1_bars"] != len(codes) * len(sessions) * 375
    ):
        raise ValueError("invalid backfill bounds or interval")
    coverage_codes = [row["scrip_code"] for row in report["coverage"]]
    if coverage_codes != codes:
        raise ValueError("coverage cohort differs from selected cohort")
    grouped = defaultdict(list)
    for row in report["coverage"]:
        intervals = row["intervals"]
        if len(intervals) != 1 or intervals[0]["interval"] != "1minute":
            raise ValueError("M1-only coverage required")
        previous_end = sessions[0]
        for window in intervals[0]["request_windows"]:
            start = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end_exclusive"])
            if not (
                sessions[0] <= start < end <= sessions[-1] + timedelta(days=1)
                and end - start <= timedelta(days=7)
                and start >= previous_end
                and any(start <= day < end for day in sessions)
            ):
                raise ValueError("invalid or overlapping request windows")
            previous_end = end
            grouped[(str(start), str(end))].append(row["scrip_code"])
    expected_batches = [
        {"start": start, "end_exclusive": end, "scrip_codes": items[offset : offset + 5]}
        for (start, end), items in sorted(grouped.items())
        for offset in range(0, len(items), 5)
    ]
    if plan["request_batches"] != expected_batches:
        raise ValueError("request batches differ from frozen coverage windows")
    # The original plan hash predates collection and does not cover every field.
    # Pin the full on-disk artifact as well, making any later mutation fail resume.
    return report, digest(path)


@contextmanager
def _collection_lock(folder: Path):
    """One collector across all plans. A crash leaves an explicit, fail-closed lock."""
    lock = folder / "collector.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError(
            "collector lock exists; verify its owner before manual recovery"
        ) from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": datetime.now(UTC).isoformat()}, stream)
        yield
    finally:
        lock.unlink()


def _seed_source(store, database: Path, codes: list[str], sessions: list[date]) -> dict:
    """Copy query results, never database files, from one read-only source transaction."""
    lower = int(datetime.combine(sessions[0], time(), IST).timestamp())
    end = sessions[-1] + timedelta(days=1)
    upper = int(datetime.combine(end, time(), IST).timestamp())
    fingerprints, summaries = {}, {}
    with duckdb.connect(str(database), read_only=True) as con:
        con.execute("BEGIN TRANSACTION")
        for code in codes:
            rows = con.execute(
                "SELECT ts,open,high,low,close,volume FROM candles WHERE scrip_code=? "
                "AND interval='1minute' AND ts>=? AND ts<? ORDER BY ts",
                [code, lower, upper],
            ).fetchall()
            fingerprints[code] = {
                "rows": len(rows),
                "sha256": hashlib.sha256(
                    json.dumps(rows, allow_nan=False, separators=(",", ":")).encode()
                ).hexdigest(),
            }
            candles = [
                Candle(
                    scrip_code=code,
                    interval=Interval.M1,
                    ts=datetime.fromtimestamp(row[0], IST),
                    open=row[1],
                    high=row[2],
                    low=row[3],
                    close=row[4],
                    volume=row[5],
                )
                for row in rows
            ]
            summaries[code] = store.ingest(
                {code: candles},
                codes=[code],
                start=sessions[0],
                end_exclusive=end,
                sessions=sessions,
                origin="production_readonly_snapshot",
            )
        con.execute("ROLLBACK")
    return {
        "database": str(database),
        "read_only": True,
        "file_copy": False,
        "snapshot_completed_at": datetime.now(UTC).isoformat(),
        "query_fingerprints": fingerprints,
        "ingest": summaries,
        "sha256": _sha(fingerprints),
    }


def _outside_plan_gaps(plan: dict, coverage: dict) -> dict:
    """A plan that omitted then-complete source windows must not silently lose them."""
    unexpected = {}
    windows = {
        row["scrip_code"]: row["intervals"][0]["request_windows"] for row in plan["coverage"]
    }
    for code, item in coverage["by_code"].items():
        missing = [
            day
            for day in item["missing_sessions"]
            if not any(w["start"] <= day < w["end_exclusive"] for w in windows[code])
        ]
        if missing:
            unexpected[code] = missing
    return unexpected


async def _fetch_batches(store, plan, state, state_path, budget, factory, preflight):
    from tradedesk_lab.aem_history_client import HistoryClientError

    batches = plan["backfill_plan"]["request_batches"]
    dates = [date.fromisoformat(day) for day in plan["warmup_dates"] + plan["evaluation_dates"]]
    result = {"requests_this_run": 0, "attempts": [], "status": "request_budget_reached"}
    if not batches:
        result["status"] = "frozen_requests_exhausted"
        return result
    guard = await asyncio.to_thread(preflight)
    result["preflight"] = guard
    if guard.get("allowed") is not True:
        result["status"] = "blocked_preflight"
        return result
    client = factory(max_requests=budget)
    try:
        start_index = state["cursor"] % len(batches)
        for offset in range(len(batches)):
            if client.request_count >= budget:
                break
            index = (start_index + offset) % len(batches)
            batch = batches[index]
            first, end = (
                date.fromisoformat(batch["start"]),
                date.fromisoformat(batch["end_exclusive"]),
            )
            sessions = [day for day in dates if first <= day < end]
            coverage = await asyncio.to_thread(store.coverage, batch["scrip_codes"], sessions)
            codes = [
                code
                for code in batch["scrip_codes"]
                if coverage["by_code"][code]["missing_sessions"]
            ]
            if not codes:
                state["cursor"] = (index + 1) % len(batches)
                continue
            guard = await asyncio.to_thread(preflight)
            if guard.get("allowed") is not True:
                result.update(status="blocked_preflight", preflight=guard)
                break
            attempt = {
                "batch_index": index,
                "scrip_codes": codes,
                "start": str(first),
                "end_exclusive": str(end),
                "started_at": datetime.now(UTC).isoformat(),
                "status": "pending",
            }
            # Persist intent before networking; a crash never marks a window complete.
            state["attempts"].append(attempt)
            _write_checkpoint(state_path, state)
            before = client.request_count
            try:
                candles = await client.candles(
                    codes, datetime.combine(first, time(), IST), datetime.combine(end, time(), IST)
                )
                stats = await asyncio.to_thread(
                    store.ingest,
                    candles,
                    codes=codes,
                    start=first,
                    end_exclusive=end,
                    sessions=sessions,
                    origin="indstocks_historical_readonly",
                )
                checked = await asyncio.to_thread(store.coverage, codes, sessions)
                attempt.update(
                    status="complete" if checked["missing_sessions"] == 0 else "partial",
                    ingest=stats,
                    coverage=checked,
                )
                state["cursor"] = (index + 1) % len(batches)
            except HistoryClientError as error:
                attempt.update(status="blocked_client", error_type=type(error).__name__)
                result["status"] = "blocked_client"
            except (ValueError, duckdb.Error) as error:
                attempt.update(status="blocked_payload_or_store", error_type=type(error).__name__)
                result["status"] = "blocked_payload_or_store"
            finally:
                used = client.request_count - before
                attempt["requests"] = used
                state["requests_recorded"] += used
                result["requests_this_run"] = client.request_count
                result["attempts"].append(dict(attempt))
                _write_checkpoint(state_path, state)
            if attempt["status"].startswith("blocked_"):
                break
        else:
            result["status"] = "frozen_requests_exhausted"
    finally:
        await client.aclose()
    return result


def collect_history(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    plan_id: str,
    max_requests: int = 0,
    client_factory=None,
    preflight=None,
) -> dict:
    """Zero requests prepares/audits offline. Positive budgets attempt bounded GETs."""
    from tradedesk_lab.aem_history_client import HistoryClient
    from tradedesk_lab.aem_history_preflight import history_preflight
    from tradedesk_lab.aem_staging import StageStore

    if (
        isinstance(max_requests, bool)
        or not isinstance(max_requests, int)
        or not 0 <= max_requests <= 100
    ):
        raise ValueError("max_requests must be between 0 and 100")
    root, output = Path(root).resolve(), Path(output).resolve()
    plan, fingerprint = load_collection_plan(output, plan_id)
    parent = output / "aem_history"
    folder = parent / plan_id
    if parent.is_symlink() or folder.is_symlink() or folder.resolve() != folder:
        raise ValueError("staging directory aliases are prohibited")
    folder.mkdir(parents=True, exist_ok=True)
    state_path, database = folder / "state.json", folder / "candles.duckdb"
    run_id = uuid4().hex
    report_path = folder / "runs" / run_id / "report.json"
    report = {
        "id": run_id,
        "plan_id": plan_id,
        "plan_artifact_sha256": fingerprint,
        "created_at": datetime.now(UTC).isoformat(),
        "artifact_path": str(report_path),
        "state_path": str(state_path),
        "database": str(database),
        "eligible_for_live": False,
        "evaluation_ready": False,
        "requests_this_run": 0,
        "max_requests": max_requests,
        "limitations": [
            "Acquisition only: no accuracy, profitability or out-of-sample claim.",
            "D1/benchmark metadata and full earlier M1 warmup still need "
            "a validated evaluation join.",
            "Exactly 375 regular slots are required; no missing candles are fabricated.",
            "Special sessions need explicit calendar review rather than relaxed completeness.",
            "Local preflight cannot establish remote account usage or the shared daily API budget.",
            "Interrupted pending requests have unknown consumption; recorded totals exclude them.",
            "Source snapshot and downloaded history can have different data vintages.",
            "No token generation, credentials mutation, order placement or production writes.",
        ],
    }
    try:
        with _collection_lock(parent):
            if state_path.exists():
                if state_path.is_symlink():
                    raise ValueError("state path alias prohibited")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state["plan_artifact_sha256"] != fingerprint:
                    raise ValueError("frozen plan artifact changed since collection began")
                if not database.is_file():
                    raise ValueError("staging database missing; checkpoint cannot certify coverage")
            else:
                state = {
                    "version": 1,
                    "plan_id": plan_id,
                    "plan_artifact_sha256": fingerprint,
                    "cursor": 0,
                    "requests_recorded": 0,
                    "attempts": [],
                    "seed": None,
                }
                _write_checkpoint(state_path, state)
            report["unconfirmed_prior_attempts"] = sum(
                attempt.get("status") == "pending" for attempt in state["attempts"]
            )
            codes = [row["scrip_code"] for row in plan["shortlist"]]
            sessions = [
                date.fromisoformat(day) for day in plan["warmup_dates"] + plan["evaluation_dates"]
            ]
            with StageStore(
                database, output_root=output, source_path=root / "data/tradedesk.duckdb"
            ) as stage:
                if state["seed"] is None:
                    state["seed"] = _seed_source(
                        stage, root / "data/tradedesk.duckdb", codes, sessions
                    )
                    _write_checkpoint(state_path, state)
                coverage = stage.coverage(codes, sessions)
                outside = _outside_plan_gaps(plan, coverage)
                if outside:
                    report.update(status="blocked_source_plan_drift", gaps_outside_plan=outside)
                elif coverage["missing_sessions"] == 0:
                    report["status"] = "regular_m1_coverage_complete"
                elif max_requests == 0:
                    report["status"] = "prepared_offline"
                else:
                    report.update(
                        asyncio.run(
                            _fetch_batches(
                                stage,
                                plan,
                                state,
                                state_path,
                                max_requests,
                                client_factory or HistoryClient,
                                preflight or history_preflight,
                            )
                        )
                    )
                report["coverage"] = stage.coverage(codes, sessions)
                if report["coverage"]["missing_sessions"] == 0:
                    report["status"] = "regular_m1_coverage_complete"
                report["source_snapshot_sha256"] = state["seed"]["sha256"]
                report["requests_recorded_all_runs"] = state["requests_recorded"]
                _write_checkpoint(state_path, state)
    except (ValueError, OSError, duckdb.Error) as error:
        # Do not include raw broker, credential-store or process command-line messages.
        report.update(status="blocked_preparation", error_type=type(error).__name__)
    _write_checkpoint(report_path, report)
    return report
