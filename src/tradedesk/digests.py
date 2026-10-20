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
DATABASES = {
    "nse": "data/tradedesk.duckdb",
    "bse": "data/bse.duckdb",
    "crypto": "data/crypto.duckdb",
}
# Round trip when the multi-day study's own figure is unavailable (its latest.json carries it).
FALLBACK_ROUND_TRIP = {"nse": 0.0039098, "bse": 0.0039098, "crypto": 0.01572}
CONTROL_RULE = "random_eligible"
NOTIONAL_INR = 10_000
LINES_PER_MESSAGE = 18
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


TUNNEL_LOG = Path("data/logs/cloudflare_tunnel.log")
LINK_STATE = Path("data/reports/dashboard_link.json")


def dashboard_link(*, root: Path = Path(".")) -> tuple[str, str] | None:
    """(title, body) when the Cloudflare quick-tunnel URL differs from the last one sent,
    else None. The last link is kept in LINK_STATE, so a rotated log never loses it."""

    import re

    try:
        text = (root / TUNNEL_LOG).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    found = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
    if not found:
        return None
    url = found[-1]
    state_path = root / LINK_STATE
    if _json(state_path).get("url") == url:
        return None
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"url": url, "seen_at": datetime.now(IST).isoformat()}))
    return (
        "tradedesk dashboard link",
        f"Dashboard: {url}\n(The link changes when the tunnel restarts.)",
    )


def round_trip(market: str, *, root: Path = Path(".")) -> float:
    """Charges on a Rs 10,000 call as a fraction of it. NSE/BSE come from the production cost
    calculator at that exact size: 0.56%, STT included (the study's 0.39% left out the flat DP
    charge on each sale). Owner's rule (2026-10-21): count everything cut at the trade - all
    charges, STT, crypto's 1% TDS - and never the income tax settled at the year-end filing."""
    if market in ("nse", "bse"):
        try:
            return equity_round_trip(root)
        except Exception:  # noqa: BLE001 - fall back to the study's figure rather than fail
            pass
    study = _json(root / LAB / "multiday_hold_study" / market / "latest.json")
    try:
        return float(study["round_trip_cost"])
    except (KeyError, TypeError, ValueError):
        return FALLBACK_ROUND_TRIP[market]


