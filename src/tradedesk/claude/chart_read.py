"""Evening chart reads (PLAN.md 9): for the top setups, ask Claude for a three-line read and
apply it to the watchlist under the configured mode.

- notify: the read is attached as a note; nothing else changes.
- veto:   'downgrade' lowers the grade by one (A->B, B->C); 'remove' rejects the entry.
          A read can never raise a grade, add an entry, or alter a price or quantity -
          `apply_reads` is a pure function and the tests prove those invariants.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tradedesk.claude.advisor import ClaudeAdvisor
from tradedesk.claude.models import ChartRead
from tradedesk.engine.scoring import Grade
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry

LOWER = {Grade.A: Grade.B, Grade.B: Grade.C, Grade.C: Grade.C}


def setup_facts(e: WatchlistEntry) -> dict[str, Any]:
    s = e.signal
    return {
        "symbol": s.symbol,
        "setup": s.setup.value,
        "grade": e.grade.value,
        "score": e.score,
        "reasons": s.reasons,
        "trigger": s.trigger,
        "stop": s.stop,
        "t1": s.t1,
        "t2": s.t2,
        "atr": s.atr,
        "regime": s.regime,
        "rs_percentile": s.rs_percentile,
        "results_in_sessions": e.results_in_sessions,
        "overhead": s.geometry.get("overhead"),
    }


def read_watchlist(
    advisor: ClaudeAdvisor,
    wl: Watchlist,
    *,
    max_setups: int,
    hourly_charts: Mapping[str, Path] | None = None,
) -> dict[str, ChartRead]:
    """Reads for the top `max_setups` active entries (by score). Keyed by signal id."""
    reads: dict[str, ChartRead] = {}
    if not advisor.enabled:
        return reads
    for e in wl.active[:max_setups]:
        daily = Path(e.chart_path) if e.chart_path else None
        hourly = (hourly_charts or {}).get(e.signal.scrip_code)
        read = advisor.read_chart(setup_facts(e), daily, hourly)
        if read is not None:
            reads[e.signal.id] = read
    return reads


def apply_reads(wl: Watchlist, reads: Mapping[str, ChartRead], mode: str) -> Watchlist:
    """Return a new Watchlist with the reads applied under `mode` ('off' | 'notify' | 'veto')."""
    if mode == "off" or not reads:
        return wl
    entries: list[WatchlistEntry] = []
    for e in wl.entries:
        read = reads.get(e.signal.id)
        if read is None:
            entries.append(e)
            continue
        note = (
            f"Claude ({read.confidence}): {read.pattern_quality} {read.overhead_supply} "
            f"Fails if: {read.failure_condition} -> {read.verdict}"
        )
        update: dict[str, Any] = {"score_notes": [*e.score_notes, note]}
        if mode == "veto":
            if read.verdict == "downgrade":
                grade = LOWER[e.grade]
                update["grade"] = grade
                update["alertable"] = e.alertable and grade is not Grade.C
            elif read.verdict == "remove":
                update["rejected_for"] = [*e.rejected_for, "Claude veto: " + read.failure_condition]
                update["alertable"] = False
        entries.append(e.model_copy(update=update))
    entries.sort(key=lambda x: (-x.score, x.signal.scrip_code))
    return wl.model_copy(update={"entries": entries})
