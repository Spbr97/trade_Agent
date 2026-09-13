"""Journal statistics (PLAN.md 1.5, 6.8, 8): per-setup rolling paper expectancy (feeds the
score and the auto-bench), live vs paper gap, rule adherence from tags, and the dashboard's
EOD / weekly / monthly performance views (below) - all read-only reporting over the paper
book's closed trades, no new signal or sizing logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from tradedesk.engine.scoring import (
    BENCH_WINDOW,
    DEFAULT_POLICY,
    EligibilityPolicy,
    TrackRecord,
    eligibility,
)
from tradedesk.journal.db import Journal

__all__ = [
    "BENCH_WINDOW",
    "LiveVsPaper",
    "live_vs_paper",
    "track_record",
    "track_records",
]


def _expectancy(rows: list[Any]) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    rs = [float(r["r_multiple"]) for r in rows]
    wins = sum(1 for r in rs if r > 0)
    return sum(rs) / len(rs), wins / len(rs)


def track_record(
    journal: Journal,
    setup: str,
    *,
    window: int = BENCH_WINDOW,
    policy: EligibilityPolicy = DEFAULT_POLICY,
    oos_trades: int = 0,
    random_baseline_r: float | None = None,
) -> TrackRecord:
    """Rolling stats over the last `window` paper trades of a setup. Benched when the
    rolling expectancy of a full window is negative (PLAN.md 6.8 auto-bench).

    `benched` stays exactly what it was - the "was good, went bad" circuit breaker. It is
    NOT the eligibility gate: it can only fire once `window` trades exist, so on its own it
    let a setup with an empty paper book alert freely. `eligible` is the gate, and it is
    False until the evidence in `policy` is actually there.

    `oos_trades`/`random_baseline_r` are passed in rather than derived here: the paper book
    has no notion of which trades were out-of-sample for a given model, and the random
    baseline comes from the backtest harness, not the journal."""
    rows = journal.trades(source="paper", setup=setup, last=window)
    exp, win = _expectancy(rows)
    benched = len(rows) >= window and exp < 0
    ok, reasons = eligibility(
        trades=len(rows),
        oos_trades=oos_trades,
        win_rate=win,
        expectancy_r=exp,
        random_baseline_r=random_baseline_r,
        policy=policy,
    )
    return TrackRecord(
        trades=len(rows),
        expectancy_r=exp,
        win_rate=win,
        benched=benched,
        oos_trades=oos_trades,
        random_baseline_r=random_baseline_r,
        eligible=ok,
        ineligibility_reasons=reasons,
    )


def track_records(
    journal: Journal, setups: list[str], *, policy: EligibilityPolicy = DEFAULT_POLICY
) -> dict[str, TrackRecord]:
    return {s: track_record(journal, s, policy=policy) for s in setups}


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


# --------------------------------------------------- dashboard: EOD / weekly / monthly


def _trade_dict(r: Any) -> dict[str, Any]:
    return {
        "symbol": r["symbol"],
        "setup": r["setup"],
        "entry_date": r["entry_date"],
        "exit_date": r["exit_date"],
        "qty": r["qty"],
        "entry_price": r["entry_price"],
        "exit_reason": r["exit_reason"],
        "net_pnl": round(float(r["net_pnl"]), 2),
        "r_multiple": round(float(r["r_multiple"]), 2),
        "sessions_held": r["sessions_held"],
    }


def eod_report(journal: Journal, on: date, *, source: str = "paper") -> dict[str, Any]:
    """What closed today, plus what's still open, from the paper book (PLAN.md 8) - the
    only NSE track record with enough volume to be meaningful before real live trades
    accumulate. Reads journal.trades()/open_positions() directly; computes nothing a
    backtest or the live risk manager doesn't already own."""
    on_iso = on.isoformat()
    closed = [r for r in journal.trades(source=source) if r["exit_date"] == on_iso]
    exp, win = _expectancy(closed)
    net_pnl = sum(float(r["net_pnl"]) for r in closed)
    open_positions = journal.open_positions(source=source)
    return {
        "date": on_iso,
        "closed_trades": [_trade_dict(r) for r in closed],
        "closed_count": len(closed),
        "net_pnl": round(net_pnl, 2),
        "win_rate": round(win, 3),
        "expectancy_r": round(exp, 3),
        "open_positions": len(open_positions),
        "open_symbols": sorted({p.signal.symbol for p in open_positions}),
    }


def _period_key(d: date, period: Literal["week", "month"]) -> str:
    if period == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return f"{d.year}-{d.month:02d}"


def performance_rollup(
    journal: Journal,
    *,
    period: Literal["week", "month"] = "week",
    n: int = 12,
    source: str = "paper",
) -> list[dict[str, Any]]:
    """Paper-book P&L grouped by ISO week or calendar month, most recent `n` periods with
    at least one closed trade. A trade counts toward the period it EXITED in - the period
    it can actually be judged in, matching how the paper book itself books P&L."""
    rows = journal.trades(source=source)
    groups: dict[str, list[Any]] = {}
    for r in rows:
        key = _period_key(date.fromisoformat(r["exit_date"]), period)
        groups.setdefault(key, []).append(r)
    out = []
    for key in sorted(groups)[-n:]:
        rs = groups[key]
        exp, win = _expectancy(rs)
        out.append(
            {
                "period": key,
                "trades": len(rs),
                "net_pnl": round(sum(float(r["net_pnl"]) for r in rs), 2),
                "win_rate": round(win, 3),
                "expectancy_r": round(exp, 3),
            }
        )
    return out