def net_inr(call: Mapping[str, Any], cost: float) -> float | None:
    """Net rupees on NOTIONAL_INR placed in one resolved call: its own recorded entry and exit
    prices, minus the market's round-trip cost. None when either price is missing."""

    try:
        entry, exit_price = float(call["entry"]), float(call["exit_price"])
    except (KeyError, TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    return NOTIONAL_INR * (exit_price / entry - 1 - cost)


def equity_round_trip(root: Path = Path(".")) -> float:
    from decimal import Decimal

    from tradedesk.config import load_config
    from tradedesk.markets.costs import EquityCostModel
    from tradedesk.models import TradeType

    model = EquityCostModel(load_config(root).risk.costs)
    rt = model.round_trip_cost(
        trade_type=TradeType.DELIVERY, qty=100, entry_price=Decimal("100"),
        exit_price=Decimal("100"),
    )  # fmt: skip
    return float(rt.total) / NOTIONAL_INR


def track_record(
    market: str, rules: Iterable[str], calls: list[Mapping[str, Any]], cost: float
) -> list[str]:
    """One line per rule being called today, plus the random control: its resolved calls so
    far, average rupees per call after every charge cut at the trade (no year-end income tax)
    and how often it made money - so a call is never sent without its own live result."""

    def stats(rows: list[Mapping[str, Any]]) -> tuple[int, float, float] | None:
        values = [v for r in rows if (v := net_inr(r, cost)) is not None]
        if not values:
            return None
        return len(values), sum(values) / len(values), sum(v > 0 for v in values) / len(values)

    after = "after all charges incl. TDS" if market == "crypto" else "after all charges incl. STT"
    lines = [f"Live record so far (Rs {NOTIONAL_INR:,} a call, {after}):"]
    control = stats([c for c in calls if c.get("rule") == CONTROL_RULE])
    for rule in sorted(set(rules)):
        own = stats([c for c in calls if c.get("rule") == rule])
        if own is None:
            lines.append(f"  {rule}: no resolved calls yet")
            continue
        n, avg, won = own
        verdict = ""
        if control is not None:
            verdict = " - worse than random picks" if avg < control[1] else " - better than random"
        lines.append(f"  {rule}: {n} calls, avg {_rupees(avg)}, {won:.0%} made money{verdict}")
    if control is not None:
        n, avg, won = control
        lines.append(f"  random picks: {n} calls, avg {_rupees(avg)}, {won:.0%} made money")
    return lines


def add_sessions(start: date, sessions: int, market: str) -> date:
    """Crypto trades every day; NSE/BSE on weekdays. Exchange holidays are not known ahead
    here, so an equity date is the earliest it can be (a holiday moves it one day later)."""

    if market == "crypto":
        return start + timedelta(days=sessions)
    day = start
    while sessions > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            sessions -= 1
    return day


def _rupees(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}Rs {abs(value):,.0f}"


def _inr_line(label: str, rows: list[Mapping[str, Any]], cost: float) -> str | None:
    values = [(r, v) for r in rows if (v := net_inr(r, cost)) is not None]
    if not values:
        return None
    total = sum(v for _, v in values)
    best = max(values, key=lambda item: item[1])
    worst = min(values, key=lambda item: item[1])
    return (
        f"{label}: {len(values)} calls, net {_rupees(total)} total, "
        f"avg {_rupees(total / len(values))} per Rs {NOTIONAL_INR:,}; "
        f"best {best[0].get('symbol')} {_rupees(best[1])}, "
        f"worst {worst[0].get('symbol')} {_rupees(worst[1])}"
    )


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
    cost = round_trip(market, root=root)
    rules = [r for r in done_calls if r.get("rule") != CONTROL_RULE]
    control = [r for r in done_calls if r.get("rule") == CONTROL_RULE]
    for label, rows in (("Closed today, rules", rules), ("Closed today, random control", control)):
        line = _inr_line(label, rows, cost)
        if line:
            lines.append(line)
    lines.append(
        f"(Rs figures: Rs {NOTIONAL_INR:,} per call, after {100 * cost:.2f}% charges incl. "
        "STT / crypto TDS; year-end income tax not deducted)"
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


def _risk(call: Mapping[str, Any]) -> float | None:
    """Rupee distance from entry to stop, fixed when the call is armed (stop_atr x ATR)."""

    try:
        return float(call["stop_atr"]) * float(call["atr_at_arm"])
    except (KeyError, TypeError, ValueError):
        return None


def daily_opens(
    market: str, codes: Iterable[str], day: date, *, root: Path = Path(".")
) -> dict[str, float]:
    """Each code's daily OPEN on `day`, read-only. Empty if the store is busy or missing:
    the digest then says so instead of guessing a price."""

    wanted = sorted(set(codes))
    if not wanted:
        return {}
    try:
        import duckdb

        start = datetime.combine(day, datetime.min.time(), tzinfo=IST)
        end = start + timedelta(days=1)
        con = duckdb.connect(str(root / DATABASES[market]), read_only=True)
        try:
            rows = con.execute(
                "SELECT scrip_code, open FROM candles WHERE interval = '1day' "
                "AND ts >= ? AND ts < ? AND scrip_code IN (SELECT unnest(?))",
                [int(start.timestamp()), int(end.timestamp()), wanted],
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - a busy store must not cost the whole digest
        print(f"daily opens unavailable for {market}: {exc!r}")
        return {}
    return {str(code): float(price) for code, price in rows if price}


def _chunk(title: str, header: list[str], lines: list[str]) -> list[tuple[str, str]]:
    if not lines:
        return [(title, "\n".join([*header, RESEARCH]))]
    parts = [lines[i : i + LINES_PER_MESSAGE] for i in range(0, len(lines), LINES_PER_MESSAGE)]
    out = []
    for n, part in enumerate(parts, start=1):
        label = f"{title} ({n}/{len(parts)})" if len(parts) > 1 else title
        body = [*(header if n == 1 else []), *part, *([RESEARCH] if n == len(parts) else [])]
        out.append((label, "\n".join(body)))
    return out


def multiday_calls(
    market: str, *, root: Path = Path("."), today: date | None = None
) -> list[tuple[str, str]]:
    """Every rule call (the random control is not listed): newly armed calls with their buy
    day, stop and target distances and latest exit day, plus calls entered today with the
    actual open. One or more messages; empty when the market has no calls."""

    every = _jsonl(root / REPORTS / CALLS[market])
    calls = [c for c in every if c.get("rule") != CONTROL_RULE]
    if not calls:
        return []
    today = today or datetime.now(IST).date()
    messages: list[tuple[str, str]] = []

    armed = max(str(c.get("armed_on") or "") for c in calls)
    fresh = [c for c in calls if c.get("armed_on") == armed]
    buy_day = add_sessions(date.fromisoformat(armed), 1, market)
    by_rule = Counter(str(c.get("rule")) for c in fresh)
    header = [
        f"{_name(market)} new multi-day calls (armed {armed}): {len(fresh)} "
        f"({', '.join(f'{k} {v}' for k, v in sorted(by_rule.items()))})",
        f"Buy at the {buy_day:%a %d %b} open. Stop and target are set from that open price.",
        *track_record(market, by_rule, every, round_trip(market, root=root)),
    ]
    lines = []
    for call in sorted(fresh, key=lambda c: str(c.get("symbol"))):
        risk = _risk(call)
        if risk is None:
            continue
        exit_day = add_sessions(buy_day, int(call.get("max_hold") or 10), market)
        target = float(call.get("target_r") or 2.0) * risk
        lines.append(
            f"{call.get('symbol')} [{call.get('rule')}] last close "
            f"{_price(float(call['close_at_arm']))} | stop open-{_price(risk)} | "
            f"target open+{_price(target)} | exit by {exit_day:%a %d %b} close"
        )
    messages += _chunk(f"{_name(market)} multi-day calls", header, lines)

    entered_from = [
        c for c in calls
        if not c.get("outcome")
        and c.get("armed_on")
        and add_sessions(date.fromisoformat(str(c["armed_on"])), 1, market) == today
    ]  # fmt: skip
    if entered_from:
        opens = daily_opens(
            market, (str(c.get("scrip_code")) for c in entered_from), today, root=root
        )
        rows = []
        for call in sorted(entered_from, key=lambda c: str(c.get("symbol"))):
            risk, entry = _risk(call), opens.get(str(call.get("scrip_code")))
            if risk is None or entry is None:
                continue
            exit_day = add_sessions(today, int(call.get("max_hold") or 10), market)
            target = entry + float(call.get("target_r") or 2.0) * risk
            rows.append(
                f"{call.get('symbol')} [{call.get('rule')}] entry {_price(entry)} | "
                f"stop {_price(entry - risk)} | target {_price(target)} | "
                f"exit by {exit_day:%a %d %b} close"
            )
        missing = len(entered_from) - len(rows)
        head = [f"{_name(market)} calls entered today {today:%a %d %b} at the open: {len(rows)}"]
        if missing:
            head.append(f"{missing} more without a stored open price yet (see dashboard)")
        messages += _chunk(f"{_name(market)} entered today", head, rows)
    return messages


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
            if label == "research calls":
                cost = round_trip(market, root=root)
                for name, subset in (
                    ("rules", [r for r in done if r.get("rule") != CONTROL_RULE]),
                    ("random control", [r for r in done if r.get("rule") == CONTROL_RULE]),
                ):
                    line = _inr_line(f"{_name(market)} {name}", subset, cost)
                    if line:
                        lines.append(line)
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
    lines += self_review_lines(start, today, root=root)
    health = _json(root / REPORTS / "health.json")
    failing = health.get("failing") or []
    lines.append(f"Failing tasks now: {len(failing)}")
    lines.append(
        f"(Rs figures: Rs {NOTIONAL_INR:,} per call, after every charge cut at the trade - "
        "STT, crypto TDS; year-end income tax not deducted)"
    )
    lines.append(RESEARCH)
    return "Weekly numbers", "\n".join(lines)


def self_review_lines(start: date, end: date, *, root: Path = Path(".")) -> list[str]:
    """What the self-review loop saw this week: setups whose rolling 20-day hit rate was under
    30% on their latest check, and the review queue's decisions."""

    windows = [
        w for w in _jsonl(root / "data" / "reviews" / "rolling_windows.jsonl")
        if (d := _day(w.get("recorded_at"))) is not None and start <= d <= end
    ]  # fmt: skip
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for w in windows:
        latest[(str(w.get("market")), str(w.get("setup")))] = w
    weak = sorted(
        (k, w) for k, w in latest.items()
        if w.get("window_hit_rate") is not None and float(w["window_hit_rate"]) < 0.30
    )  # fmt: skip
    queue = _jsonl(root / "data" / "reviews" / "queue.jsonl")
    added = [q for q in queue if (d := _day(q.get("created_at"))) is not None and start <= d <= end]
    pending = [q for q in queue if q.get("status") == "pending"]
    lines = [
        f"Self-review: {len(latest)} setup checks this week, {len(weak)} under 30% hit rate; "
        f"review items added {len(added)}, waiting for you {len(pending)}"
    ]
    for (market, setup), w in weak[:8]:
        lines.append(
            f"  {market.upper()} {setup}: {100 * float(w['window_hit_rate']):.0f}% "
            f"over {w.get('window_n')} calls"
        )
    if len(weak) > 8:
        lines.append(f"  +{len(weak) - 8} more on the Review tab")
    return lines


def review_items(*, root: Path = Path(".")) -> list[tuple[str, str, str]]:
    """``(key, title, body)`` for every review-queue item still waiting for the owner. The key
    is the item id, so send_digest announces each item once however many jobs run this."""

    out = []
    for item in _jsonl(root / "data" / "reviews" / "queue.jsonl"):
        if item.get("status") != "pending" or not item.get("id"):
            continue
        body = "\n".join(
            [
                str(item.get("detail") or "")[:240],
                "Approve or reject on the dashboard: Review tab. Nothing changes until you do.",
            ]
        )
        title = f"Review waiting ({str(item.get('market') or '').upper()}): {item.get('title')}"
        out.append((f"review:{item['id']}", title[:120], body))
    return out


MOVERS_SHOWN = 10


def potential_movers(market: str, *, root: Path = Path(".")) -> tuple[str, str, str] | None:
    """``(key, title, body)`` for the newest potential-movers watch, or None when there is no
    fresh list. Research only: the watch's own forward record is shown with it."""

    report = _json(root / LAB / "potential_movers" / market / "latest.json")
    rows = report.get("candidates") or []
    if report.get("status") != "available" or report.get("data_stale") or not rows:
        return None
    lines = [
        f"{r.get('symbol')}  {r.get('tier')}  close {_price(float(r['close']))}"
        f"  5d {float(r.get('five_session_change_pct') or 0):+.1f}%"
        f"  p(up5%) {100 * float(r.get('model_probability') or 0):.0f}%"
        for r in rows[:MOVERS_SHOWN]
        if r.get("close") is not None
    ]
    forward = report.get("forward_tracking") or {}
    if forward.get("picks"):
        lines.append(
            f"Track record: {100 * float(forward.get('up5_rate') or 0):.0f}% of "
            f"{forward['picks']} past picks rose 5%+ next session (all stocks: "
            f"{100 * float(forward.get('same_day_universe_up5_rate') or 0):.0f}%); "
            f"{100 * float(forward.get('down5_rate') or 0):.0f}% fell 5%+"
        )
    if len(rows) > MOVERS_SHOWN:
        lines.append(f"+{len(rows) - MOVERS_SHOWN} more on the dashboard (Markets tab)")
    lines.append(RESEARCH)
    title = f"{_name(market)} potential movers for the next session ({report.get('as_of')})"
    return f"movers:{market}:{report.get('as_of')}", title, "\n".join(lines)
