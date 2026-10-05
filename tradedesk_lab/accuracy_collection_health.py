"""Ordered, fail-closed collection pipelines for fresh accuracy evidence.

This module changes no signal, model, threshold, geometry, or promotion rule.  It makes
the existing scheduled collectors run in dependency order and publishes a durable health
receipt so a missed/stale collector cannot be mistaken for valid ``collecting`` evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradedesk_lab.artifacts import OUTPUT, ROOT, digest, write_json

VERSION = "accuracy-collection-health-v1"
DEFAULT_OUTPUT = OUTPUT / "accuracy_collection_health"
SUPPORTED_MARKETS = frozenset({"nse", "bse", "crypto"})
MAX_CAPTURE_CHARS = 4_000
MAX_FINAL_AGE_HOURS = {"nse": 120.0, "bse": 120.0, "crypto": 2.0}
MAX_RUNNING_AGE_HOURS = 6.0


@dataclass(frozen=True)
class PipelineStep:
    name: str
    argv: tuple[str, ...]
    critical: bool = True


def pipeline_steps(market: str, python: str | None = None) -> tuple[PipelineStep, ...]:
    """Return the registered execution order for one market."""

    executable = python or sys.executable
    cli = (executable, "-m", "tradedesk.cli")
    if market == "nse":
        return (
            PipelineStep("data_load", (*cli, "data", "load")),
            PipelineStep("benchmark_load", (*cli, "data", "load", "NSE_40000001")),
            PipelineStep("paper_update", (*cli, "paper", "update"), critical=False),
            PipelineStep("scan", (*cli, "scan")),
            PipelineStep(
                "signal_and_m8_m11",
                (executable, "scripts/nse_signal_tracker.py", "--strict-accuracy-refresh"),
            ),
            PipelineStep("drift_check", (*cli, "ml", "check-drift"), critical=False),
            PipelineStep(
                "lab_forward", (executable, "-m", "tradedesk_lab", "forward"), critical=False
            ),
            PipelineStep(
                "research_tracker",
                (executable, "scripts/research_tracker.py", "run", "--market", "nse"),
                critical=False,
            ),
        )
    if market == "bse":
        return (
            PipelineStep("data_load", (*cli, "data", "load", "--market", "bse")),
            PipelineStep("scan", (*cli, "scan", "--market", "bse")),
            PipelineStep(
                "signal_tracker",
                (executable, "scripts/bse_signal_tracker.py", "--strict-accuracy-refresh"),
            ),
            PipelineStep(
                "research_and_b1",
                (
                    executable,
                    "scripts/research_tracker.py",
                    "run",
                    "--market",
                    "bse",
                    "--strict-accuracy-refresh",
                ),
            ),
        )
    if market == "crypto":
        return (
            PipelineStep("data_load", (*cli, "data", "load", "--market", "crypto")),
            PipelineStep(
                "signal_c1_c2",
                (executable, "scripts/crypto_signal_tracker.py", "--strict-accuracy-refresh"),
            ),
            PipelineStep(
                "accuracy_recovery_r3",
                (executable, "scripts/crypto_accuracy_recovery.py"),
                critical=False,
            ),
        )
    raise ValueError(f"unsupported accuracy collection market: {market}")


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    )


def _report_sha256(report: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical({key: value for key, value in report.items() if key != "report_sha256"}).encode()
    ).hexdigest()


@contextmanager
def _pipeline_lock(path: Path) -> Iterator[None]:
    """Hold one OS-released lease across a complete dependent pipeline."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.25)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _tail(value: str | None) -> str:
    text = (value or "").strip()
    return text[-MAX_CAPTURE_CHARS:]


