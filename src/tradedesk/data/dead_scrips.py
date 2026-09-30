"""Persistent list of scrip codes the data API keeps rejecting as invalid.

A delisted or renamed instrument returns HTTP 400 "Invalid scrip codes" on every load. Left in
the target list it wastes a data-API call each day and is forever "behind the newest bar",
which buries real gaps in the straggler report. A code is only skipped after it has failed
`MIN_FAILURES` times as a LONE code (the loader bisects failing batches, so a healthy
neighbour is never blamed) and is retried once every `RETRY_DAYS` in case it returns. History
already in the store is never touched, and universe membership still decides scan eligibility -
this only trims what `data load` asks the API for."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

MIN_FAILURES = 3
RETRY_DAYS = 30


def _read(path: Path) -> dict[str, dict[str, object]]:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def skip_set(path: Path, today: date) -> set[str]:
    """Codes to leave out of today's load."""
    out: set[str] = set()
    for code, rec in _read(path).items():
        failures = int(rec.get("failures", 0))  # type: ignore[call-overload]
        last = date.fromisoformat(str(rec.get("last_checked", "1970-01-01")))
        if failures >= MIN_FAILURES and today - last < timedelta(days=RETRY_DAYS):
            out.add(code)
    return out


def record(
    path: Path, today: date, invalid: Iterable[str], succeeded: Iterable[str]
) -> dict[str, dict[str, object]]:
    """Count one more failure per lone-invalid code; forget any code that loaded fine."""
    data = _read(path)
    for code in succeeded:
        data.pop(code, None)
    for code in invalid:
        rec = data.get(code, {"first_seen": today.isoformat(), "failures": 0})
        rec["failures"] = int(rec["failures"]) + 1  # type: ignore[call-overload]
        rec["last_checked"] = today.isoformat()
        data[code] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return data
