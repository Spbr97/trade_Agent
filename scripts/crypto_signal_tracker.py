"""Crypto signal tracker (M13 follow-up): "test a few coins and track to see if you're
right" - crypto trades 24/7, but paper/book.py::PaperBook is deliberately NSE-only
(CLAUDE.md M13 note: Portfolio.costs would need CryptoCostModel wiring through the paper
book's sizing too, and risk/sizing.py's whole-unit assumption already breaks on BTC/ETH -
see docs/signoff-crypto-phase4.md). Rather than force crypto through that, this is a
lighter, honest tool: log every real signal the crypto scan produces on a fixed watchlist
of liquid pairs, then later grade each one with the SAME triple-barrier rule the
prediction layer trains on (prediction/labeling.py) - hit target before stop, or not,
gap-aware. It answers "was the setup right" without needing position sizing, a cost
model wired into a portfolio, or fractional quantities at all.

Deliberately logs EVERY signal the engine detects (`wl.entries`), not just
`wl.active`: a first real run showed every BTC/ETH/BNB signal rejected purely on "size
0" (NSE's reused Rs 250/trade risk budget can't afford qty=1 at BTC's price - see
docs/signoff-crypto-phase4.md, a money/sizing question, not a "was the pattern right"
question). `rejected_for` is kept on each row so tradeability is still visible
separately from whether the setup itself called the move correctly.

Run daily (scheduled via Task Scheduler, crypto_daily task) - crypto's daily candle is a
UTC-midnight bar, settled well before this runs at 07:00 IST.

Log: data/reports/crypto_signal_tracking.jsonl, one row per signal, updated in place as
outcomes resolve (rewrite-the-file style, small enough not to need anything fancier).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradedesk.backtest.runner import prepare_market
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.data.history_loader import default_start, load_history
from tradedesk.engine.signals import Signal
from tradedesk.markets import crypto_market
from tradedesk.prediction.labeling import triple_barrier
from tradedesk.scan import build_watchlist, scan_config

DB = Path("data/crypto.duckdb")
LOG = Path("data/reports/crypto_signal_tracking.jsonl")
SESSIONS_DIR = Path("data/reports/crypto_sessions")
DASHBOARD = Path("data/reports/crypto_dashboard.html")
WATCHLIST = [
    "CDX_BTCINR", "CDX_ETHINR", "CDX_SOLINR", "CDX_XRPINR", "CDX_DOGEINR",
    "CDX_ADAINR", "CDX_TRXINR", "CDX_XLMINR", "CDX_HBARINR", "CDX_BNBINR",
]  # fmt: skip
MAX_HOLD = 10  # sessions -> calendar days for a 24/7 market; matches config/risk.yaml


@dataclass
class TrackedSignal:
    signal_id: str
    scrip_code: str
    symbol: str
    setup: str
    grade: str
    armed_on: str
    entry: float
    stop: float
    t1: float
    t2: float
    net_rr_t1: float | None
    net_rr_t2: float | None
    rejected_for: list[str]
    logged_at: str
    outcome: str | None = None  # None until resolved: "target" | "stop" | "gap_stop" | "timeout"
    label: int | None = None
    exit_price: float | None = None
    r_multiple: float | None = None
    resolved_at: str | None = None


def load_log() -> dict[str, TrackedSignal]:
    if not LOG.exists():
        return {}
    rows = [
        json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {r["signal_id"]: TrackedSignal(**r) for r in rows}


def save_log(rows: dict[str, TrackedSignal]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as fh:
        for r in rows.values():
            fh.write(json.dumps(asdict(r)) + "\n")


def setup_hit_rate(setup: str, rows: dict[str, TrackedSignal] | None = None) -> dict[str, float | int | None]:  # noqa: E501
    """Historical "hit T1 before the stop" rate for one setup, from crypto's own resolved
    log - kept separate from NSE's paper-book win rate (journal.stats.track_record) since
    the two datasets are deliberately independent (different market, different costs)."""
    rows = rows if rows is not None else load_log()
    done = [r for r in rows.values() if r.outcome is not None and r.setup == setup]
    if not done:
        return {"n": 0, "hit_rate": None}
    wins = sum(1 for r in done if r.outcome == "target")
    return {"n": len(done), "hit_rate": wins / len(done)}


def resolve_outcomes(store: CandleStore, rows: dict[str, TrackedSignal]) -> list[TrackedSignal]:
    """Grade every unresolved signal against real price history since it armed, using the
    exact triple-barrier rule prediction/labeling.py trains on."""
    resolved: list[TrackedSignal] = []
    for row in rows.values():
        if row.outcome is not None:
            continue
        df = store.load(row.scrip_code, Interval.D1, adjusted=False)
        armed = date.fromisoformat(row.armed_on)
        entry_dates = [d for d in df.index.date if d > armed]
        if not entry_dates:
            continue
        entry_date = entry_dates[0]
        start_idx = list(df.index.date).index(entry_date)
        lab = triple_barrier(
            df.iloc[start_idx:], entry=row.entry, stop=row.stop, target=row.t1,
            max_hold=MAX_HOLD,
        )  # fmt: skip
        if lab.outcome == "insufficient":
            continue  # not enough history yet - try again tomorrow
        row.outcome = lab.outcome
        row.label = lab.label
        row.exit_price = lab.exit_price
        if lab.exit_price is not None:
            row.r_multiple = (lab.exit_price - row.entry) / (row.entry - row.stop)
        row.resolved_at = datetime.now(IST).isoformat()
        resolved.append(row)
    return resolved


def _line(label: str, done: list[TrackedSignal]) -> str:
    if not done:
        return f"  {label}: 0 resolved yet"
    wins = sum(1 for r in done if r.label == 1)
    avg_r = sum(r.r_multiple for r in done if r.r_multiple is not None) / len(done)
    return f"  {label}: {wins}/{len(done)} hit T1 ({wins / len(done):.0%}), avg R {avg_r:+.2f}"


def scoreboard(rows: dict[str, TrackedSignal]) -> str:
    """Split by whether the signal would actually have been tradeable (net R:R/sizing),
    since most crypto signals fail that for reasons unrelated to pattern quality - see
    the module docstring."""
    done = [r for r in rows.values() if r.outcome is not None]
    tradeable = [r for r in done if not r.rejected_for]
    untradeable = [r for r in done if r.rejected_for]
    return (
        f"{len(rows)} logged, {len(done)} resolved:\n"
        + _line("would have been tradeable", tradeable)
        + "\n"
        + _line("rejected (sizing/net R:R/etc)", untradeable)
    )


def _fmt_price(x: float) -> str:
    """`:g` renders BTC's ~77 lakh price in scientific notation; show it plainly instead,
    with enough decimals for a sub-rupee meme-coin price to stay readable too."""
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.8f}".rstrip("0").rstrip(".")


def render_session_report(
    day: date,
    new_rows: list[TrackedSignal],
    newly_resolved: list[TrackedSignal],
    rows: dict[str, TrackedSignal],
) -> str:
    """One "session" = one crypto daily bar (the setups are daily-bar only - see the module
    docstring). Written every run so there is a plain-text trail of what was called and
    whether it turned out right, separate from the JSONL the code reads back."""
    lines = [f"# Crypto session report - {day.isoformat()}", ""]
    lines.append(f"New calls today: {len(new_rows)}")
    for r in new_rows:
        tradeable = "tradeable" if not r.rejected_for else f"rejected ({', '.join(r.rejected_for)})"
        lines.append(
            f"  - {r.symbol} {r.setup} grade {r.grade}: entry {_fmt_price(r.entry)} "
            f"stop {_fmt_price(r.stop)} t1 {_fmt_price(r.t1)} t2 {_fmt_price(r.t2)} [{tradeable}]"
        )
    lines.append("")
    lines.append(f"Calls resolved today: {len(newly_resolved)}")
    for r in newly_resolved:
        verdict = "RIGHT" if r.label == 1 else "WRONG"
        lines.append(
            f"  - {r.symbol} {r.setup} armed {r.armed_on}: {verdict} ({r.outcome}, "
            f"{r.r_multiple:+.2f}R)"
        )
    lines.append("")
    lines.append("Running scoreboard:")
    lines.append(scoreboard(rows))
    return "\n".join(lines)


def save_session_report(day: date, text: str) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{day.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    return path


def render_dashboard_html(rows: dict[str, TrackedSignal]) -> str:
    """Static, self-contained HTML - no server, no build step. Regenerated on every run
    (Task Scheduler, daily 07:00 IST) so opening the file locally always shows the latest
    calls and their outcomes. Deliberately plain: this is a monitoring tool, not a product."""
    ordered = sorted(rows.values(), key=lambda r: r.armed_on, reverse=True)
    pending = [r for r in ordered if r.outcome is None]
    resolved = [r for r in ordered if r.outcome is not None]

    def row_html(r: TrackedSignal, show_outcome: bool) -> str:
        tradeable = "yes" if not r.rejected_for else "no"
        outcome_cell = ""
        if show_outcome:
            cls = "win" if r.label == 1 else "loss"
            r_mult = f"{r.r_multiple:+.2f}R" if r.r_multiple is not None else "-"
            outcome_cell = f'<td class="{cls}">{r.outcome} ({r_mult})</td>'
        return (
            "<tr>"
            f"<td>{r.armed_on}</td><td>{r.symbol}</td><td>{r.setup}</td><td>{r.grade}</td>"
            f"<td>{_fmt_price(r.entry)}</td><td>{_fmt_price(r.stop)}</td>"
            f"<td>{_fmt_price(r.t1)}</td><td>{_fmt_price(r.t2)}</td>"
            f"<td>{tradeable}</td>" + outcome_cell + "</tr>"
        )

    pending_rows = "\n".join(row_html(r, show_outcome=False) for r in pending) or (
        '<tr><td colspan="9">none open</td></tr>'
    )
    resolved_rows = "\n".join(row_html(r, show_outcome=True) for r in resolved) or (
        '<tr><td colspan="10">none resolved yet</td></tr>'
    )
    score_text = scoreboard(rows).replace("\n", "<br>")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Crypto signal tracker</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; background: #fafafa; }}
h1 {{ font-size: 1.3rem; }}
h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 0.5rem; font-size: 0.85rem; }}
th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right; }}
th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) {{ text-align: left; }}
th {{ background: #eee; }}
.win {{ color: #146c2e; font-weight: 600; }}
.loss {{ color: #b3261e; font-weight: 600; }}
.score {{ background: #fff; border: 1px solid #ddd; padding: 0.75rem 1rem; font-size: 0.9rem; }}
.updated {{ color: #666; font-size: 0.8rem; }}
</style></head>
<body>
<h1>Crypto signal tracker (paper calls only - nothing is ever placed)</h1>
<p class="updated">Last updated: {datetime.now(IST).isoformat(timespec="seconds")}</p>
<div class="score">{score_text}</div>

<h2>Open calls (not yet resolved)</h2>
<table><tr><th>Armed</th><th>Coin</th><th>Setup</th><th>Grade</th><th>Entry</th>
<th>Stop</th><th>T1</th><th>T2</th><th>Tradeable</th></tr>
{pending_rows}
</table>

<h2>Resolved calls</h2>
<table><tr><th>Armed</th><th>Coin</th><th>Setup</th><th>Grade</th><th>Entry</th>
<th>Stop</th><th>T1</th><th>T2</th><th>Tradeable</th><th>Outcome</th></tr>
{resolved_rows}
</table>
</body></html>
"""


