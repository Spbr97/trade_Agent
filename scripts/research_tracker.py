"""Forward research log: call it tonight, grade it later, and measure every candidate rule
against a matched RANDOM CONTROL drawn from the same eligible universe on the same days.

Why this exists
---------------
Everything measured in this project so far has been a backtest over 2023-2026 - and every
rule that looked good was either selected on that same data (RSI(2) mean reversion) or
failed outright against a random-timing null (all three live setups, on NSE *and* BSE, and
VWAP Reclaim intraday). The eligibility gate in config/setups.yaml needs 500 trades and 100
out-of-sample before anything may alert, and nothing is currently generating that evidence:
the paper book is empty and the setups are blocked. This script is what generates it.

Three deliberate design choices, each of which is the honest one rather than the flattering
one:

1. **Forward-only. No backfill.** Every candidate below was chosen by looking at 2023-2026
   data, so replaying it over that window would reproduce a known answer, not test one. The
   log starts empty and fills one session at a time. That is slow and it is the point - the
   first genuinely out-of-sample evidence this project has ever had about these rules.

2. **A random control is logged as a peer rule, every single day.** `random_eligible` picks
   the same number of stocks from the same eligible universe on the same dates, with the
   same geometry - it just skips the trigger. Since the whole project's repeated finding is
   "the setups lose to random," a null that is measured LIVE and FORWARD alongside the
   candidates is the only comparison that can't be argued with later. It also settles a
   real open question flagged by scripts/cross_sectional_rsi2.py: the RSI(2) edge may live
   in the ELIGIBILITY FILTER (liquid, priced right, above its own ema50) rather than the
   RSI(2) trigger. If `random_eligible` scores like the candidates, that's the answer.

3. **Entry is the NEXT session's open, never the arming close.** The rule fires on a close
   you only know at the close; entering at that same close is look-ahead. Levels are
   therefore stored as an ATR multiple at arming time and resolved into real prices from
   the next bar - the same convention scripts/entry_search.py used.

Adding a new idea is meant to be cheap: append a Candidate to CANDIDATES. That is the
"try a different indicator or a different way of reading the chart" surface - the harness,
the control, the grading and the report all come for free.

Commands: `run` (nightly: resolve, then scan, then report), `report`, `scan`, `resolve`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer

from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.config import load_config
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.indicators import daily_features
from tradedesk.prediction.labeling import triple_barrier

app = typer.Typer(add_completion=False)


@app.callback()
def _cli() -> None:
    """See scripts/validate_intraday_setup.py's callback docstring."""


LOG = Path("data/reports/research_calls.jsonl")
SESSIONS = Path("data/reports/research_sessions")

# Cap per rule per session. A rule that fires on 400 stocks would otherwise drown every
# other rule in the pooled statistics; a seeded random subset of its firing set is an
# unbiased sample of it (deliberately NOT "the most extreme N" - cross_sectional_rsi2.py
# measured that ranking by signal strength adds nothing).
MAX_PER_RULE = 15

# Measured round-trip cost drag in R for a 3xATR stop at 0.25% risk-per-trade
# (CLAUDE.md 2026-09-13: 0.128R @0.25%, 0.084R @0.5%, 0.056R @1%). Applied in the report as
# a flat subtraction to show net alongside gross. It is an APPROXIMATION for reporting only
# - the real per-trade cost model is risk/costs.py and depends on actual position value.
DEFAULT_COST_R = 0.128


@dataclass(frozen=True)
class Candidate:
    """One entry idea. `fires` gets the full feature frame and returns a boolean Series
    aligned to it; only the row for the scan date is ever read, but taking the frame lets a
    rule use rolling context."""

    name: str
    why: str
    fires: Callable[[pd.DataFrame], Any]
    stop_atr: float = 3.0
    target_r: float = 2.0
    max_hold: int = 10


def _all_true(f: pd.DataFrame) -> Any:
    return pd.Series(True, index=f.index)


