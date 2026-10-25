"""Is each setup profitable after costs on LIVE calls? One verdict per setup and market.

Owner-approved 2026-10-18. Inputs are the per-market signal trackers (every call the scan
makes, graded with real fees: the rows' own `net_r`) and the replacement search's historical
gauntlet results. Output: data/reports/profit_gate_status.json (read by the scan) and
data/reports/profit_gate_scoreboard.json (the dashboard's winner numbers).

Verdicts, checked every day:

  TRADEABLE     lowest plausible live average > +0.05R per trade after costs, >= 50 calls
  PROVISIONAL   proven on history (>= 500 trades, >= +0.20R after costs on that market,
                NSE/BSE only) and not stopped: may alert at a QUARTER of normal size
  STOPPED       was provisional, but live results say below break-even, or 12 losses in a row
  NO EDGE       highest plausible live average < 0 (stays on the roster; the replacement
                search looks for something better)
  COLLECTING    not enough live evidence either way yet

"Plausible" is a time-uniform 95% range (Waudby-Smith et al.'s asymptotic confidence
sequence, from the observed spread of results), so looking every day does not inflate the error,
and the 5% is split across every setup tested in a pool so trying many cannot manufacture a
winner. NSE and BSE results of the same setup are pooled (same rule, same kind of market);
crypto is judged on its own. Nothing here places an order; it decides only what may alert.
"""

from __future__ import annotations

import glob
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk.broker.indstocks.models import IST

TRACKERS = {
    "nse": Path("data/reports/nse_signal_tracking.jsonl"),
    "bse": Path("data/reports/bse_signal_tracking.jsonl"),
    "crypto": Path("data/reports/crypto_signal_tracking.jsonl"),
}
SEARCH_REPORTS = Path("data/m14_m18/replacement_search")
STATUS_PATH = Path("data/reports/profit_gate_status.json")
SCOREBOARD_PATH = Path("data/reports/profit_gate_scoreboard.json")
POOLS = {"nse": "equity", "bse": "equity", "crypto": "crypto"}
TRADED_OUTCOMES = {"target", "stop", "gap_stop", "timeout"}

ALPHA = 0.05
CLIP_R = (-3.0, 8.0)  # a rare >8R winner counts as 8R, a <-3R gap loss as -3R
MIN_CALLS = 20  # below this the spread of results is not known well enough to judge
TARGET_CALLS = 250  # the range is tightest around here
STOP_ALPHA = 0.20  # stopping a provisional setup errs towards caution: it costs no money
TRADEABLE_MIN_CALLS = 50
TRADEABLE_MIN_LOWER_R = 0.05
PROVISIONAL_MARKETS = ("nse", "bse")
PROVISIONAL_MIN_HISTORY_TRADES = 500
PROVISIONAL_MIN_HISTORY_R = 0.20
PROVISIONAL_SIZE = 0.25
STOP_LOSING_STREAK = 12
STATUS_MAX_AGE = timedelta(days=4)  # older than this, the scan treats every setup as unproven

ALERTING = {"tradeable": 1.0, "provisional": PROVISIONAL_SIZE}

# Since 2026-10-14 the tracker grades every call twice, under a 3-session 0.75R contract and
# a 10-session 2R one (signal_tracker.log_exit_contract_signals). Counting both made each
# call two live results (2026-10-25: 205 NSE/BSE calls counted twice, doubling losing
# streaks). A call counts once: its swing row - the multi-session hold the history was
# proven on - or, for calls from before the contracts, its legacy row.
JUDGED_CONTRACTS = ("swing-v1", "legacy-t1-tracker-v1")
# A call outside the market's ATR band can never alert, whatever the verdict. Money-market
# ETFs (LIQUIDCASE, LIQUIDBEES, ...) sit there: a stop a few paise away turns the charges
# into -3R to -49R and buried r_tsmom252's live record (2026-10-25).
UNTRADEABLE_PREFIX = "ATR "
# Owner's decision (2026-10-25): the replacement search now re-tests earlier passes on the
# fair random benchmark. Until GRANDFATHER_UNTIL a pass that later fails that retest keeps
# its quarter-size permission (flagged `fair_test_failed_in`), so alerts change only when the
# owner has seen the fair-test table and decided.
FAIR_TEST_FROM = "2026-10-25"
GRANDFATHER_UNTIL = "2026-11-08"


