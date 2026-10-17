"""Plain-number phone digests: close-job status, end-of-day, intraday check, multi-day calls
and the weekly roll-up.

Every function reads files the scheduled jobs already write and returns ``(title, body)``;
`scripts/send_digest.py` delivers them through task_health.alert (ntfy on the server). Nothing
here computes a signal or places an order: the numbers are counts, outcomes and the research
calls' own recorded levels, and every message says it is research only. A missing or odd field
is skipped, never guessed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST

REPORTS = Path("data/reports")
LAB = Path("data/m14_m18")
WATCHLISTS = Path("data/watchlists")
CALLS = {
    "nse": "research_calls.jsonl",
    "bse": "research_calls_bse.jsonl",
    "crypto": "research_calls_crypto.jsonl",
}
CONTROL_RULE = "random_eligible"
MAX_LISTED = 6
RESEARCH = "RESEARCH ONLY, not advice or an alert."


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        pass
    return rows


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _day(stamp: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(IST).date()
    except ValueError:
        return None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _r(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}R"


def _pct(part: int, whole: int) -> str:
    return "n/a" if not whole else f"{100 * part / whole:.0f}%"


def _price(value: float) -> str:
    return f"{value:.2f}" if abs(value) >= 1 else f"{value:.5g}"


def _outcomes(rows: list[Mapping[str, Any]]) -> str:
    counts = Counter(str(r.get("outcome")) for r in rows)
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"


def _name(market: str) -> str:
    return market.upper()


def _resolved_on(rows: list[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("outcome") and _day(r.get("resolved_at")) == day]


def close_job(
    market: str, job: str, result: str, exit_status: str, *, today: date, root: Path = Path(".")
) -> tuple[str, str]:
    ok = result == "success"
    title = f"{_name(market)} close job {'finished OK' if ok else 'FAILED'}"
    lines = [f"{job}: {result} (exit {exit_status})"]
    if ok:
        folder = root / WATCHLISTS if market == "nse" else root / WATCHLISTS / market
        files = sorted(folder.glob("20*.json"))
        if files:
            watch = _json(files[-1])
            entries = watch.get("entries") or []
            regime = watch.get("regime")
            regime_name = regime.get("regime") if isinstance(regime, dict) else regime
            lines.append(
                f"Scan {files[-1].stem}: {len(entries)} entries, regime {regime_name or 'n/a'}"
            )
    lines.append(RESEARCH)
    return title, "\n".join(lines)


def eod_summary(market: str, day: date, *, root: Path = Path(".")) -> tuple[str, str]:
    reports = root / REPORTS
    tracker = _jsonl(reports / f"{market}_signal_tracking.jsonl")
    calls = _jsonl(reports / CALLS[market])
    lines = [f"{_name(market)} end of day {day.isoformat()}"]
    done = _resolved_on(tracker, day)
    r_values = [float(r["r_multiple"]) for r in done if r.get("r_multiple") is not None]
    lines.append(
        f"Tracked signals: resolved {len(done)} ({_outcomes(done)}), mean {_r(_mean(r_values))}; "
        f"open {sum(1 for r in tracker if not r.get('outcome'))}"
    )
    armed = [r for r in calls if r.get("armed_on") == day.isoformat()]
    done_calls = _resolved_on(calls, day)
    call_r = [float(r["r_multiple"]) for r in done_calls if r.get("r_multiple") is not None]
    lines.append(
        f"Research calls (incl. random control): armed {len(armed)}; resolved {len(done_calls)} "
        f"({_outcomes(done_calls)}), mean {_r(_mean(call_r))}; "
        f"open {sum(1 for r in calls if not r.get('outcome'))}"
    )
    lines.append(RESEARCH)
    return f"{_name(market)} end-of-day numbers", "\n".join(lines)


def intraday_check(market: str, *, root: Path = Path(".")) -> tuple[str, str]:
    report = _json(root / LAB / "intraday_mover_study" / market / "latest.json")
    title = f"{_name(market)} intraday study check"
    if not report:
        return title, f"No intraday study report yet for {market}.\n{RESEARCH}"
    consensus = (report.get("pick_record") or {}).get("consensus") or {}
    latest, overall = consensus.get("latest") or {}, consensus.get("all") or {}
    lines = [f"{_name(market)} intraday study, latest session {consensus.get('latest_session')}"]
    lines.append(
        f"Consensus picks that session: {latest.get('picks', 0)}, "
        f"winners {latest.get('winners', 0)}, beat market {latest.get('beat_market', 0)}"
    )
    rate = overall.get("beat_market_rate")
    lines.append(
        f"All {consensus.get('sessions', 0)} sessions: {overall.get('picks', 0)} picks, "
        f"winners {overall.get('winners', 0)}, beat market "
        f"{'n/a' if rate is None else f'{100 * rate:.0f}%'}"
    )
    cutoff = consensus.get("cutoff") or {}
    if cutoff:
        lines.append(
            f"Cut-off: {cutoff.get('status')} ({cutoff.get('picks')}/{cutoff.get('picks_required')}"
            f" picks, needs beat rate {cutoff.get('beat_rate_required')})"
        )
    for score in (report.get("forward") or [])[:3]:
        lines.append(
            f"Forward {score.get('candidate')}: {score.get('forward_days')} days, "
            f"net {100 * float(score.get('mean_net') or 0):+.2f}%, t {score.get('t_excess', 0):.1f}"
        )
    survivors = (report.get("last_search") or {}).get("survivors") or []
    lines.append(f"Search survivors: {len(survivors)}")
    lines.append(RESEARCH)
    return title, "\n".join(lines)


def _levels(call: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """(entry reference, stop, first target) from the call's own recorded numbers."""

    try:
        if call.get("entry") is not None and call.get("stop") is not None:
            entry, stop = float(call["entry"]), float(call["stop"])
            target = float(call["t1"]) if call.get("t1") is not None else None
            if target is None:
                target = entry + float(call["target_r"]) * (entry - stop)
            return entry, stop, target
        close = float(call["close_at_arm"])
        risk = float(call["stop_atr"]) * float(call["atr_at_arm"])
        return close, close - risk, close + float(call["target_r"]) * risk
    except (KeyError, TypeError, ValueError):
        return None


