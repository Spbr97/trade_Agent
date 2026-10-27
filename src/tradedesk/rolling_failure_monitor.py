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

2026-10-28 (owner-approved): judged on MONEY, not hit rate. A window fails when its calls
lose money on average after costs (`window_mean_net_r` < `NET_R_FLOOR`). The old 30% hit-rate
floor flagged every trend setup with a 2R target in a normal stretch (a setup that truly hits
35% falls below 30% in a quarter to a third of 15-26-call windows) while saying nothing about
whether it made money. Each call also counts once: since 2026-10-14 the tracker grades every
call under two exit contracts, and calls outside the ATR band can never alert - both are
filtered exactly as `profit_gate.live_results` does. Observations written before this change
carry no net R and are not judged, so SUSTAINED needs three days under the new rule.
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
DEFAULT_HIT_RATE_FLOOR = 0.3  # recorded for display only since 2026-10-28
DEFAULT_CONSECUTIVE_WINDOWS_REQUIRED = 3
NET_R_FLOOR = 0.0  # a window fails when its calls lose money on average after costs
TRADED_OUTCOMES = {"target", "stop", "gap_stop", "timeout"}


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
    window_mean_net_r: float | None = None  # None on rows written before 2026-10-28


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
    window_mean_net_r: float | None = None


def _resolved_date(row: TrackedSignal) -> date | None:
    if row.outcome is None or row.resolved_at is None:
        return None
    return datetime.fromisoformat(row.resolved_at).date()


def judged_once(row: TrackedSignal) -> bool:
    """One row per call, and only calls that could ever alert (as profit_gate counts them)."""
    from tradedesk.profit_gate import JUDGED_CONTRACTS, UNTRADEABLE_PREFIX

    if (row.contract_version or JUDGED_CONTRACTS[1]) not in JUDGED_CONTRACTS:
        return False
    return not any(str(why).startswith(UNTRADEABLE_PREFIX) for why in row.rejected_for or [])


def call_net_r(row: TrackedSignal, round_trip: float | None) -> float | None:
    """The call's R after costs: the tracker's own `net_r` (real fees), else gross R less the
    round trip in R (profit_gate.after_cost_r); gross R when no cost figure is available."""
    if row.outcome not in TRADED_OUTCOMES:
        return None
    if row.net_r is not None:
        return float(row.net_r)
    if row.r_multiple is None:
        return None
    if round_trip is None or not row.entry > row.stop:
        return float(row.r_multiple)
    return float(row.r_multiple) - round_trip * row.entry / (row.entry - row.stop)


def compute_window(
    rows: list[TrackedSignal],
    *,
    as_of: date,
    window_days: int,
    round_trip: float | None = None,
) -> tuple[int, float, float | None] | None:
    """Returns (n, hit_rate, mean net R) among resolved calls in [as_of - window_days, as_of],
    or None if there is nothing resolved in the window at all (distinct from "0 wins", which
    IS a real, countable observation). Mean net R is over the calls actually traded."""

    start = as_of - timedelta(days=window_days)
    in_window = []
    for row in rows:
        resolved = _resolved_date(row)
        if resolved is not None and start <= resolved <= as_of:
            in_window.append(row)
    if not in_window:
        return None
    wins = sum(1 for r in in_window if r.outcome == "target")
    nets = [v for r in in_window if (v := call_net_r(r, round_trip)) is not None]
    return len(in_window), wins / len(in_window), (sum(nets) / len(nets) if nets else None)


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
        if judged_once(row):
            by_setup[row.setup].append(row)
    try:
        from tradedesk.digests import round_trip

        cost: float | None = round_trip(market)
    except Exception:  # noqa: BLE001 - rows without a recorded net R fall back to gross R
        cost = None

    recorded_at = datetime.now(IST).isoformat()
    observations: list[RollingWindowObservation] = []
    for setup, setup_rows in sorted(by_setup.items()):
        if (setup, as_of.isoformat()) in already_recorded:
            continue
        window = compute_window(
            setup_rows, as_of=as_of, window_days=window_days, round_trip=cost
        )
        if window is None:
            continue
        n, hit_rate, mean_net = window
        observations.append(
            RollingWindowObservation(
                market=market,
                setup=setup,
                as_of_date=as_of.isoformat(),
                window_days=window_days,
                window_n=n,
                window_hit_rate=hit_rate,
                recorded_at=recorded_at,
                window_mean_net_r=mean_net,
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
    net_r_floor: float = NET_R_FLOOR,
) -> list[SustainedFailure]:
    """Reads the full observation history (already-appended by `append_observations`) and
    decides severity per setup, most-recent-first: SUSTAINED if the last
    `consecutive_windows_required` qualifying (n >= min_trades_in_window, net R recorded)
    observations ALL lose money after costs (mean net R < `net_r_floor`); SINGLE if only the
    latest qualifying one does. Neither fires if the latest qualifying observation makes
    money. `hit_rate_floor` is kept for callers and the message only - it no longer decides."""

    by_setup: dict[str, list[RollingWindowObservation]] = defaultdict(list)
    for obs in observations:
        if (
            obs.market == market
            and obs.window_n >= min_trades_in_window
            and obs.window_mean_net_r is not None
        ):
            by_setup[obs.setup].append(obs)

    def failing(o: RollingWindowObservation) -> bool:
        return o.window_mean_net_r is not None and o.window_mean_net_r < net_r_floor

    results: list[SustainedFailure] = []
    for setup, obs_list in sorted(by_setup.items()):
        ordered = sorted(obs_list, key=lambda o: o.as_of_date)
        if not ordered:
            continue
        latest = ordered[-1]
        if not failing(latest):
            continue
        trailing = ordered[-consecutive_windows_required:]
        all_failing = len(trailing) == consecutive_windows_required and all(
            failing(o) for o in trailing
        )
        money = f"{latest.window_mean_net_r:+.2f}R a call after costs"
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
                        f"rolling {latest.window_days}-day windows all lost money after "
                        f"costs (latest {money}, {latest.window_hit_rate:.0%} hit target, "
                        f"over {latest.window_n} resolved calls, as of {latest.as_of_date})."
                    ),
                    window_mean_net_r=latest.window_mean_net_r,
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
                        f"window lost money - {money} over {latest.window_n} resolved calls "
                        f"(as of {latest.as_of_date})."
                    ),
                    window_mean_net_r=latest.window_mean_net_r,
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
