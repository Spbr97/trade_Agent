"""Bounded M1 collection for causal AEM market and sector context."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from tradedesk.broker.indstocks.models import IST
from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

CONTEXT_SYMBOLS = ("NIFTY 50", "BANK NIFTY", "Nifty Financial")


def _sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_context_plan(root: Path, output: Path, dataset_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", dataset_id):
        raise ValueError("dataset id must be 32 lowercase hexadecimal characters")
    folder = output / "aem_staged/datasets" / dataset_id
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("id") != dataset_id:
        raise ValueError("staged manifest identity mismatch")
    dates = [date.fromisoformat(value) for value in manifest["source"]["evaluation_dates"]]
    if len(dates) != 120 or dates != sorted(set(dates)):
        raise ValueError("context v1 requires the frozen 120-session evaluation calendar")

    index_path = root / "data/instruments/index.csv"
    with index_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    matched = [
        row for row in rows if row.get("EXCH") == "NSE" and row.get("SEGMENT") in CONTEXT_SYMBOLS
    ]
    if len(matched) != len(CONTEXT_SYMBOLS):
        raise ValueError("required context indices are missing or duplicated")
    found = {row["SEGMENT"]: f"NSE_{row['SECURITY_ID']}" for row in matched}
    if set(found) != set(CONTEXT_SYMBOLS):
        raise ValueError("required context indices are missing or duplicated")
    symbols = {name: found[name] for name in CONTEXT_SYMBOLS}
    if symbols["NIFTY 50"] != manifest["source"]["benchmark_code"]:
        raise ValueError("context benchmark differs from frozen staged source")

    windows = []
    cursor = dates[0]
    end = dates[-1] + timedelta(days=1)
    while cursor < end:
        window_end = min(cursor + timedelta(days=7), end)
        sessions = [day for day in dates if cursor <= day < window_end]
        if sessions:
            windows.append(
                {
                    "start": cursor.isoformat(),
                    "end_exclusive": window_end.isoformat(),
                    "sessions": [day.isoformat() for day in sessions],
                }
            )
        cursor = window_end
    plan = {
        "version": "aem-context-history-v1",
        "dataset_id": dataset_id,
        "dataset_manifest_sha256": digest(manifest_path),
        "instrument_source": str(index_path),
        "instrument_source_sha256": digest(index_path),
        "symbols": symbols,
        "sessions": [day.isoformat() for day in dates],
        "windows": windows,
        "expected_rows": len(symbols) * len(dates) * 375,
        "membership_policy": {
            "NIFTY 50": "market benchmark; no stock membership join",
            "BANK NIFTY": "context data only; historical stock membership unavailable",
            "Nifty Financial": "context data only; historical stock membership unavailable",
        },
    }
    plan["sha256"] = _sha(plan)
    return plan


@contextmanager
def _lock(folder: Path):
    lock = folder / "collector.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("context collector lock exists") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": datetime.now(UTC).isoformat()}, stream)
        yield
    finally:
        lock.unlink()


async def _collect_missing(
    store,
    plan: dict,
    state: dict,
    state_path: Path,
    *,
    max_requests: int,
    client_factory,
    preflight,
) -> dict:
    from tradedesk_lab.aem_history_client import HistoryClientError

    codes = list(plan["symbols"].values())
    result = {"status": "request_budget_reached", "requests_this_run": 0, "attempts": []}
    guard = await asyncio.to_thread(preflight)
    result["preflight"] = guard
    if guard.get("allowed") is not True:
        result["status"] = "blocked_preflight"
        return result
    client = client_factory(max_requests=max_requests)
    try:
        start_index = state["cursor"] % len(plan["windows"])
        for offset in range(len(plan["windows"])):
            if client.request_count >= max_requests:
                break
            index = (start_index + offset) % len(plan["windows"])
            window = plan["windows"][index]
            sessions = [date.fromisoformat(value) for value in window["sessions"]]
            coverage = await asyncio.to_thread(store.coverage, codes, sessions)
            missing_codes = [
                code for code in codes if coverage["by_code"][code]["missing_sessions"]
            ]
            if not missing_codes:
                state["cursor"] = (index + 1) % len(plan["windows"])
                continue
            guard = await asyncio.to_thread(preflight)
            if guard.get("allowed") is not True:
                result.update(status="blocked_preflight", preflight=guard)
                break
            first = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end_exclusive"])
            attempt = {
                "window_index": index,
                "scrip_codes": missing_codes,
                "start": str(first),
                "end_exclusive": str(end),
                "started_at": datetime.now(UTC).isoformat(),
                "status": "pending",
            }
            state["attempts"].append(attempt)
            write_json(state_path, state)
            before = client.request_count
            try:
                candles = await client.candles(
                    missing_codes,
                    datetime.combine(first, time(), IST),
                    datetime.combine(end, time(), IST),
                )
                stats = await asyncio.to_thread(
                    store.ingest,
                    candles,
                    codes=missing_codes,
                    start=first,
                    end_exclusive=end,
                    sessions=sessions,
                    origin="indstocks_context_history_readonly",
                )
                checked = await asyncio.to_thread(store.coverage, missing_codes, sessions)
                attempt.update(
                    status="complete" if checked["missing_sessions"] == 0 else "partial",
                    ingest=stats,
                    coverage=checked,
                )
                state["cursor"] = (index + 1) % len(plan["windows"])
            except HistoryClientError as error:
                attempt.update(status="blocked_client", error_type=type(error).__name__)
                result["status"] = "blocked_client"
            except (duckdb.Error, ValueError, OSError) as error:
                attempt.update(status="blocked_payload_or_store", error_type=type(error).__name__)
                result["status"] = "blocked_payload_or_store"
            finally:
                used = client.request_count - before
                attempt["requests"] = used
                state["requests_recorded"] += used
                result["requests_this_run"] = client.request_count
                result["attempts"].append(dict(attempt))
                write_json(state_path, state)
            if attempt["status"].startswith("blocked_"):
                break
        else:
            result["status"] = "frozen_requests_exhausted"
    finally:
        await client.aclose()
    return result


def collect_context_history(
    root: Path = ROOT,
    output: Path = OUTPUT,
    *,
    dataset_id: str,
    max_requests: int = 0,
    client_factory=None,
    preflight=None,
) -> dict:
    """Prepare, audit, or fill an isolated index-context store."""

    from tradedesk_lab.aem_history_client import HistoryClient
    from tradedesk_lab.aem_history_preflight import history_preflight
    from tradedesk_lab.aem_staging import StageStore

    if type(max_requests) is not int or not 0 <= max_requests <= 100:
        raise ValueError("max requests must be an integer from 0 to 100")
    root, output = Path(root).resolve(), Path(output).resolve()
    plan = build_context_plan(root, output, dataset_id)
    folder = output / "aem_history/context" / dataset_id
    folder.mkdir(parents=True, exist_ok=True)
    plan_path = folder / "plan.json"
    if plan_path.is_file():
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing != plan:
            raise ValueError("frozen context plan changed")
    else:
        write_json(plan_path, plan)
    state_path = folder / "state.json"
    database = folder / "candles.duckdb"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.is_file()
        else {
            "version": 1,
            "plan_sha256": plan["sha256"],
            "cursor": 0,
            "requests_recorded": 0,
            "attempts": [],
        }
    )
    if state["plan_sha256"] != plan["sha256"]:
        raise ValueError("context collection state differs from frozen plan")
    run_id = uuid4().hex
    report_path = folder / "runs" / run_id / "report.json"
    report = {
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset_id,
        "plan_sha256": plan["sha256"],
        "artifact_path": str(report_path),
        "database": str(database),
        "eligible_for_live": False,
        "max_requests": max_requests,
        "requests_this_run": 0,
    }
    with _lock(folder):
        write_json(state_path, state)
        with StageStore(database, output_root=output) as store:
            dates = [date.fromisoformat(value) for value in plan["sessions"]]
            codes = list(plan["symbols"].values())
            coverage = store.coverage(codes, dates)
            if coverage["missing_sessions"] == 0:
                report["status"] = "regular_m1_coverage_complete"
            elif max_requests == 0:
                report["status"] = "prepared_offline"
            else:
                report.update(
                    asyncio.run(
                        _collect_missing(
                            store,
                            plan,
                            state,
                            state_path,
                            max_requests=max_requests,
                            client_factory=client_factory or HistoryClient,
                            preflight=preflight or history_preflight,
                        )
                    )
                )
            report["coverage"] = store.coverage(codes, dates)
    report["requests_recorded_all_runs"] = state["requests_recorded"]
    report["context_ready"] = report["coverage"]["missing_sessions"] == 0
    report["limitations"] = [
        "Collection is prerequisite data work and does not improve measured accuracy.",
        "The three index series do not provide historical point-in-time stock membership.",
        "No live call, management, risk, or order path reads this isolated store.",
        "A later frozen experiment or prospective cohort must prove any improvement.",
    ]
    write_json(report_path, report)
    write_json(
        folder / "latest.json",
        {"id": run_id, "dataset_id": dataset_id, "path": str(report_path)},
    )
    return report
