"""M17: candidate ledger, separate artifacts and durable holdout reservations."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def now() -> str:
    return datetime.now(UTC).isoformat()


class Registry:
    def __init__(self, path: Path, *, readonly: bool = False):
        if readonly:
            self.con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.con = sqlite3.connect(path, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA busy_timeout=30000")
        if not readonly:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.executescript("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT,
                    status TEXT, metadata TEXT, report TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    family TEXT, parameters TEXT, status TEXT, metrics TEXT,
                    artifact_path TEXT, artifact_sha256 TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS candidate_family ON candidates(family);
                CREATE INDEX IF NOT EXISTS candidate_experiment ON candidates(experiment_id);
                CREATE TABLE IF NOT EXISTS holdouts (
                    scope TEXT PRIMARY KEY, experiment_id TEXT, reserved_at TEXT);
                PRAGMA user_version=1;
            """)

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *args: object) -> None:
        self.con.close()

    def begin(self, metadata: dict[str, Any]) -> str:
        identifier = uuid4().hex
        with self.con:
            self.con.execute(
                "INSERT INTO experiments VALUES (?,?,NULL,?,?,NULL,NULL)",
                (identifier, now(), "running", json.dumps(metadata)),
            )
        return identifier

    def candidate(
        self,
        experiment: str,
        family: str,
        parameters: dict[str, Any],
        metrics: dict[str, Any],
        *,
        status: str = "completed",
        artifact: str | None = None,
        sha: str | None = None,
        error: str | None = None,
    ) -> str:
        identifier = uuid4().hex
        with self.con:
            self.con.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    experiment,
                    family,
                    json.dumps(parameters),
                    status,
                    json.dumps(metrics, allow_nan=False),
                    artifact,
                    sha,
                    error,
                ),
            )
        return identifier

    def reserve_holdout(self, scope: str, experiment: str) -> None:
        """Reserve BEFORE scoring: an interrupted run cannot silently reuse the holdout."""
        try:
            with self.con:
                self.con.execute("INSERT INTO holdouts VALUES (?,?,?)", (scope, experiment, now()))
        except sqlite3.IntegrityError as exc:
            raise ValueError("This market/date holdout has already been consumed") from exc

    def finish(
        self, identifier: str, report: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        with self.con:
            self.con.execute(
                "UPDATE experiments SET finished_at=?,status=?,report=?,error=? WHERE id=?",
                (
                    now(),
                    "failed" if error else "completed",
                    json.dumps(report, allow_nan=False),
                    error,
                    identifier,
                ),
            )

    def runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM experiments ORDER BY started_at DESC LIMIT ?", (min(max(limit, 1), 500),)
        ).fetchall()
        return [self._decode(row) for row in rows]

    def run(self, identifier: str) -> dict[str, Any] | None:
        row = self.con.execute("SELECT * FROM experiments WHERE id=?", (identifier,)).fetchone()
        if row is None:
            return None
        result = self._decode(row)
        result["candidates"] = [
            self._decode(r)
            for r in self.con.execute(
                "SELECT * FROM candidates WHERE experiment_id=?", (identifier,)
            )
        ]
        return result

    def best(self, metric: str = "brier", family: str | None = None) -> dict[str, Any] | None:
        if metric not in {"brier", "auc", "precision", "net_r", "wilson_lower"}:
            raise ValueError("Unknown metric")
        rows = [
            self._decode(r)
            for r in self.con.execute(
                "SELECT * FROM candidates WHERE status='completed' AND (? IS NULL OR family=?)",
                (family, family),
            )
        ]
        eligible = [r for r in rows if r["metrics"].get(metric) is not None]
        if not eligible:
            return None
        return sorted(eligible, key=lambda r: r["metrics"][metric], reverse=metric != "brier")[0]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for name in ("metadata", "report", "parameters", "metrics"):
            if name in result and result[name] is not None:
                result[name] = json.loads(result[name])
        return result
