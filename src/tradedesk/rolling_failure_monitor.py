"""Continuous, rolling-window failure detection (2026-09-27) - the "is this setup STILL
failing, day after day" surface the self-review loop's retire/rewrite trigger needs.

This is deliberately separate from, and does not replace, `signal_tracker.flag_setup_failures`:
that function checks a setup's LIFETIME-cumulative hit rate once, with a permanent one-shot
dedupe-by-title (a rejected item silences that title forever). It is a fine "something looks
off, a human should glance at this" signal, but it cannot express "this has been bad for
weeks straight" versus "this had one bad patch and recovered" - and the user was explicit that
retiring or rewriting a setup must only follow SUSTAINED, CONTINUOUS failure, checked for each
trade and each day, not a single flag.

Scope: reads the exact same per-market TrackedSignal logs `signal_tracker.py` already owns -
every signal a setup produced and resolved, tradeable or not (see that module's own
`rejected_for` field). A setup's daily predictions that never became a live/tradeable call are
just as much "a prediction that failed" as one that would have alerted, and both count here -
narrowing this to only tradeable-eligible signals would silently exclude most of the evidence.

Design: every day, for each (market, setup), compute hit rate over a trailing calendar window
(`window_days`) among resolved calls in that window (both conditions required: enough calendar
history AND enough resolved trades, `min_trades_in_window`). Append ONE observation row per
(market, setup, as_of_date) to an append-only log - re-derived fresh from the TrackedSignal
log each run rather than carrying persisted streak state, so a corrected/rolled-back log
self-heals instead of carrying a stale streak forward. "Sustained, continuous" failure =
`consecutive_windows_required` of the MOST RECENT daily observations for that (market, setup)
all independently below `hit_rate_floor` - not one snapshot.

Two severities, both computed from the same rolling window, feeding different proposal paths
in `self_review/`:
- SUSTAINED (retire/rewrite trigger): consecutive_windows_required daily observations in a
  row, all below the floor.
- SINGLE (lighter, config-tuning trigger): the single latest observation below the floor,
  regardless of history - may fire sooner, proposes a smaller/less disruptive fix.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from tradedesk.broker.indstocks.models import IST
from tradedesk.signal_tracker import TrackedSignal, load_log

WINDOWS_LOG = Path("data/reviews/rolling_windows.jsonl")

LOG_PATHS: dict[str, Path] = {
    "nse": Path("data/reports/nse_signal_tracking.jsonl"),
    "bse": Path("data/reports/bse_signal_tracking.jsonl"),
    "crypto": Path("data/reports/crypto_signal_tracking.jsonl"),
}

DEFAULT_WINDOW_DAYS = 20
DEFAULT_MIN_TRADES_IN_WINDOW = 15
DEFAULT_HIT_RATE_FLOOR = 0.3
DEFAULT_CONSECUTIVE_WINDOWS_REQUIRED = 3


class FailureSeverity(StrEnum):
    SUSTAINED = "sustained"  # consecutive_windows_required windows in a row below floor
    SINGLE = "single"  # only the latest window is below floor


@dataclass(frozen=True)
class RollingWindowObservation:
    market: str
    setup: str
    as_of_date: str  # ISO date
    window_days: int
    window_n: int
    window_hit_rate: float
    recorded_at: str


@dataclass(frozen=True)
class SustainedFailure:
    market: str
    setup: str
    severity: FailureSeverity
    as_of_date: str
    window_hit_rate: float
    window_n: int
    consecutive_windows_failed: int
    detail: str


def _resolved_date(row: TrackedSignal) -> date | None:
    if row.outcome is None or row.resolved_at is None:
        return None
    return datetime.fromisoformat(row.resolved_at).date()


def compute_window(
    rows: list[TrackedSignal],
    *,
    as_of: date,
    window_days: int,
) -> tuple[int, float] | None:
    """Returns (n, hit_rate) among resolved calls in [as_of - window_days, as_of], or None if
    there is nothing resolved in the window at all (distinct from "0 wins", which IS a real,
    countable observation)."""

    start = as_of - timedelta(days=window_days)
    in_window = []
    for row in rows:
        resolved = _resolved_date(row)
        if resolved is not None and start <= resolved <= as_of:
            in_window.append(row)
    if not in_window:
        return None
    wins = sum(1 for r in in_window if r.outcome == "target")
    return len(in_window), wins / len(in_window)


def append_observations(
    market: str,
    rows: dict[str, TrackedSignal],
    *,
    as_of: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_trades_in_window: int = DEFAULT_MIN_TRADES_IN_WINDOW,
    windows_log: Path = WINDOWS_LOG,
) -> list[RollingWindowObservation]:
    """Computes today's rolling-window observation for every setup with resolved history on
    this market and appends it to `windows_log` - one row per (market, setup, as_of_date),
    regardless of whether it clears `min_trades_in_window` (a below-floor-on-too-few-trades
    day is recorded as `window_n` under the floor, not silently skipped, so the log is a
    complete daily record a human can audit).

    Idempotent per (market, setup, as_of_date): skips a setup that already has a row for this
    exact date rather than appending a second one. Without this, calling `run_daily_check`
    more than once on the same day (e.g. `rolling-check` followed by `self-review-run`, or a
    retried cron job) would write duplicate same-day rows, and `find_sustained_failures`'s
    "most recent N observations" logic would then count those duplicates as N distinct days -
    manufacturing a false SUSTAINED (retire-trigger) verdict out of one real day's data. Found
    for real 2026-09-27: two same-day runs while activating this for crypto produced exactly
    that false SUSTAINED classification for all three setups."""

    as_of = as_of or datetime.now(IST).date()
    already_recorded = {
        (o.setup, o.as_of_date)
        for o in load_observations(windows_log)
        if o.market == market
    }
    by_setup: dict[str, list[TrackedSignal]] = defaultdict(list)
    for row in rows.values():
        by_setup[row.setup].append(row)

    recorded_at = datetime.now(IST).isoformat()
    observations: list[RollingWindowObservation] = []
    for setup, setup_rows in sorted(by_setup.items()):
        if (setup, as_of.isoformat()) in already_recorded:
            continue
        window = compute_window(setup_rows, as_of=as_of, window_days=window_days)
        if window is None:
            continue
        n, hit_rate = window
        observations.append(
            RollingWindowObservation(
                market=market,
                setup=setup,
                as_of_date=as_of.isoformat(),
                window_days=window_days,
                window_n=n,
                window_hit_rate=hit_rate,
                recorded_at=recorded_at,
            )
        )

    if observations:
        windows_log.parent.mkdir(parents=True, exist_ok=True)
        with windows_log.open("a", encoding="utf-8") as fh:
            for obs in observations:
                fh.write(json.dumps(asdict(obs)) + "\n")
    return [o for o in observations if o.window_n >= min_trades_in_window]


def load_observations(windows_log: Path = WINDOWS_LOG) -> list[RollingWindowObservation]:
    if not windows_log.exists():
        return []
    rows = []
    for line in windows_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(RollingWindowObservation(**json.loads(line)))
    return rows


def find_sustained_failures(
    market: str,
    observations: list[RollingWindowObservation],
    *,
    hit_rate_floor: float = DEFAULT_HIT_RATE_FLOOR,
    consecutive_windows_required: int = DEFAULT_CONSECUTIVE_WINDOWS_REQUIRED,
    min_trades_in_window: int = DEFAULT_MIN_TRADES_IN_WINDOW,
) -> list[SustainedFailure]:
    """Reads the full observation history (already-appended by `append_observations`) and
    decides severity per setup, most-recent-first: SUSTAINED if the last
    `consecutive_windows_required` qualifying (n >= min_trades_in_window) observations are
    ALL below the floor; SINGLE if only the single latest qualifying observation is below the
    floor. Neither fires if the latest qualifying observation is at or above the floor."""

    by_setup: dict[str, list[RollingWindowObservation]] = defaultdict(list)
    for obs in observations:
        if obs.market == market and obs.window_n >= min_trades_in_window:
            by_setup[obs.setup].append(obs)

    results: list[SustainedFailure] = []
    for setup, obs_list in sorted(by_setup.items()):
        ordered = sorted(obs_list, key=lambda o: o.as_of_date)
        if not ordered:
            continue
        latest = ordered[-1]
        if latest.window_hit_rate >= hit_rate_floor:
            continue
        trailing = ordered[-consecutive_windows_required:]
        all_failing = len(trailing) == consecutive_windows_required and all(
            o.window_hit_rate < hit_rate_floor for o in trailing
        )
        if all_failing:
            results.append(
                SustainedFailure(
                    market=market,
                    setup=setup,
                    severity=FailureSeverity.SUSTAINED,
                    as_of_date=latest.as_of_date,
                    window_hit_rate=latest.window_hit_rate,
                    window_n=latest.window_n,
                    consecutive_windows_failed=consecutive_windows_required,
                    detail=(
                        f"{setup} on {market}: {consecutive_windows_required} consecutive "
                        f"rolling {latest.window_days}-day windows all below "
                        f"{hit_rate_floor:.0%} (latest {latest.window_hit_rate:.0%} over "
                        f"{latest.window_n} resolved calls, as of {latest.as_of_date})."
                    ),
                )
            )
        else:
            results.append(
                SustainedFailure(
                    market=market,
                    setup=setup,
                    severity=FailureSeverity.SINGLE,
                    as_of_date=latest.as_of_date,
                    window_hit_rate=latest.window_hit_rate,
                    window_n=latest.window_n,
                    consecutive_windows_failed=1,
                    detail=(
                        f"{setup} on {market}: latest rolling {latest.window_days}-day "
                        f"window is {latest.window_hit_rate:.0%} over {latest.window_n} "
                        f"resolved calls (as of {latest.as_of_date}), below the "
                        f"{hit_rate_floor:.0%} floor."
                    ),
                )
            )
    return results


def run_daily_check(
    market: str,
    *,
    log_path: Path | None = None,
    windows_log: Path = WINDOWS_LOG,
    as_of: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_trades_in_window: int = DEFAULT_MIN_TRADES_IN_WINDOW,
    hit_rate_floor: float = DEFAULT_HIT_RATE_FLOOR,
    consecutive_windows_required: int = DEFAULT_CONSECUTIVE_WINDOWS_REQUIRED,
) -> list[SustainedFailure]:
    """The single daily entry point: append today's observation, then re-derive severities
    from the full history. Called by `tradedesk review rolling-check` (see cli.py)."""

    path = log_path or LOG_PATHS[market]
    rows = load_log(path)
    append_observations(
        market,
        rows,
        as_of=as_of,
        window_days=window_days,
        min_trades_in_window=min_trades_in_window,
        windows_log=windows_log,
    )
    observations = load_observations(windows_log)
    return find_sustained_failures(
        market,
        observations,
        hit_rate_floor=hit_rate_floor,
        consecutive_windows_required=consecutive_windows_required,
        min_trades_in_window=min_trades_in_window,
    )