def confidence_sequence(
    values: Sequence[float], *, alpha: float, target_n: int = TARGET_CALLS
) -> tuple[float, float]:
    """Time-uniform (1 - alpha) range for the mean of `values`, valid however often it is
    looked at: the asymptotic confidence sequence of Waudby-Smith, Arbour, Sinha, Kennedy &
    Ramdas (2021, "Time-uniform central limit theory"), tuned to be tightest near `target_n`
    calls. Values are clipped to CLIP_R first so one freak result cannot swing it. Needs a
    real spread of results: with fewer than MIN_CALLS it reports the widest range."""

    lo, hi = CLIP_R
    xs = [min(max(v, lo), hi) for v in values]
    n = len(xs)
    if n < MIN_CALLS:
        return lo, hi
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    log_a = math.log(alpha)
    rho2 = (-2 * log_a + math.log(-2 * log_a + 1)) / target_n
    width = sd * math.sqrt(
        2 * (n * rho2 + 1) / (n**2 * rho2) * math.log(math.sqrt(n * rho2 + 1) / alpha)
    )
    return mean - width, mean + width


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def after_cost_r(row: Mapping[str, Any], round_trip: float) -> float | None:
    """The tracker's own `net_r` (real fees) when it has one; otherwise gross R less the
    market's round trip expressed in R for this call (cost % x entry / risk per share)."""

    if row.get("net_r") is not None:
        return float(row["net_r"])
    try:
        gross = float(row["r_multiple"])
        entry, stop = float(row["entry"]), float(row["stop"])
    except (KeyError, TypeError, ValueError):
        return None
    if entry <= stop:
        return None
    return gross - round_trip * entry / (entry - stop)


def live_results(market: str, *, root: Path = Path(".")) -> dict[str, list[dict[str, Any]]]:
    """Resolved, actually-traded LIVE calls per setup in resolution order, in R after costs.
    Backfilled history rows (source != "live") are not live evidence and are skipped, each
    call counts once (JUDGED_CONTRACTS), and calls outside the ATR band never count."""

    from tradedesk.digests import round_trip

    cost = round_trip(market, root=root)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(root / TRACKERS[market]):
        if row.get("source") != "live" or row.get("outcome") not in TRADED_OUTCOMES:
            continue
        if str(row.get("contract_version") or JUDGED_CONTRACTS[1]) not in JUDGED_CONTRACTS:
            continue
        if any(str(why).startswith(UNTRADEABLE_PREFIX) for why in row.get("rejected_for") or []):
            continue
        r = after_cost_r(row, cost)
        if r is None:
            continue
        out.setdefault(str(row.get("setup")), []).append(
            {
                "symbol": row.get("symbol"),
                "net_r": r,
                "resolved_at": str(row.get("resolved_at") or ""),
                "won": r > 0,
            }
        )
    for calls in out.values():
        calls.sort(key=lambda c: c["resolved_at"])
    return out


def history(market: str, *, root: Path = Path(".")) -> dict[str, dict[str, Any]]:
    """The newest gauntlet result per candidate name from that market's replacement-search
    reports: {"passed", "net_r", "n_trades", "win_rate", "tested_in"}."""

    found: dict[str, dict[str, Any]] = {}
    folder = root / SEARCH_REPORTS / market
    for path in sorted(glob.glob(str(folder / "*.json"))):
        try:
            report = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = list(report.get("leaderboard") or [])
        rows += [r for r in report.get("tested_this_run") or [] if isinstance(r, dict)]
        for row in rows:
            if row.get("name") and row.get("net_r") is not None:
                new = {
                    "passed": bool(row.get("passed")),
                    "net_r": float(row["net_r"]),
                    "n_trades": int(row.get("n_trades") or 0),
                    "win_rate": row.get("win_rate"),
                    "tested_in": os.path.basename(path),
                }
                prior = found.get(row["name"])
                if (
                    prior
                    and prior["passed"]
                    and not new["passed"]
                    and str(row.get("tested_at") or "") >= FAIR_TEST_FROM
                    and datetime.now(IST).date().isoformat() < GRANDFATHER_UNTIL
                ):
                    # Failed the fair random benchmark: keep the earlier pass for now and
                    # say so, instead of silently switching its alerts off.
                    prior["fair_test_failed_in"] = new["tested_in"]
                    continue
                found[row["name"]] = new
    return found


def history_qualifies(hist: Mapping[str, Any] | None) -> bool:
    return bool(
        hist
        and hist["passed"]
        and hist["n_trades"] >= PROVISIONAL_MIN_HISTORY_TRADES
        and hist["net_r"] >= PROVISIONAL_MIN_HISTORY_R
    )


def losing_streak(calls: Iterable[Mapping[str, Any]]) -> int:
    run = 0
    for call in calls:
        run = 0 if call["won"] else run + 1
    return run


@dataclass
class Verdict:
    setup: str
    market: str
    status: str
    size_factor: float
    live_calls: int
    live_winners: int
    live_mean_r: float | None
    live_lower_r: float
    live_upper_r: float
    pooled_calls: int
    losing_streak: int
    history: dict[str, Any] | None
    reason: str
    recent: list[dict[str, Any]] = field(default_factory=list)


