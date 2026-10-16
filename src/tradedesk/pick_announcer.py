"""Announce the intraday mover study's consensus paper picks as they register.

Read-only towards the study: it reads the hash-chained forward_picks.jsonl and toasts each
`consensus_top3` row once, labelled research-only. Called by the equity collector right after
it registers picks, so no chat session has to be running. A toast is local to this machine;
nothing is sent to an outside service.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

LAB_ROOT = Path("data/m14_m18/intraday_mover_study")
STATE_PATH = Path("data/reports/picks_announced.json")
INSTRUMENTS = Path("data/instruments/equity.csv")
CHECKPOINT_NAMES = {
    "opening_bar": "opening bar",
    "opening_two_bars": "first 30 min",
    "first_hour": "first hour",
    "first_two_hours": "first two hours",
}


def _symbols(path: Path = INSTRUMENTS) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8", errors="ignore", newline="") as stream:
            for row in csv.reader(stream):
                if len(row) > 5 and row[1] == "E":
                    out[f"{row[0]}_{row[2]}"] = row[5]
    except OSError:
        pass
    return out


def announce_new_picks(
    market: str,
    session: date,
    *,
    lab_root: Path = LAB_ROOT,
    state_path: Path = STATE_PATH,
    instruments: Path = INSTRUMENTS,
    send: Any = None,
) -> list[str]:
    """Toast each not-yet-announced consensus row for `session`; returns what was announced."""

    chain = lab_root / market / "forward_picks.jsonl"
    try:
        lines = chain.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    try:
        announced = set(json.loads(state_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        announced = set()
    fresh: list[tuple[str, dict[str, Any]]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (
            row.get("event") == "picks_registered"
            and str(row.get("candidate", "")).startswith("consensus")
            and row.get("decision_session") == session.isoformat()
        ):
            key = f"{market}:{row['candidate']}:{row['decision_session']}"
            if key not in announced:
                fresh.append((key, row))
    if not fresh:
        return []
    names = _symbols(instruments)
    if send is None:
        from tradedesk.task_health import toast as send  # local desktop toast
    done: list[str] = []
    for key, row in fresh:
        codes = ", ".join(names.get(c, c) for c in row["codes"])
        label = CHECKPOINT_NAMES.get(row["checkpoint"], row["checkpoint"])
        send(
            f"RESEARCH-ONLY paper picks ({market.upper()}, not an alert)",
            f"SHORT at {row['entry_bar']} open ({label}): {codes}",
        )
        done.append(key)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(sorted(announced | set(done))), encoding="utf-8")
    return done
