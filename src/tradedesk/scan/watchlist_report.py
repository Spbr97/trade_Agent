"""Render a Watchlist as text (terminal / Telegram) and Markdown (PLAN.md 7 trade card)."""

from __future__ import annotations

from tradedesk.engine.scoring import Grade
from tradedesk.scan.evening_scan import Watchlist, WatchlistEntry


def trade_card(e: WatchlistEntry, capital: float) -> str:
    s = e.signal
    risk_ps = s.risk_per_share
    ep = s.exit_plan
    chased_at = s.trigger + s.chased_atr_mult * s.atr
    rr = (
        f"{e.net_rr_t1:.2f} at T1, {e.net_rr_t2:.2f} at T2"
        if e.net_rr_t1 is not None and e.net_rr_t2 is not None
        else "n/a"
    )
    events = (
        f"results in {e.results_in_sessions} sessions"
        if e.results_in_sessions is not None
        else "no results date on file"
    ) + (f" · regime {s.regime}" if s.regime else "")
    caps = f" · capped by {', '.join(e.size_caps)}" if e.size_caps else ""
    lines = [
        f"{s.symbol} · {s.setup.value.replace('_', ' ')} · LONG · {e.grade.value} ({e.score})",
        f"Trigger {s.trigger:>10.2f}  15m close above; armed {s.armed_on:%d %b}, "
        f"valid {s.valid_sessions} sessions",
        f"Stop    {s.stop:>10.2f}  {risk_ps:.2f}/share ({risk_ps / s.trigger:.1%}); "
        f"chased if open > {chased_at:.2f}",
        f"T1      {s.t1:>10.2f}  {ep.partial_at_r:.1f}R · sell {ep.partial_fraction:.0%}, "
        "stop to breakeven",
        f"T2      {s.t2:>10.2f}  {s.gross_rr_t2:.1f}R · then trail ({ep.trail})",
        f"Time    exit if not +{ep.time_stop_min_r:.0f}R by session {ep.time_stop_sessions} · "
        f"max hold {ep.max_hold_sessions}",
        f"Qty     {e.qty} -> risk Rs {e.risk_amount:,.0f} ({e.risk_pct:.2%} of Rs {capital:,.0f})"
        f" · position Rs {e.position_value:,.0f}{caps}",
        f"Costs   ~Rs {e.costs_round_trip:,.0f} round trip -> net R:R {rr}",
        f"Events  {events}",
        f"Heat    open risk {e.heat_before_pct:.1%} -> {e.heat_after_pct:.1%}",
        "Why     " + "; ".join(s.reasons),
    ]
    if e.score_notes:
        lines.append("Notes   " + "; ".join(e.score_notes))
    if e.rejected_for:
        lines.append("REJECTED " + "; ".join(e.rejected_for))
    return "\n".join(lines)


def render_text(wl: Watchlist, *, include_rejected: bool = True) -> str:
    out: list[str] = []
    r = wl.regime
    head = f"Watchlist for the session after {wl.on:%a %d %b %Y}"
    out.append(head)
    out.append("=" * len(head))
    if r is not None:
        out.append(
            f"Regime {r.regime.value.upper()} (size x{r.size_multiplier:g}): {'; '.join(r.reasons)}"
            + (f" · breadth {r.breadth_pct:.0f}%" if r.breadth_pct is not None else "")
            + (f" · VIX {r.vix:.1f}" if r.vix is not None else "")
        )
    else:
        out.append("Regime: not enough benchmark history")
    a, b, c = wl.by_grade(Grade.A), wl.by_grade(Grade.B), wl.by_grade(Grade.C)
    n_rej = len(wl.entries) - len(wl.active)
    out.append(f"{len(a)} A · {len(b)} B · {len(c)} C on the list; {n_rej} rejected")
    for grade, entries in (("A", a), ("B", b), ("C", c)):
        if not entries:
            continue
        out.append("")
        out.append(f"--- Grade {grade} ---")
        for e in entries:
            out.append("")
            out.append(trade_card(e, wl.capital))
    rejected = [e for e in wl.entries if not e.on_watchlist]
    if include_rejected and rejected:
        out.append("")
        out.append("--- Rejected ---")
        for e in rejected:
            out.append(
                f"{e.signal.symbol:<12}{e.signal.setup.value:<16}{e.grade.value} ({e.score}) "
                + "; ".join(e.rejected_for)
            )
    if wl.open_positions:
        out.append("")
        out.append("--- Open positions (as given) ---")
        for p in wl.open_positions:
            out.append(
                f"{p['scrip_code']:<12}qty {p['qty']:<6}entry {p['entry']:<10}stop {p['stop']}"
            )
    return "\n".join(out)


def render_markdown(wl: Watchlist) -> str:
    rows = [
        "| Symbol | Setup | Grade | Trigger | Stop | T1 | T2 | Qty | Risk | Net R:R T2 | Results |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in wl.active:
        s = e.signal
        rr = f"{e.net_rr_t2:.2f}" if e.net_rr_t2 is not None else "n/a"
        res = f"{e.results_in_sessions} sessions" if e.results_in_sessions is not None else "-"
        rows.append(
            f"| {s.symbol} | {s.setup.value} | {e.grade.value} ({e.score}) | {s.trigger:.2f} | "
            f"{s.stop:.2f} | {s.t1:.2f} | {s.t2:.2f} | {e.qty} | {e.risk_pct:.2%} | {rr} | {res} |"
        )
    regime = wl.regime.regime.value if wl.regime else "n/a"
    return f"## Watchlist {wl.on} · regime {regime}\n\n" + "\n".join(rows)