CANDIDATES: list[Candidate] = [
    Candidate(
        name="random_eligible",
        why=(
            "CONTROL, not an idea. Random pick from the same eligible universe, same "
            "geometry, no trigger. Every other rule is only interesting to the extent it "
            "beats this one."
        ),
        fires=_all_true,
    ),
    Candidate(
        name="rsi2_dip_ema50",
        why=(
            "Best rule ever measured here: +0.077R vs a random-timing null (t=7.04), "
            "replicated at +0.0665R on a locked test half (t=3.35). Gross edge is real; "
            "net edge did not survive costs at policy-compliant sizing."
        ),
        fires=lambda f: (f["rsi2"] < 10) & (f["close"] > f["ema50"]),
    ),
    Candidate(
        name="rsi2_dip_vol",
        why=(
            "rsi2_dip_ema50 plus volume confirmation (vol_ratio50>1.2) - the best of 10 "
            "stacked filters tried, which moved the locked-test net from -0.0109R to "
            "-0.0005R. Flat, not profitable, and t=-0.03 said don't trust even that."
        ),
        fires=lambda f: (f["rsi2"] < 10) & (f["close"] > f["ema50"]) & (f["vol_ratio50"] > 1.2),
    ),
    Candidate(
        name="rsi2_deep_ema200",
        why="Deeper dip, stronger trend filter: +0.064R vs null (t=5.28) in entry_search.",
        fires=lambda f: (f["rsi2"] < 5) & (f["close"] > f["ema200"]),
    ),
    Candidate(
        name="pullback_ema10_above_ema50",
        why=(
            "Mildest mean-reversion variant that still scored: close below its own ema10 "
            "while above ema50, +0.035R vs null (t=4.72). Fires far more often than the "
            "RSI(2) rules, so it accumulates evidence faster."
        ),
        fires=lambda f: (f["close"] < f["ema10"]) & (f["close"] > f["ema50"]),
    ),
]

BY_NAME = {c.name: c for c in CANDIDATES}


@dataclass
class ResearchCall:
    """One logged call. Plan fields are written at arming; `entry`/`stop`/`t1` and every
    outcome field stay None until the next session's open makes them real."""

    call_id: str
    rule: str
    scrip_code: str
    symbol: str
    armed_on: str
    close_at_arm: float
    atr_at_arm: float
    stop_atr: float
    target_r: float
    max_hold: int
    logged_at: str
    # filled in by resolve()
    entry: float | None = None
    stop: float | None = None
    t1: float | None = None
    entry_on: str | None = None
    outcome: str | None = None
    label: int | None = None
    exit_price: float | None = None
    r_multiple: float | None = None
    sessions_held: int | None = None
    resolved_at: str | None = None
    reasons: list[str] = field(default_factory=list)


def load_log(path: Path) -> dict[str, ResearchCall]:
    if not path.exists():
        return {}
    out: dict[str, ResearchCall] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["call_id"]] = ResearchCall(**r)
    return out