def multiday_calls(market: str, *, root: Path = Path(".")) -> tuple[str, str] | None:
    calls = [c for c in _jsonl(root / REPORTS / CALLS[market]) if c.get("rule") != CONTROL_RULE]
    if not calls:
        return None
    armed = max(str(c.get("armed_on") or "") for c in calls)
    today = [c for c in calls if c.get("armed_on") == armed]
    by_rule = Counter(str(c.get("rule")) for c in today)
    lines = [
        f"{_name(market)} multi-day calls armed {armed}: {len(today)} "
        f"({', '.join(f'{k} {v}' for k, v in sorted(by_rule.items()))})",
        "Enter at the next session's open; numbers use the arm close as the reference.",
    ]
    scored = [(c, _levels(c)) for c in today]
    scored = [(c, lv) for c, lv in scored if lv is not None]
    scored.sort(key=lambda item: (item[1][0] - item[1][1]) / item[1][0] if item[1][0] else 1.0)
    for call, (entry, stop, target) in scored[:MAX_LISTED]:
        lines.append(
            f"{call.get('symbol')} [{call.get('rule')}] ref {_price(entry)} "
            f"stop {_price(stop)} target {_price(target)} exit by session "
            f"{call.get('max_hold')}"
        )
    if len(scored) > MAX_LISTED:
        lines.append(f"+{len(scored) - MAX_LISTED} more on the dashboard (tightest stops shown)")
    lines.append(RESEARCH)
    return f"{_name(market)} multi-day calls", "\n".join(lines)


def weekly(
    today: date, *, root: Path = Path("."), markets: Iterable[str] = CALLS
) -> tuple[str, str]:
    start = today - timedelta(days=6)
    lines = [f"Week {start.isoformat()} to {today.isoformat()}"]
    for market in markets:
        calls = _jsonl(root / REPORTS / CALLS[market])
        tracker = _jsonl(root / REPORTS / f"{market}_signal_tracking.jsonl")
        for label, rows in (("research calls", calls), ("tracked signals", tracker)):
            done = [
                r
                for r in rows
                if r.get("outcome")
                and (_day(r.get("resolved_at")) or start) >= start
                and (_day(r.get("resolved_at")) or today) <= today
            ]
            wins = sum(1 for r in done if r.get("label") == 1)
            r_values = [float(r["r_multiple"]) for r in done if r.get("r_multiple") is not None]
            lines.append(
                f"{_name(market)} {label}: {len(done)} resolved, targets {wins} "
                f"({_pct(wins, len(done))}), mean {_r(_mean(r_values))}"
            )
        consensus = (
            _json(root / LAB / "intraday_mover_study" / market / "latest.json")
            .get("pick_record", {})
            .get("consensus", {})
        )
        overall = consensus.get("all") or {}
        if overall.get("picks"):
            lines.append(
                f"{_name(market)} consensus picks (all time): {overall['picks']}, "
                f"winners {overall.get('winners', 0)}, beat market {overall.get('beat_market', 0)}"
            )
    health = _json(root / REPORTS / "health.json")
    failing = health.get("failing") or []
    lines.append(f"Failing tasks now: {len(failing)}")
    lines.append(RESEARCH)
    return "Weekly numbers", "\n".join(lines)
