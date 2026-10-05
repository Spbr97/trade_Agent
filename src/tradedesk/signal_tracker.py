"""Shared "log every call, grade it later via triple-barrier" tracker.

Built first for crypto (scripts/crypto_signal_tracker.py) because CoinDCX has no paper
book - paper/book.py::PaperBook stays NSE-only (CostModel/sizing wiring cost, see M13
notes). BSE needs the exact same workaround for the exact same reason: no paper book has
been built for it either. Rather than duplicate ~150 lines of near-identical dataclass/
log/resolve/render code into a second script, the generic pieces live here; each market's
script (scripts/crypto_signal_tracker.py, scripts/bse_signal_tracker.py) supplies only what
genuinely differs - which broker client to refresh candles with, and which watchlist to
scan - and calls track_and_resolve() with its own paths so the two markets' logs, reports
and dashboards never mix (same "independent datasets" reasoning as everywhere else in this
project: a crypto hit rate says nothing about a BSE one).

Also owns flag_setup_failures(): rule-based (no Claude call, no spend-cap risk) failure
detection that feeds review_queue.py - "this setup's hit rate on this market has dropped
below a floor" is a fact code can check directly, so it doesn't need an LLM to say it. This
is the "self-analyse if a call fails" surface for markets that have no paper book to run
`tradedesk review week` against.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from tradedesk.backtest.runner import BacktestConfig, MarketData
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import Settings
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.signals import Signal
from tradedesk.evidence import (
    LEGACY_TRACKER_V1,
    ContractKind,
    OutcomeState,
    classify_evidence,
    classify_outcome,
    enrich_call_record,
    infer_market,
    is_trainable_outcome,
)
from tradedesk.markets import Market
from tradedesk.prediction.labeling import triple_barrier
from tradedesk.prediction_ledger import (
    LEDGER_SCHEMA_VERSION,
    STRATEGY_VERSION,
    build_prediction_payload,
    seal_prediction,
    validate_prediction_record,
)
from tradedesk.scan.evening_scan import (
    RESEARCH_ONLY_REASON_PREFIX,
    RETIRED_REASON_PREFIX,
    Watchlist,
    build_watchlist,
)


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
    # Shadow-model probability at arming time (WatchlistEntry.probability, 2026-09-15), for the
    # per-call "what did it predict vs what happened" diagnostic (dashboard Learning > Predictions).
    # None for any row logged before this field existed, or on a day scoring was skipped/stale.
    probability: float | None = None
    # "live" | "backfill". Defaults to "backfill" so every row already logged before this
    # field existed (crypto's one-shot 2018-2026 replay, BSE's 5-large-cap history, and any
    # live-tracker row logged before 2026-09-13) loads as "backfill" on the round trip
    # through JSONL - `TrackedSignal(**r)` falls back to this default for any key the saved
    # row doesn't have. New rows from the live daily tracker set it explicitly via
    # log_new_signals(source="live"); backfill_watchlists() passes "backfill" explicitly too,
    # so this default only ever matters for data that predates the field.
    source: str = "backfill"
    # True when the setup was retired on this market by an approved self-review item: the
    # call is still logged and graded (so the loop keeps learning, and can see a recovery)
    # but it is not a call the agent stands behind.
    shadow: bool = False
    # Milestone 1 keeps recommendation authority separate from outcome lifecycle.  These
    # defaults preserve old JSONL constructors; load_log enriches historical rows as
    # ``legacy`` rather than pretending they were created under today's frozen contract.
    market: str | None = None
    evidence_class: str | None = None
    outcome_state: str | None = None
    contract_kind: str = ContractKind.LEGACY.value
    contract_version: str = "legacy-t1-tracker-v1"
    # Milestone 2 seal. Historical rows remain explicitly unsealed; they are not
    # retroactively described as immutable. New rows contain the complete prediction-time
    # payload and hashes. Outcome fields above intentionally stay outside the seal.
    ledger_schema_version: str = "legacy-unsealed-v0"
    prediction_payload: dict[str, object] | None = None
    prediction_sha256: str | None = None
    source_snapshot_sha256: str | None = None
    contract_sha256: str | None = None
    strategy_version: str | None = None
    feature_version: str | None = None
    model_version: str | None = None


def load_log(log_path: Path) -> dict[str, TrackedSignal]:
    if not log_path.exists():
        return {}
    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    market = infer_market(log_path)
    enriched = [enrich_call_record(r, market=market) for r in rows]
    for record in enriched:
        validate_prediction_record(record)
    ids = [str(record["signal_id"]) for record in enriched]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate signal_id in prediction ledger")
    return {r["signal_id"]: TrackedSignal(**r) for r in enriched}


def save_log(rows: dict[str, TrackedSignal], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    market = infer_market(log_path)
    records: list[dict[str, object]] = []
    tracked_rows = list(rows.values())
    for r in tracked_rows:
        record = enrich_call_record(asdict(r), market=market)
        validate_prediction_record(record)
        records.append(record)
    # Only mutate memory and disk after every row passed its integrity checks. A bad seal
    # must never truncate the last known-good ledger.
    for r, record in zip(tracked_rows, records, strict=True):
        r.market = record["market"]  # type: ignore[assignment]
        r.evidence_class = str(record["evidence_class"])
        r.outcome_state = str(record["outcome_state"])
        r.contract_kind = str(record["contract_kind"])
        r.contract_version = str(record["contract_version"])
    temporary = log_path.with_suffix(log_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    temporary.replace(log_path)


def setup_hit_rate(setup: str, rows: dict[str, TrackedSignal]) -> dict[str, float | int | None]:
    done = [r for r in rows.values() if is_trainable_outcome(r.outcome) and r.setup == setup]
    if not done:
        return {"n": 0, "hit_rate": None}
    wins = sum(1 for r in done if r.outcome == "target")
    return {"n": len(done), "hit_rate": wins / len(done)}


def resolve_outcomes(
    store: CandleStore, rows: dict[str, TrackedSignal], max_hold: int
) -> list[TrackedSignal]:
    """Grade every unresolved signal against real price history since it armed, using the
    exact triple-barrier rule prediction/labeling.py trains on."""
    resolved: list[TrackedSignal] = []
    for row in rows.values():
        if row.outcome is not None:
            continue
        validate_prediction_record(asdict(row))
        df = store.load(row.scrip_code, Interval.D1, adjusted=False)
        armed = date.fromisoformat(row.armed_on)
        idx_dates = pd.DatetimeIndex(df.index).date
        entry_dates = [d for d in idx_dates if d > armed]
        if not entry_dates:
            continue
        entry_date = entry_dates[0]
        start_idx = list(idx_dates).index(entry_date)
        lab = triple_barrier(
            df.iloc[start_idx:], entry=row.entry, stop=row.stop, target=row.t1,
            max_hold=max_hold,
        )  # fmt: skip
        if lab.outcome == "insufficient":
            continue  # not enough history yet - try again next run
        row.outcome = lab.outcome
        row.outcome_state = classify_outcome(row.outcome).value
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
    since a lot of signals fail that for reasons unrelated to pattern quality."""
    done = [r for r in rows.values() if is_trainable_outcome(r.outcome)]
    tradeable = [r for r in done if not r.rejected_for]
    untradeable = [r for r in done if r.rejected_for]
    return (
        f"{len(rows)} logged, {len(done)} resolved:\n"
        + _line("would have been tradeable", tradeable)
        + "\n"
        + _line("rejected (sizing/net R:R/etc)", untradeable)
    )


