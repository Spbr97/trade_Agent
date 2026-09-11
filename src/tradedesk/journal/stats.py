"""Journal statistics (PLAN.md 1.5, 6.8, 8): per-setup rolling paper expectancy (feeds the
score and the auto-bench), live vs paper gap, rule adherence from tags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradedesk.engine.scoring import TrackRecord
from tradedesk.journal.db import Journal

BENCH_WINDOW = 30


def _expectancy(rows: list[Any]) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    rs = [float(r["r_multiple"]) for r in rows]
    wins = sum(1 for r in rs if r > 0)
    return sum(rs) / len(rs), wins / len(rs)


def track_record(journal: Journal, setup: str, *, window: int = BENCH_WINDOW) -> TrackRecord:
    """Rolling stats over the last `window` paper trades of a setup. Benched when the
    rolling expectancy of a full window is negative (PLAN.md 6.8 auto-bench)."""
    rows = journal.trades(source="paper", setup=setup, last=window)
    exp, win = _expectancy(rows)
    benched = len(rows) >= window and exp < 0
    return TrackRecord(trades=len(rows), expectancy_r=exp, win_rate=win, benched=benched)


def track_records(journal: Journal, setups: list[str]) -> dict[str, TrackRecord]:
    return {s: track_record(journal, s) for s in setups}


@dataclass
class LiveVsPaper:
    live_trades: int
    paper_trades: int
    live_expectancy_r: float
    paper_expectancy_r: float

    @property
    def gap_r(self) -> float:
        return self.live_expectancy_r - self.paper_expectancy_r


def live_vs_paper(journal: Journal) -> LiveVsPaper:
    """Compare live and paper results on the SAME signals (the ones you actually took)."""
    live = journal.trades(source="live")
    ids = {r["signal_id"] for r in live}
    paper = [r for r in journal.trades(source="paper") if r["signal_id"] in ids]
    le, _ = _expectancy(live)
    pe, _ = _expectancy(paper)
    return LiveVsPaper(len(live), len(paper), le, pe)


def rule_adherence(journal: Journal) -> tuple[int, int, float]:
    """(trades, trades with a rule-break tag, adherence fraction)."""
    live = journal.trades(source="live")
    if not live:
        return 0, 0, 1.0
    broken = {
        r["signal_id"]
        for r in journal.tags_for(r["signal_id"] for r in live)
        if r["tag"] == "rule_break"
    }
    return len(live), len(broken), 1 - len(broken) / len(live)


def summary(journal: Journal) -> dict[str, Any]:
    paper = journal.trades(source="paper")
    live = journal.trades(source="live")
    pe, pw = _expectancy(paper)
    le, lw = _expectancy(live)
    setups = sorted({r["setup"] for r in paper})
    n, broken, adherence = rule_adherence(journal)
    return {
        "paper": {"trades": len(paper), "expectancy_r": round(pe, 3), "win_rate": round(pw, 3)},
        "live": {"trades": len(live), "expectancy_r": round(le, 3), "win_rate": round(lw, 3)},
        "by_setup": {
            s: {
                "trades": t.trades,
                "expectancy_r": round(t.expectancy_r, 3),
                "win_rate": round(t.win_rate, 3),
                "benched": t.benched,
            }
            for s, t in track_records(journal, setups).items()
        },
        "rule_adherence": {"trades": n, "rule_breaks": broken, "adherence": round(adherence, 3)},
        "live_vs_paper_gap_r": round(live_vs_paper(journal).gap_r, 3),
    }