def decide(
    setup: str,
    market: str,
    own: list[dict[str, Any]],
    pooled: list[dict[str, Any]],
    hist: Mapping[str, Any] | None,
    *,
    alpha: float,
) -> Verdict:
    values = [c["net_r"] for c in pooled]
    lower, upper = confidence_sequence(values, alpha=alpha)
    streak = losing_streak(pooled)
    proven_history = market in PROVISIONAL_MARKETS and history_qualifies(hist)
    if len(values) >= TRADEABLE_MIN_CALLS and lower > TRADEABLE_MIN_LOWER_R:
        status, reason = "tradeable", f"live lower bound {lower:+.2f}R > +0.05R after costs"
    elif values and upper < 0:
        status = "stopped" if proven_history else "no_edge"
        reason = f"live upper bound {upper:+.2f}R < 0 after costs"
    elif proven_history and confidence_sequence(values, alpha=STOP_ALPHA)[1] < 0:
        status = "stopped"
        reason = "live results below break-even after costs (80% range)"
    elif proven_history and streak >= STOP_LOSING_STREAK:
        status, reason = "stopped", f"{streak} losing calls in a row"
    elif proven_history:
        assert hist is not None
        status = "provisional"
        reason = (
            f"history {hist['net_r']:+.2f}R over {hist['n_trades']} trades after costs; "
            f"quarter size until live proof"
        )
    else:
        status, reason = "collecting", "not enough live evidence yet"
    own_values = [c["net_r"] for c in own]
    return Verdict(
        setup=setup,
        market=market,
        status=status,
        size_factor=ALERTING.get(status, 0.0),
        live_calls=len(own),
        live_winners=sum(1 for c in own if c["won"]),
        live_mean_r=(sum(own_values) / len(own_values)) if own_values else None,
        live_lower_r=lower,
        live_upper_r=upper,
        pooled_calls=len(values),
        losing_streak=streak,
        history=dict(hist) if hist else None,
        reason=reason,
        recent=[{k: c[k] for k in ("symbol", "net_r", "resolved_at")} for c in own[-5:]],
    )


def evaluate(rosters: Mapping[str, Sequence[str]], *, root: Path = Path(".")) -> list[Verdict]:
    """`rosters`: market -> setups that run there (enabled, not retired)."""

    live = {m: live_results(m, root=root) for m in rosters}
    hist = {m: history(m, root=root) for m in rosters}
    tests_per_pool: dict[str, int] = {}
    for market, setups in rosters.items():
        for setup in setups:
            if (hist[market].get(setup) or {}).get("passed"):
                tests_per_pool[POOLS[market]] = tests_per_pool.get(POOLS[market], 0) + 1
    verdicts = []
    for market, setups in rosters.items():
        pool = POOLS[market]
        alpha = ALPHA / max(1, tests_per_pool.get(pool, 1))
        for setup in setups:
            pooled = sorted(
                (
                    call
                    for other, setups_there in rosters.items()
                    if POOLS[other] == pool and setup in setups_there
                    for call in live[other].get(setup, [])
                ),
                key=lambda c: c["resolved_at"],
            )
            verdicts.append(
                decide(
                    setup,
                    market,
                    live[market].get(setup, []),
                    pooled,
                    hist[market].get(setup),
                    alpha=alpha,
                )  # fmt: skip
            )
    return verdicts


def write(
    verdicts: Sequence[Verdict],
    *,
    root: Path = Path("."),
    twins: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    now = datetime.now(IST).isoformat()
    status = {
        "written_at": now,
        "markets": {
            m: {
                v.setup: {
                    "status": v.status,
                    "size_factor": v.size_factor,
                    "history_trades": (v.history or {}).get("n_trades", 0),
                    "history_net_r": (v.history or {}).get("net_r", 0.0),
                    "reason": v.reason,
                }
                for v in verdicts
                if v.market == m
            }
            for m in sorted({v.market for v in verdicts})
        },
    }
    rows = []
    for v in verdicts:
        row = asdict(v)
        row["quick_twin"] = ((twins or {}).get(v.market) or {}).get(v.setup)
        rows.append(row)
    board = {"written_at": now, "rule": __doc__, "verdicts": rows}
    for path, payload in ((STATUS_PATH, status), (SCOREBOARD_PATH, board)):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        tmp.replace(target)
    return status


@dataclass(frozen=True)
class AlertingSetup:
    status: str  # "tradeable" | "provisional"
    size_factor: float
    history_trades: int
    history_net_r: float
    reason: str


def alerting_setups(
    market: str, *, root: Path = Path("."), now: datetime | None = None
) -> dict[str, AlertingSetup]:
    """Setups allowed to alert on `market` (tradeable: full size, provisional: a quarter).
    Fails closed: a missing, unreadable or stale status file allows none."""

    try:
        status = json.loads((root / STATUS_PATH).read_text(encoding="utf-8"))
        written = datetime.fromisoformat(status["written_at"])
    except (OSError, ValueError, KeyError):
        return {}
    if (now or datetime.now(IST)) - written > STATUS_MAX_AGE:
        return {}
    out = {}
    for setup, row in (status.get("markets", {}).get(market) or {}).items():
        state = str(row.get("status", ""))
        factor = ALERTING.get(state, 0.0)
        if factor > 0:
            out[setup] = AlertingSetup(
                status=state,
                size_factor=factor,
                history_trades=int(row.get("history_trades") or 0),
                history_net_r=float(row.get("history_net_r") or 0.0),
                reason=str(row.get("reason") or ""),
            )
    return out
