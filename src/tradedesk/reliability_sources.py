"""Per-market glue: turn each market's own resolved-call log/journal into the shapes
`reliability.py`'s statistics operate on - `dict[symbol, list[bool]]` for per-symbol
confidence, `(wins, n)` for the pooled overall number.

Kept separate from `reliability.py` on purpose: this module does real I/O (JSONL, SQLite)
against production paths, so its own tests use real fixtures rather than the structural/
property-based approach `reliability.py`'s tests use - mixing the two styles in one file
would blur what each file's docstring promises about how it was verified.

`overall_reliability()`'s scope (see reliability.py) is REAL LIVE resolved calls only:
NSE paper book (`paper_trades`, which only ever holds real triggered signals) + crypto/BSE
rows tagged `source="live"`. Backfill and research_tracker's forward-only candidates are
both excluded - see reliability.py::overall_reliability's docstring for why.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.analysis import BSE_LOG, CRYPTO_LOG, NSE_JOURNAL
from tradedesk.reliability import (
    HISTORY_PATH,
    SymbolConfidence,
    append_reliability_history,
    load_reliability_history,
    overall_reliability,
    symbol_confidence,
)

# risk.yaml's own max_risk_per_trade_pct default (first-50-live-trades tier). Backfilled
# calls were never actually sized against real capital, so this is a documented, stated
# assumption for turning an R-multiple into a % figure - not a real ledger number, the same
# honesty standard as research_tracker.py's own cost_r_for approximation.
RISK_PER_TRADE_PCT = 0.0025


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _cutoff_iso(days: int | None) -> str | None:
    if days is None:
        return None
    return (datetime.now().date() - timedelta(days=days)).isoformat()


def crypto_bse_symbol_confidence(
    log_path: Path, *, source: str | None = None
) -> list[SymbolConfidence]:
    """symbol -> confidence from one market's resolved-call JSONL log. `source` ("live" or
    "backfill"), when given, restricts to that tag; None pools both - backfill is real
    market history even though it isn't something "the agent" did live, so a per-symbol
    read benefits from the deeper evidence where available."""
    outcomes: dict[str, list[bool]] = {}
    for r in _read_jsonl(log_path):
        if r.get("label") is None:
            continue
        if source is not None and r.get("source", "backfill") != source:
            continue
        outcomes.setdefault(r["symbol"], []).append(r["label"] == 1)
    return symbol_confidence(outcomes)


def crypto_bse_live_counts(log_path: Path) -> tuple[int, int]:
    """(wins, n) over resolved LIVE-tagged calls only - the population `overall_reliability`
    is meant to pool, per its own docstring."""
    resolved = [
        r
        for r in _read_jsonl(log_path)
        if r.get("label") is not None and r.get("source", "backfill") == "live"
    ]
    wins = sum(1 for r in resolved if r["label"] == 1)
    return wins, len(resolved)


def crypto_bse_backfill_pnl(log_path: Path, *, days: int | None = None) -> dict[str, Any]:
    """Backfill P&L summary over a timeframe (days=None -> all time) - replaces the removed
    row-by-row "Past (backfill)" browsing table with one compact stat. R-multiples are what
    this project actually grades calls in (signal_tracker.py); pnl_pct is that R-multiple
    total scaled by risk.yaml's own per-trade risk assumption - see RISK_PER_TRADE_PCT."""
    cutoff = _cutoff_iso(days)
    resolved = [
        r
        for r in _read_jsonl(log_path)
        if r.get("source", "backfill") == "backfill"
        and r.get("r_multiple") is not None
        and (cutoff is None or (r.get("resolved_at") or "") >= cutoff)
    ]
    n = len(resolved)
    if n == 0:
        return {"n": 0, "sum_r": 0.0, "avg_r": None, "win_rate": None, "pnl_pct": 0.0, "days": days}
    sum_r = sum(float(r["r_multiple"]) for r in resolved)
    wins = sum(1 for r in resolved if r.get("label") == 1)
    return {
        "n": n,
        "sum_r": sum_r,
        "avg_r": sum_r / n,
        "win_rate": wins / n,
        "pnl_pct": sum_r * RISK_PER_TRADE_PCT * 100,
        "days": days,
    }


def nse_symbol_confidence(journal_path: Path = NSE_JOURNAL) -> list[SymbolConfidence]:
    from tradedesk.journal import Journal

    if not journal_path.exists():
        return []
    outcomes: dict[str, list[bool]] = {}
    with Journal(journal_path) as jn:
        for row in jn.trades(source="paper"):
            r_multiple = row["r_multiple"]
            if r_multiple is None:
                continue
            outcomes.setdefault(row["symbol"], []).append(r_multiple > 0)
    return symbol_confidence(outcomes)


def nse_live_counts(journal_path: Path = NSE_JOURNAL) -> tuple[int, int]:
    from tradedesk.journal import Journal

    if not journal_path.exists():
        return 0, 0
    with Journal(journal_path) as jn:
        rows = [row for row in jn.trades(source="paper") if row["r_multiple"] is not None]
    wins = sum(1 for row in rows if row["r_multiple"] > 0)
    return wins, len(rows)


def overall_reliability_now() -> dict[str, Any]:
    """The one top-line number: Wilson lower bound pooled across every real, live resolved
    call across all three markets, plus the per-market breakdown behind it."""
    nse_wins, nse_n = nse_live_counts(NSE_JOURNAL)
    crypto_wins, crypto_n = crypto_bse_live_counts(CRYPTO_LOG)
    bse_wins, bse_n = crypto_bse_live_counts(BSE_LOG)
    total = overall_reliability(nse_wins + crypto_wins + bse_wins, nse_n + crypto_n + bse_n)
    total["by_market"] = {
        "nse": overall_reliability(nse_wins, nse_n),
        "crypto": overall_reliability(crypto_wins, crypto_n),
        "bse": overall_reliability(bse_wins, bse_n),
    }
    return total


def log_daily_reliability_snapshot(path: Path = HISTORY_PATH) -> dict[str, Any] | None:
    """Append one row to the reliability history IF one hasn't already been logged today -
    idempotent regardless of how many times or from which market's schedule this gets
    called, so it's safe to wire into exactly one daily job (NSE's research_tracker `run`,
    since it fires once a day) without a separate lock. Returns the logged record, or None
    if today's snapshot already existed."""
    today = date.today().isoformat()
    history = load_reliability_history(path)
    if any(row.get("date") == today for row in history):
        return None
    record = overall_reliability_now()
    append_reliability_history(record, path)
    return record