def save_dashboard(rows: dict[str, TrackedSignal]) -> Path:
    DASHBOARD.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD.write_text(render_dashboard_html(rows), encoding="utf-8")
    return DASHBOARD


def main() -> None:
    settings = load_config(".")
    market = crypto_market(settings)
    with CandleStore(DB) as store:
        # incremental load, watchlist coins only - keeps this fast and independent of
        # the full 338-pair Task Scheduler load
        import asyncio

        from tradedesk.broker.coindcx import CoinDcxClient
        from tradedesk.broker.coindcx.rest import CoinDcxError

        start = default_start(Interval.D1, datetime.now(IST))

        async def refresh() -> None:
            async with CoinDcxClient() as c:
                c.register_pairs(store.custom_symbols(WATCHLIST))
                await load_history(
                    c, store, WATCHLIST, Interval.D1, start=start,
                    max_codes_per_call=1, error_types=(CoinDcxError,),
                )  # fmt: skip

        asyncio.run(refresh())

        today = store.last_ts(WATCHLIST[0], Interval.D1)
        if today is None:
            print("no data for the watchlist; aborting")
            return
        day = today.date()

        cfg = scan_config(settings, day, market=market)
        md = prepare_market(store, WATCHLIST, WATCHLIST[0], cfg)
        wl = build_watchlist(md, cfg, settings, day, market=market)

        rows = load_log()
        new_rows: list[TrackedSignal] = []
        for e in wl.entries:  # every detected signal, not just wl.active - see module docstring
            sig: Signal = e.signal
            if sig.id in rows:
                continue
            row = TrackedSignal(
                signal_id=sig.id, scrip_code=sig.scrip_code, symbol=sig.symbol,
                setup=sig.setup.value, grade=e.grade.value, armed_on=sig.armed_on.isoformat(),
                entry=sig.trigger, stop=sig.stop, t1=sig.t1, t2=sig.t2,
                net_rr_t1=e.net_rr_t1, net_rr_t2=e.net_rr_t2, rejected_for=list(e.rejected_for),
                logged_at=datetime.now(IST).isoformat(),
            )  # fmt: skip
            rows[sig.id] = row
            new_rows.append(row)

        newly_resolved = resolve_outcomes(store, rows)
        save_log(rows)
        save_dashboard(rows)
        report = render_session_report(day, new_rows, newly_resolved, rows)
        report_path = save_session_report(day, report)
        print(
            f"{day}: {len(wl.entries)} signals detected "
            f"({len(wl.active)} would be tradeable, {len(new_rows)} new today), "
            f"{len(newly_resolved)} newly resolved"
        )
        print(scoreboard(rows))
        print(f"session report: {report_path}")
        print(f"dashboard: {DASHBOARD}")


if __name__ == "__main__":
    main()
