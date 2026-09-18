"""Read-only coverage audit for the app-derived MCB reference examples."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json

REFERENCE_QUERIES = (
    "Goodluck India",
    "Nitin Spinners",
    "Skipper",
    "Uniparts India",
    "Purple Style Labs",
)


def _coverage(con, code: str) -> list[dict]:
    rows = con.execute(
        "SELECT interval,count(*),min(ts),max(ts) FROM candles "
        "WHERE scrip_code=? GROUP BY interval ORDER BY interval",
        [code],
    ).fetchall()
    return [
        {
            "interval": interval,
            "bars": count,
            "from": datetime.fromtimestamp(first, UTC).isoformat(),
            "through": datetime.fromtimestamp(last, UTC).isoformat(),
        }
        for interval, count, first, last in rows
    ]


def audit_mcb_data(root: Path = ROOT, output: Path = OUTPUT) -> dict:
    """Resolve reference names and report local candle coverage without downloading data."""
    database = root / "data/tradedesk.duckdb"
    references = []
    with duckdb.connect(str(database), read_only=True) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info('instruments')").fetchall()}
        name_column = "symbol_name" if "symbol_name" in columns else "trading_symbol"
        for query in REFERENCE_QUERIES:
            tokens = [token for token in query.upper().split() if len(token) > 2]
            name_match = f"upper(coalesce({name_column},'')) LIKE ?"
            clauses = " AND ".join(
                f"(upper(coalesce(trading_symbol,'')) LIKE ? OR {name_match})" for _ in tokens
            )
            values = [f"%{token}%" for token in tokens for _ in range(2)]
            matches = con.execute(
                "SELECT scrip_code,trading_symbol," + name_column + " FROM instruments "
                "WHERE exch='NSE' AND (" + clauses + ") ORDER BY trading_symbol LIMIT 20",
                values,
            ).fetchall()
            references.append(
                {
                    "source_name": query,
                    "matches": [
                        {
                            "scrip_code": code,
                            "trading_symbol": symbol,
                            "symbol_name": name,
                            "coverage": _coverage(con, code),
                        }
                        for code, symbol, name in matches
                    ],
                }
            )
        interval_summary = [
            {"interval": row[0], "symbols": row[1], "bars": row[2]}
            for row in con.execute(
                "SELECT interval,count(DISTINCT scrip_code),count(*) FROM candles "
                "WHERE scrip_code LIKE 'NSE_%' GROUP BY interval ORDER BY interval"
            ).fetchall()
        ]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "database": str(database),
        "mode": "read_only_local_coverage_audit",
        "reference_source": "successful-call examples supplied through the master plan",
        "warning": (
            "Success-only examples define hypotheses; they are not a training set or proof of edge."
        ),
        "interval_summary": interval_summary,
        "references": references,
    }
    write_json(output / "mcb/data-audit.json", report)
    return report