def _source_snapshot(root: Path, market: str) -> dict[str, Any]:
    if market in {"nse", "bse"}:
        directory = root / "data/watchlists" / ("bse" if market == "bse" else "")
        paths = sorted(directory.glob("*.json"))
        if not paths:
            return {"available": False, "session": None, "path": None, "sha256": None}
        path = paths[-1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            session = str(payload["on"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {
                "available": False,
                "session": None,
                "path": str(path.relative_to(root)),
                "sha256": None,
            }
    else:
        path = root / "data/reports/crypto_universe_latest.json"
        if not path.exists():
            return {"available": False, "session": None, "path": None, "sha256": None}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            session = str(payload["session"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {
                "available": False,
                "session": None,
                "path": str(path.relative_to(root)),
                "sha256": None,
            }
    return {
        "available": True,
        "session": session,
        "path": str(path.relative_to(root)),
        "sha256": digest(path),
    }


def _write_state(output: Path, market: str, report: dict[str, Any], *, final: bool) -> None:
    report["report_sha256"] = _report_sha256(report)
    if final:
        write_json(output / "runs" / market / f"{report['run_id']}.json", report)
    write_json(output / f"{market}.json", report)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_collection_pipeline(
    market: str,
    *,
    root: Path = ROOT,
    output: Path = DEFAULT_OUTPUT,
    steps: Sequence[PipelineStep] | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Run one registered market pipeline and save a hash-bound health receipt."""

    market = market.lower()
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported accuracy collection market: {market}")
    root, output = Path(root), Path(output)
    registered = tuple(steps or pipeline_steps(market))
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:10]}"
    report: dict[str, Any] = {
        "version": VERSION,
        "run_id": run_id,
        "market": market,
        "status": "queued",
        "queued_at": datetime.now(UTC).isoformat(),
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "source": _source_snapshot(root, market),
        "stages": [],
        "failed_stage": None,
        "baseline_improved": False,
        "eligible_for_live": False,
        "detail": "Collection is running; this is operational health, never a pass.",
    }
    lock_name = "equity.pipeline.lock" if market in {"nse", "bse"} else "crypto.pipeline.lock"
    with _pipeline_lock(output / lock_name):
        acquired_at = datetime.now(UTC).isoformat()
        report["status"] = "running"
        report["started_at"] = acquired_at
        report["updated_at"] = acquired_at
        report["lock_acquired_at"] = acquired_at
        _write_state(output, market, report, final=False)
        failed_critical = False
        any_failure = False
        for step in registered:
            if failed_critical:
                report["stages"].append(
                    {
                        "name": step.name,
                        "status": "skipped_dependency_failure",
                        "critical": step.critical,
                        "returncode": None,
                    }
                )
                continue
            began = time.monotonic()
            try:
                completed = runner(
                    list(step.argv),
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                returncode = int(completed.returncode)
                stdout, stderr = completed.stdout or "", completed.stderr or ""
            except Exception as exc:
                returncode = -1
                stdout, stderr = "", f"{type(exc).__name__}: {exc}"
            if stdout:
                print(stdout, end="\n" if not stdout.endswith("\n") else "")
            if stderr:
                print(stderr, file=sys.stderr, end="\n" if not stderr.endswith("\n") else "")
            stage_status = "completed" if returncode == 0 else "failed"
            report["stages"].append(
                {
                    "name": step.name,
                    "status": stage_status,
                    "critical": step.critical,
                    "returncode": returncode,
                    "elapsed_seconds": round(time.monotonic() - began, 3),
                    "stdout_tail": _tail(stdout),
                    "stderr_tail": _tail(stderr),
                }
            )
            if returncode != 0:
                any_failure = True
                report["failed_stage"] = report["failed_stage"] or step.name
                failed_critical = bool(step.critical)
            report["source"] = _source_snapshot(root, market)
            report["updated_at"] = datetime.now(UTC).isoformat()
            _write_state(output, market, report, final=False)

        report["finished_at"] = datetime.now(UTC).isoformat()
        report["updated_at"] = report["finished_at"]
        report["source"] = _source_snapshot(root, market)
        if failed_critical:
            report["status"] = "failed"
            report["detail"] = (
                "A required collection stage failed; downstream evidence was skipped and this "
                "run is not a pass."
            )
        elif any_failure:
            report["status"] = "degraded"
            report["detail"] = (
                "Core evidence collection completed, but at least one supplementary stage failed."
            )
        else:
            report["status"] = "completed"
            report["detail"] = (
                "Every registered collection stage completed; performance gates remain separate."
            )
        _write_state(output, market, report, final=True)
    return report


def verify_collection_health(
    market: str,
    *,
    output: Path = DEFAULT_OUTPUT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify the latest health receipt without converting health into performance."""

    errors: list[str] = []
    path = Path(output) / f"{market}.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"], "report": None}
    if not isinstance(report, dict):
        return {"passed": False, "errors": ["health receipt is not an object"], "report": None}
    if report.get("version") != VERSION:
        errors.append("version")
    if report.get("market") != market:
        errors.append("market")
    status = report.get("status")
    if status not in {"running", "completed", "degraded", "failed"}:
        errors.append("status")
    if report.get("baseline_improved") is not False:
        errors.append("baseline_improved")
    if report.get("eligible_for_live") is not False:
        errors.append("eligible_for_live")
    if report.get("report_sha256") != _report_sha256(report):
        errors.append("report_sha256")
    if status != "running":
        run_path = Path(output) / "runs" / market / f"{report.get('run_id')}.json"
        try:
            immutable = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            errors.append("run_receipt")
        else:
            if _canonical(immutable) != _canonical(report):
                errors.append("run_receipt_identity")
    timestamp_name = "updated_at" if status == "running" else "finished_at"
    try:
        observed_at = datetime.fromisoformat(str(report[timestamp_name]))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age_hours = (
            (now or datetime.now(UTC)) - observed_at.astimezone(UTC)
        ).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        age_hours = None
        errors.append("freshness_timestamp")
    max_age_hours = (
        MAX_RUNNING_AGE_HOURS if status == "running" else MAX_FINAL_AGE_HOURS.get(market, 0.0)
    )
    freshness_passed = bool(
        age_hours is not None and -0.25 <= age_hours <= max_age_hours
    )
    if not freshness_passed:
        errors.append("stale")
    integrity_errors = [error for error in errors if error not in {"stale", "freshness_timestamp"}]
    operationally_completed = status == "completed"
    return {
        "passed": not errors and operationally_completed,
        "integrity_passed": not integrity_errors,
        "freshness_passed": freshness_passed,
        "operationally_completed": operationally_completed,
        "age_hours": age_hours,
        "max_age_hours": max_age_hours,
        "errors": errors,
        "report": report,
    }


__all__ = [
    "DEFAULT_OUTPUT",
    "PipelineStep",
    "MAX_FINAL_AGE_HOURS",
    "MAX_RUNNING_AGE_HOURS",
    "pipeline_steps",
    "run_collection_pipeline",
    "verify_collection_health",
]
