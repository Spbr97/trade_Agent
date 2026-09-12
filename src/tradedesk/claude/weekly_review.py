"""Weekly coach (PLAN.md 9): read the journal, paper book and rule-break tags; write a
short review with ONE thing to change next week. Saved as Markdown under data/reviews/."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tradedesk.claude.advisor import ClaudeAdvisor
from tradedesk.claude.models import WeeklyReview
from tradedesk.journal.db import Journal
from tradedesk.journal.stats import summary


def week_bounds(on: date) -> tuple[date, date]:
    monday = on - timedelta(days=on.weekday())
    return monday, monday + timedelta(days=6)


def gather(
    journal: Journal, on: date
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    start, end = week_bounds(on)
    stats = summary(journal)
    trades: list[dict[str, Any]] = []
    for source in ("live", "paper"):
        for r in journal.trades(source=source):
            if start.isoformat() <= r["exit_date"] <= end.isoformat():
                trades.append(
                    {
                        "source": source,
                        "symbol": r["symbol"],
                        "setup": r["setup"],
                        "entry": r["entry_date"],
                        "exit": r["exit_date"],
                        "exit_reason": r["exit_reason"],
                        "r": round(float(r["r_multiple"]), 2),
                        "sessions": r["sessions_held"],
                        "gap_damage": round(float(r["gap_damage"] or 0.0), 0),
                    }
                )
    tags = [
        {"signal_id": t["signal_id"], "tag": t["tag"], "note": t["note"], "at": t["at"]}
        for t in journal.tags_for()
        if start.isoformat() <= str(t["at"])[:10] <= end.isoformat()
    ]
    return stats, trades, tags


def render(review: WeeklyReview, on: date, stats: dict[str, Any]) -> str:
    start, end = week_bounds(on)
    lines = [
        f"# Weekly review {start} - {end}",
        "",
        review.summary,
        "",
        "## What went well",
        *[f"- {x}" for x in review.what_went_well],
        "",
        "## What hurt",
        *[f"- {x}" for x in review.what_hurt],
        "",
        "## Rule breaks",
        *([f"- {x}" for x in review.rule_breaks] or ["- none tagged"]),
        "",
        "## One change next week",
        f"**{review.one_change_next_week}**",
        "",
        "## Numbers (computed by code)",
        f"- paper: {stats['paper']['trades']} trades, {stats['paper']['expectancy_r']:+.2f}R, "
        f"win {stats['paper']['win_rate']:.0%}",
        f"- live: {stats['live']['trades']} trades, {stats['live']['expectancy_r']:+.2f}R, "
        f"win {stats['live']['win_rate']:.0%}",
        f"- rule adherence: {stats['rule_adherence']['adherence']:.0%}",
        f"- live vs paper gap: {stats['live_vs_paper_gap_r']:+.2f}R",
    ]
    return "\n".join(lines) + "\n"


def run_weekly_review(
    advisor: ClaudeAdvisor, journal: Journal, on: date, out_dir: Path
) -> tuple[Path | None, WeeklyReview | None]:
    stats, trades, tags = gather(journal, on)
    review = advisor.weekly_review(stats, trades, tags)
    if review is None:
        return None, None
    out_dir.mkdir(parents=True, exist_ok=True)
    start, _ = week_bounds(on)
    path = out_dir / f"{start.isoformat()}.md"
    path.write_text(render(review, on, stats), encoding="utf-8")
    # Feed the approve/reject queue (review_queue.py): "one thing to change" is exactly
    # the kind of proposal that must wait for a human decision, never apply itself.
    # Path is derived from `out_dir`, NOT review_queue.QUEUE's module-level default -
    # tests call this with a tmp_path out_dir, and hardcoding the default here would
    # silently write real test rows into the production queue on every test run.
    from tradedesk.review_queue import add_item

    add_item(
        market="nse",
        title=f"Week of {start.isoformat()}: {review.one_change_next_week}",
        detail=review.summary,
        proposal=review.one_change_next_week,
        path=out_dir / "queue.jsonl",
    )
    return path, review