def save_log(rows: dict[str, ResearchCall], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows.values():
            fh.write(json.dumps(asdict(r)) + "\n")


def _eligible_mask(f: pd.DataFrame, min_turnover: float, min_price: float) -> Any:
    """The universe filter every rule (control included) is drawn from. Kept identical
    across rules on purpose - it is itself one of the things under test."""
    turnover20 = (f["close"] * f["volume"]).rolling(20).mean()
    return (
        (turnover20 >= min_turnover)
        & (f["close"] >= min_price)
        & f["atr14"].notna()
        & f["rsi2"].notna()
        & f["ema200"].notna()
        & (f["atr14"] > 0)
    )


@app.command()
def scan(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    on: str = typer.Option("", "--on", help="scan date (default: newest stored session)"),
    max_codes: int = typer.Option(0, "--max-codes", help="0 = all"),
    log: Path = typer.Option(LOG, "--log"),
) -> None:
    """Log every candidate's calls for one session. Safe to re-run: call ids are
    (rule, code, date), so an existing call is never duplicated or overwritten."""
    settings = load_config(root)
    min_turnover = float(settings.universe.min_avg_daily_turnover_inr)
    min_price = float(settings.universe.min_price)
    rows = load_log(log)
    want = date.fromisoformat(on) if on else None

    # Firing sets per rule, collected across the universe before any capping - the cap must
    # sample from the whole day's firing set, not from whichever codes happened to load first.
    fired: dict[str, list[tuple[str, str, float, float]]] = {c.name: [] for c in CANDIDATES}
    scan_date: date | None = want

    with CandleStore(db) as store:
        codes = store.codes(Interval.D1)
        if max_codes:
            codes = codes[:max_codes]
        typer.echo(f"scanning {len(codes)} codes")
        for n, code in enumerate(codes, 1):
            if n % 500 == 0:
                typer.echo(f"  {n}/{len(codes)}")
            raw = store.load(code, Interval.D1)
            if raw is None or len(raw) < 260:
                continue
            f = daily_features(raw)
            dates = pd.DatetimeIndex(f.index).tz_convert("Asia/Kolkata").date
            if want is None:
                pos = len(f) - 1
            else:
                hit = np.flatnonzero(dates == want)
                if not len(hit):
                    continue
                pos = int(hit[0])
            d = dates[pos]
            if scan_date is None or d > scan_date:
                scan_date = d
            elig = _eligible_mask(f, min_turnover, min_price)
            if not bool(elig.iloc[pos]):
                continue
            close = float(f["close"].iloc[pos])
            atr = float(f["atr14"].iloc[pos])
            names = [c.name for c in CANDIDATES if bool(c.fires(f).iloc[pos])]
            if not names:
                continue
            sym = store.symbol_for(code) or code  # only looked up for codes that fired
            for name in names:
                fired[name].append((code, sym, close, atr))

    if scan_date is None:
        typer.echo("no session found to scan")
        return

    new_count = 0
    for cand in CANDIDATES:
        hits = fired[cand.name]
        if not hits:
            continue
        if len(hits) > MAX_PER_RULE:
            # Seeded by (rule, date) so a re-run of the same session picks the same sample.
            rng = np.random.default_rng(abs(hash((cand.name, scan_date.isoformat()))) % (2**32))
            idx = rng.choice(len(hits), size=MAX_PER_RULE, replace=False)
            hits = [hits[i] for i in sorted(idx)]
        for code, sym, close, atr in hits:
            call_id = f"{cand.name}:{code}:{scan_date.isoformat()}"
            if call_id in rows:
                continue
            rows[call_id] = ResearchCall(
                call_id=call_id, rule=cand.name, scrip_code=code, symbol=sym,
                armed_on=scan_date.isoformat(), close_at_arm=close, atr_at_arm=atr,
                stop_atr=cand.stop_atr, target_r=cand.target_r, max_hold=cand.max_hold,
                logged_at=datetime.now(IST).isoformat(),
            )  # fmt: skip
            new_count += 1

    save_log(rows, log)
    typer.echo(f"\n{scan_date}: {new_count} new calls logged ({len(rows)} total)")
    for cand in CANDIDATES:
        typer.echo(f"  {cand.name}: {len(fired[cand.name])} fired")


@app.command()
def resolve(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    log: Path = typer.Option(LOG, "--log"),
) -> None:
    """Turn every ripe unresolved call into a real graded trade: enter at the session AFTER
    it armed (at that bar's open), stop at stop_atr x the arming ATR, target at target_r,
    then the same gap-aware triple barrier prediction/labeling.py uses everywhere."""
    rows = load_log(log)
    todo = [r for r in rows.values() if r.outcome is None]
    if not todo:
        typer.echo("nothing to resolve")
        return

    by_code: dict[str, list[ResearchCall]] = {}
    for r in todo:
        by_code.setdefault(r.scrip_code, []).append(r)

    done = 0
    with CandleStore(db) as store:
        for code, calls in by_code.items():
            raw = store.load(code, Interval.D1)
            if raw is None or raw.empty:
                continue
            dates = list(pd.DatetimeIndex(raw.index).tz_convert("Asia/Kolkata").date)
            for r in calls:
                armed = date.fromisoformat(r.armed_on)
                after = [i for i, d in enumerate(dates) if d > armed]
                if not after:
                    continue  # next session hasn't happened yet
                i0 = after[0]
                entry = float(raw["open"].iloc[i0])
                if entry <= 0 or r.atr_at_arm <= 0:
                    continue
                stop = entry - r.stop_atr * r.atr_at_arm
                if stop >= entry:
                    continue
                target = entry + r.target_r * (entry - stop)
                lab = triple_barrier(
                    raw.iloc[i0:], entry=entry, stop=stop, target=target,
                    max_hold=r.max_hold,
                )  # fmt: skip
                if lab.outcome == "insufficient":
                    continue  # not enough sessions have passed yet - retry next run
                r.entry, r.stop, r.t1 = entry, stop, target
                r.entry_on = dates[i0].isoformat()
                r.outcome, r.label = lab.outcome, lab.label
                r.exit_price = lab.exit_price
                r.sessions_held = lab.sessions
                if lab.exit_price is not None:
                    r.r_multiple = (lab.exit_price - entry) / (entry - stop)
                r.resolved_at = datetime.now(IST).isoformat()
                done += 1

    save_log(rows, log)
    typer.echo(f"resolved {done} calls ({sum(1 for r in rows.values() if r.outcome is None)} still open)")  # noqa: E501


def _stats(vals: list[float]) -> tuple[int, float, float]:
    """n, mean, t-stat against zero."""
    if not vals:
        return 0, float("nan"), float("nan")
    a = np.array(vals, dtype=float)
    se = a.std(ddof=1) / np.sqrt(len(a)) if len(a) > 1 else 0.0
    return len(a), float(a.mean()), float(a.mean() / se) if se > 0 else float("nan")


def _welch(a: list[float], b: list[float]) -> float:
    """t for (mean(a) - mean(b)), unequal variances - the candidate-vs-control test."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    x, y = np.array(a, float), np.array(b, float)
    se = np.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y))
    return float((x.mean() - y.mean()) / se) if se > 0 else float("nan")


def build_report(rows: dict[str, ResearchCall], cost_r: float) -> str:
    resolved = [r for r in rows.values() if r.outcome is not None and r.r_multiple is not None]
    open_calls = [r for r in rows.values() if r.outcome is None]
    per_rule: dict[str, list[float]] = {c.name: [] for c in CANDIDATES}
    for r in resolved:
        per_rule.setdefault(r.rule, []).append(float(r.r_multiple))  # type: ignore[arg-type]
    control = per_rule.get("random_eligible", [])

    lines = [
        f"# Research report - {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}",
        "",
        f"{len(rows)} calls logged, {len(resolved)} resolved, {len(open_calls)} still open.",
        "",
        "Nothing here is tradeable or alertable. This is a forward, out-of-sample research",
        "log; every rule is measured against `random_eligible`, a matched random pick from",
        "the same universe on the same days. A rule only means anything if it beats that.",
        "",
        "`green%` is the share of trades that ended profitable - NOT the share that reached",
        "the 2R target. At a 3xATR stop, touching 2R is a 6-ATR move inside 10 sessions and",
        "happens ~3% of the time, so these rules live or die on their timeout exits; the",
        "target hit rate is a near-constant ~0 and says nothing useful. (Same reason",
        "scripts/mr_model.py had to train on `label_profit` instead of the 2R label.)",
        "",
        f"{'rule':<28} {'n':>5} {'green%':>7} {'grossR':>8} {'netR':>8} {'t':>6} {'vs ctrl':>8} {'t':>6}",  # noqa: E501
        "-" * 88,
    ]
    for cand in CANDIDATES:
        vals = per_rule.get(cand.name, [])
        n, mean, t = _stats(vals)
        if n == 0:
            lines.append(f"{cand.name:<28} {'0':>5} {'-':>7} {'-':>8} {'-':>8} {'-':>6} {'-':>8} {'-':>6}")  # noqa: E501
            continue
        green = sum(1 for v in vals if v > 0) / n
        if cand.name == "random_eligible":
            edge_s, welch_s = "control", ""
        else:
            edge_s = f"{mean - float(np.mean(control)):+.4f}" if control else "-"
            w = _welch(vals, control) if control else float("nan")
            welch_s = f"{w:.2f}" if np.isfinite(w) else "-"
        lines.append(
            f"{cand.name:<28} {n:>5} {green:>6.1%} {mean:>+8.4f} {mean - cost_r:>+8.4f} "
            f"{t:>6.2f} {edge_s:>8} {welch_s:>6}"
        )

    lines += [
        "",
        f"netR subtracts a flat {cost_r:.3f}R measured cost drag (3xATR stop @0.25% risk).",
        "It is a reporting approximation, not the real per-trade cost model.",
        "",
        "## What would have to happen for any of these to alert",
        "config/setups.yaml's gate needs 500 trades, 100 out-of-sample, 80% win rate,",
        "positive expectancy, and +0.10R over a random baseline. On the numbers above:",
    ]
    for cand in CANDIDATES:
        if cand.name == "random_eligible":
            continue
        vals = per_rule.get(cand.name, [])
        n, mean, _ = _stats(vals)
        missing = []
        if n < 500:
            missing.append(f"{500 - n} more trades")
        if control and n and (mean - float(np.mean(control))) < 0.10:
            missing.append("+0.10R over control not met")
        if n and mean < 0:
            missing.append("expectancy negative")
        lines.append(f"  - {cand.name}: {'; '.join(missing) if missing else 'meets the measured criteria - review it'}")  # noqa: E501

    lines += ["", "## Open calls", ""]
    for r in sorted(open_calls, key=lambda r: r.armed_on, reverse=True)[:25]:
        lines.append(f"  - {r.armed_on} {r.symbol} [{r.rule}] close {r.close_at_arm:,.2f} atr {r.atr_at_arm:,.2f}")  # noqa: E501
    if not open_calls:
        lines.append("  none")
    return "\n".join(lines)


@app.command()
def report(
    log: Path = typer.Option(LOG, "--log"),
    cost_r: float = typer.Option(DEFAULT_COST_R, "--cost-r"),
    sessions: Path = typer.Option(SESSIONS, "--sessions"),
    save: bool = typer.Option(True, "--save/--no-save"),
) -> None:
    rows = load_log(log)
    text = build_report(rows, cost_r)
    typer.echo(text)
    if save:
        sessions.mkdir(parents=True, exist_ok=True)
        p = sessions / f"{date.today().isoformat()}.md"
        p.write_text(text, encoding="utf-8")
        typer.echo(f"\nsaved -> {p}")


@app.command()
def run(
    db: Path = typer.Option(Path("data/tradedesk.duckdb"), "--db"),
    root: Path = typer.Option(Path("."), "--root"),
    max_codes: int = typer.Option(0, "--max-codes"),
    log: Path = typer.Option(LOG, "--log"),
) -> None:
    """The nightly job: grade what's ripe, log tonight's calls, write the report."""
    resolve(db=db, log=log)
    scan(db=db, root=root, on="", max_codes=max_codes, log=log)
    report(log=log, cost_r=DEFAULT_COST_R, sessions=SESSIONS, save=True)


if __name__ == "__main__":
    app()