def fmt_price(x: float) -> str:
    """`:g` renders a BTC-scale price in scientific notation; show it plainly instead, with
    enough decimals for a sub-rupee price to stay readable too."""
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.8f}".rstrip("0").rstrip(".")


def render_session_report(
    market: str,
    day: date,
    new_rows: list[TrackedSignal],
    newly_resolved: list[TrackedSignal],
    rows: dict[str, TrackedSignal],
    *,
    run_at: str | None = None,
) -> str:
    """`run_at` (e.g. "13:02 IST") labels one check within a day for markets that run
    more than once daily (crypto: 3x/day - see scripts/crypto_signal_tracker.py). Since
    crypto's setups are daily-bar only, a later same-day run typically finds 0 new calls
    (the daily candle hasn't advanced) - that's expected, not a bug, and worth saying
    plainly rather than leaving the reader to wonder why nothing new showed up."""
    header = f"# {market} session report - {day.isoformat()}"
    if run_at:
        header += f" (run at {run_at})"
    lines = [header, "", f"New calls this run: {len(new_rows)}"]
    if not new_rows and run_at:
        lines.append("  (expected on a later same-day run - the daily candle hasn't advanced yet)")  # noqa: E501
    for r in new_rows:
        tradeable = "tradeable" if not r.rejected_for else f"rejected ({', '.join(r.rejected_for)})"
        lines.append(
            f"  - {r.symbol} {r.setup} grade {r.grade}: entry {fmt_price(r.entry)} "
            f"stop {fmt_price(r.stop)} t1 {fmt_price(r.t1)} t2 {fmt_price(r.t2)} [{tradeable}]"
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


def save_session_report(day: date, text: str, sessions_dir: Path, *, append: bool = False) -> Path:
    """`append=True` (crypto's multiple-runs-per-day case) adds this run's report to the
    day's existing file instead of overwriting it, so a day with 3 checks shows all 3 in
    one place rather than the earlier runs vanishing when the last one overwrites them."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{day.isoformat()}.md"
    if append and path.exists():
        path.write_text(path.read_text(encoding="utf-8") + "\n---\n\n" + text, encoding="utf-8")
    else:
        path.write_text(text, encoding="utf-8")
    return path


def render_dashboard_html(market: str, rows: dict[str, TrackedSignal]) -> str:
    ordered = sorted(rows.values(), key=lambda r: r.armed_on, reverse=True)
    pending = [r for r in ordered if r.outcome_state == OutcomeState.PENDING_CALL.value]
    resolved = [r for r in ordered if r.outcome_state == OutcomeState.RESOLVED_CALL.value]
    excluded = [
        r
        for r in ordered
        if r.outcome_state
        in {OutcomeState.INVALID_CALL.value, OutcomeState.NEVER_TRIGGERED.value}
    ]

    def row_html(r: TrackedSignal, show_outcome: bool) -> str:
        tradeable = "yes" if not r.rejected_for else "no"
        outcome_cell = ""
        if show_outcome:
            cls = "win" if r.label == 1 else "loss"
            r_mult = f"{r.r_multiple:+.2f}R" if r.r_multiple is not None else "-"
            outcome_cell = f'<td class="{cls}">{r.outcome} ({r_mult})</td>'
        return (
            "<tr>"
            f"<td>{r.armed_on}</td><td>{r.symbol}</td>"
            f"<td>{r.setup}{' (shadow)' if r.shadow else ''}</td><td>{r.grade}</td>"
            f"<td>{fmt_price(r.entry)}</td><td>{fmt_price(r.stop)}</td>"
            f"<td>{fmt_price(r.t1)}</td><td>{fmt_price(r.t2)}</td>"
            f"<td>{r.evidence_class or '-'}</td><td>{r.contract_kind}</td>"
            f"<td>{'sealed' if r.prediction_sha256 else 'legacy'}</td>"
            f"<td>{tradeable}</td>" + outcome_cell + "</tr>"
        )

    pending_rows = "\n".join(row_html(r, show_outcome=False) for r in pending) or (
        '<tr><td colspan="12">none open</td></tr>'
    )
    resolved_rows = "\n".join(row_html(r, show_outcome=True) for r in resolved) or (
        '<tr><td colspan="13">none resolved yet</td></tr>'
    )
    excluded_rows = "\n".join(row_html(r, show_outcome=True) for r in excluded) or (
        '<tr><td colspan="13">none invalid or never triggered</td></tr>'
    )
    score_text = scoreboard(rows).replace("\n", "<br>")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{market} signal tracker</title>
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
<h1>{market} signal tracker (paper calls only - nothing is ever placed)</h1>
<p class="updated">Last updated: {datetime.now(IST).isoformat(timespec="seconds")}</p>
<div class="score">{score_text}</div>

<h2>Open calls (not yet resolved)</h2>
<table><tr><th>Armed</th><th>Symbol</th><th>Setup</th><th>Grade</th><th>Entry</th>
<th>Stop</th><th>T1</th><th>T2</th><th>Evidence</th><th>Contract</th><th>Ledger</th><th>Tradeable</th></tr>
{pending_rows}
</table>

<h2>Resolved calls</h2>
<table><tr><th>Armed</th><th>Symbol</th><th>Setup</th><th>Grade</th><th>Entry</th>
<th>Stop</th><th>T1</th><th>T2</th><th>Evidence</th><th>Contract</th><th>Ledger</th><th>Tradeable</th><th>Outcome</th></tr>
{resolved_rows}
</table>

<h2>Invalid / never-triggered calls (visible, excluded from learning)</h2>
<table><tr><th>Armed</th><th>Symbol</th><th>Setup</th><th>Grade</th><th>Entry</th>
<th>Stop</th><th>T1</th><th>T2</th><th>Evidence</th><th>Contract</th><th>Ledger</th><th>Tradeable</th><th>Outcome</th></tr>
{excluded_rows}
</table>
</body></html>
"""


def save_dashboard(market: str, rows: dict[str, TrackedSignal], dashboard_path: Path) -> Path:
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(render_dashboard_html(market, rows), encoding="utf-8")
    return dashboard_path


def log_new_signals(
    wl: Watchlist,
    rows: dict[str, TrackedSignal],
    *,
    source: str = "live",
    market: str | None = None,
) -> list[TrackedSignal]:
    """Every detected signal (`wl.entries`), not just `wl.active` - a rejected-for-sizing
    signal still answers "was the pattern right", which is a different question from "was
    it tradeable". See TrackedSignal.rejected_for and scoreboard().

    `source` defaults to "live" (the normal daily tracker's call), so scripts/crypto_signal_
    tracker.py and scripts/bse_signal_tracker.py need no change; backfill_watchlists() below
    passes "backfill" explicitly - see TrackedSignal.source for why the distinction exists."""
    new_rows: list[TrackedSignal] = []
    for e in wl.entries:
        sig: Signal = e.signal
        if sig.id in rows:
            validate_prediction_record(asdict(rows[sig.id]))
            continue
        effective_market = market or {
            "NSE": "nse",
            "BSE": "bse",
            "CDX": "crypto",
        }.get(sig.scrip_code.split("_", 1)[0])
        if effective_market is None:
            raise ValueError(f"market required for immutable prediction: {sig.scrip_code}")
        shadow = any(
            reason.startswith((RETIRED_REASON_PREFIX, RESEARCH_ONLY_REASON_PREFIX))
            for reason in e.rejected_for
        )
        evidence_class = classify_evidence(e.rejected_for, shadow=shadow).value
        payload = build_prediction_payload(
            watchlist=wl,
            entry=e,
            market=effective_market,
            source=source,
            evidence_class=evidence_class,
            contract_kind=ContractKind.LEGACY.value,
            contract_version=LEGACY_TRACKER_V1.version,
        )
        row = TrackedSignal(
            signal_id=sig.id, scrip_code=sig.scrip_code, symbol=sig.symbol,
            setup=sig.setup.value, grade=e.grade.value, armed_on=sig.armed_on.isoformat(),
            entry=sig.trigger, stop=sig.stop, t1=sig.t1, t2=sig.t2,
            net_rr_t1=e.net_rr_t1, net_rr_t2=e.net_rr_t2, rejected_for=list(e.rejected_for),
            probability=e.probability, logged_at=datetime.now(IST).isoformat(), source=source,
            shadow=shadow, market=effective_market,
            evidence_class=evidence_class,
            outcome_state=OutcomeState.PENDING_CALL.value,
            # Milestone 1 defines the future quick/swing contracts but does not claim
            # the legacy resolver enforces them. Activation belongs to Milestone 3.
            contract_kind=ContractKind.LEGACY.value,
            contract_version=LEGACY_TRACKER_V1.version,
            ledger_schema_version=LEDGER_SCHEMA_VERSION,
            prediction_payload=payload,
            prediction_sha256=seal_prediction(payload),
            source_snapshot_sha256=str(payload["source_snapshot_sha256"]),
            contract_sha256=str(payload["contract"]["sha256"]),  # type: ignore[index]
            strategy_version=STRATEGY_VERSION,
            feature_version=str(payload["versions"]["feature_contract"]),  # type: ignore[index]
            model_version=e.model_version,
        )  # fmt: skip
        validate_prediction_record(asdict(row))
        rows[sig.id] = row
        new_rows.append(row)
    return new_rows


def backfill_watchlists(
    md: MarketData,
    cfg: BacktestConfig,
    settings: Settings,
    mkt: Market,
    rows: dict[str, TrackedSignal],
) -> list[TrackedSignal]:
    """Backfill a real historical track record from data already on disk, instead of
    starting a market's log at zero and waiting weeks of daily runs to accumulate one
    (crypto already had years of history loaded; BSE just needs its watchlist's history
    loaded first - see scripts/backfill_signal_tracker.py). Replays the exact same
    build_watchlist step the daily job runs, once per session in `cfg.start..cfg.end`,
    against ONE `prepare_market` call (cheap: the features are computed once, not per day).

    Only NEW signal ids get logged - existing rows are left untouched - so running this
    after the daily incremental job has already logged today's calls is safe and won't
    double-count or overwrite anything."""
    sessions = [d for d in md.calendar if cfg.start <= d <= cfg.end]
    new_rows: list[TrackedSignal] = []
    for day in sessions:
        wl = build_watchlist(md, cfg, settings, day, market=mkt)
        new_rows.extend(log_new_signals(wl, rows, source="backfill", market=mkt.name))
    return new_rows


def flag_setup_failures(
    market: str,
    rows: dict[str, TrackedSignal],
    *,
    min_resolved: int = 5,
    hit_rate_floor: float = 0.3,
    review_path: Path | None = None,
) -> list[str]:
    """Rule-based failure analysis (no Claude call - a hit rate crossing a floor is a fact
    code can check directly): for each setup with at least `min_resolved` resolved calls on
    this market, flag it into review_queue.py if its hit rate has dropped below the floor.
    One item per (market, setup) - re-running this daily does not re-flag a setup that
    already has an open or decided item, so the queue doesn't fill with duplicates.

    This is the "self-analyse if a call fails" surface for crypto and BSE, which have no
    paper book to run `tradedesk review week`'s Claude-based analysis against.

    `review_path` overrides review_queue.QUEUE's default - tests must pass a tmp_path here
    (the same lesson learned from claude/weekly_review.py writing into the production
    queue during a pytest run before this parameter existed)."""
    from tradedesk.review_queue import QUEUE, add_item, load_queue

    path = review_path or QUEUE
    existing_titles = {i.title for i in load_queue(path).values()}
    by_setup: dict[str, list[TrackedSignal]] = {}
    for r in rows.values():
        if is_trainable_outcome(r.outcome):
            by_setup.setdefault(r.setup, []).append(r)
    flagged: list[str] = []
    for setup, done in by_setup.items():
        if len(done) < min_resolved:
            continue
        wins = sum(1 for r in done if r.outcome == "target")
        rate = wins / len(done)
        if rate >= hit_rate_floor:
            continue
        title = f"{setup} underperforming on {market}"
        if title in existing_titles:
            continue
        add_item(
            market=market,
            title=title,
            detail=(
                f"{wins}/{len(done)} resolved calls hit target ({rate:.0%}), below the "
                f"{hit_rate_floor:.0%} floor checked over the last {len(done)} resolved calls."
            ),
            proposal=f"Consider disabling or re-tuning {setup} for {market} until it recovers.",
            path=path,
        )
        flagged.append(title)
    return flagged
