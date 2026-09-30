"""Replacement search: the part of the self-review loop that looks for something BETTER,
not just something to switch off.

Every run, for a market with a flagged setup, this tests a batch of candidates from
`tradedesk_lab.candidates.candidate_space()` - improvement variants of the failing setups
first (same pattern, new trend filter / exit style), then genuinely different methods
(breakouts, momentum crossovers, volatility squeezes, volume thrusts, mean reversion) -
through the full harness gauntlet on real history for that market. It remembers what it
has tried (`tradedesk_lab/registry.py`, family `<market>:replacement`), so each run moves
on to candidates it has not tested yet and only re-tests an old one after
`retest_after_days` (new data may change the answer); it never loops over the same
rejects.

A candidate only counts as a replacement if it clears everything
`detector_authoring.candidate_verdict` checks: the full gauntlet and kill criteria, +0.10R
over random timing, a random-benchmark p-value tightened for how many candidates the search
has tried (so testing a hundred rules doesn't manufacture a false winner), and a net
expectancy after real costs that beats the retired setups' own forward-measured result.
The bar is never lowered to produce a winner - an honest "nothing yet, here is how close
the best one came and what runs next" is the output until one genuinely clears it.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tradedesk_lab.artifacts import OUTPUT, ROOT, verify_base, write_json
from tradedesk_lab.candidates import candidate_space
from tradedesk_lab.candidates.rules import clear_base_cache
from tradedesk_lab.registry import Registry

from tradedesk.broker.indstocks.models import IST
from tradedesk.config import load_config
from tradedesk.rolling_failure_monitor import LOG_PATHS
from tradedesk.self_review.detector_authoring import (
    BaseDrifted,
    author_and_validate,
    load_real_daily_frames,
    market_bundle,
)
from tradedesk.self_review.manifest_refresh import refresh_if_explained
from tradedesk.signal_tracker import load_log

DEFAULT_TIME_BUDGET_S = 20 * 60
DEFAULT_RETEST_AFTER_DAYS = 14
SEARCH_DIR = OUTPUT / "replacement_search"


def family_for(market: str) -> str:
    return f"{market}:replacement"


def search_p_value_threshold(space_size: int, alpha: float = 0.05, floor: float = 0.001) -> float:
    """Bonferroni over the whole candidate space, floored at the null baseline's own
    resolution (1/1000 cohorts - a p below that can't be measured, only reported as 0)."""
    return max(floor, alpha / max(space_size, 1))


def retired_baseline(
    market: str, setups: list[str], log_path: Path | None = None
) -> tuple[float | None, float | None]:
    """(best mean R, best hit rate) among the setups being replaced, from that market's
    graded signal-tracker calls. A replacement's net R has to beat the BEST of them, so it
    is better than every setup it would stand in for. These are gross (pre-cost) R while a
    candidate's is net of real costs - the comparison is deliberately tilted against the
    candidate. The hit rate is reported alongside, not gated on: a higher hit rate with a
    lower expectancy is not an improvement."""

    rows = load_log(log_path or LOG_PATHS[market])
    best_r: float | None = None
    best_hit: float | None = None
    for setup in setups:
        done = [r for r in rows.values() if r.setup == setup and r.outcome is not None]
        rs = [r.r_multiple for r in done if r.r_multiple is not None]
        if rs:
            mean = sum(rs) / len(rs)
            best_r = mean if best_r is None else max(best_r, mean)
        if done:
            hit = sum(1 for r in done if r.outcome == "target") / len(done)
            best_hit = hit if best_hit is None else max(best_hit, hit)
    return best_r, best_hit


@dataclass
class CandidateResult:
    name: str
    description: str
    family: str
    passed: bool
    net_r: float | None
    win_rate: float | None
    n_trades: int
    stopped_at: str | None
    p_value: float | None
    fail_reasons: list[str]
    experiment_id: str
    candidate_id: str
    artifact_path: str
    tested_at: str


@dataclass
class SearchOutcome:
    market: str
    status: str  # "ran" | "blocked" | "nothing_to_search"
    failing_setups: list[str]
    baseline_net_r: float | None
    baseline_hit_rate: float | None
    p_value_threshold: float
    space_size: int
    tested_total: int
    untested_remaining: int
    tested_this_run: list[CandidateResult] = field(default_factory=list)
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str | None = None
    report_path: str | None = None
    # Filled in by orchestrate once the NEW_DETECTOR items exist.
    submitted_items: list[str] = field(default_factory=list)
    candidates_by_name: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def passed(self) -> list[CandidateResult]:
        return [r for r in self.tested_this_run if r.passed]

    def summary(self) -> str:
        """The plain-English replacement plan shown on the review item."""
        base = f"{_fmt_r(self.baseline_net_r)}/call, {_fmt_pct(self.baseline_hit_rate)} hit rate"
        if self.status == "blocked":
            return (
                f"Replacement search BLOCKED on {self.market}: {self.blocked_reason}. "
                "Nothing was tested this run; it resumes automatically once that is cleared."
            )
        lines = [
            f"Replacement search on {self.market} (retired setups' forward result: {base}): "
            f"tested {len(self.tested_this_run)} candidates this run; "
            f"{self.tested_total}/{self.space_size} tried so far, "
            f"{self.untested_remaining} still untested - the next run continues with those."
        ]
        if self.passed:
            names = ", ".join(r.name for r in self.passed)
            lines.append(
                f"REPLACEMENT FOUND: {names} cleared the full gauntlet and beats the retired "
                "setups - submitted as NEW DETECTOR item(s)"
                + (f" {', '.join(self.submitted_items)}" if self.submitted_items else "")
                + ". Approving it adds it on this market only."
            )
        elif self.leaderboard:
            top = self.leaderboard[0]
            lines.append(
                f"Closest so far: {top['name']} ({top['description']}) - net "
                f"{_fmt_r(top['net_r'])}/trade, {_fmt_pct(top['win_rate'])} winners over "
                f"{top['n_trades']} trades, failed on: "
                f"{'; '.join(top['fail_reasons']) or 'n/a'}. Nothing is promoted until one "
                "clears every check."
            )
        else:
            lines.append("No candidate has produced a measurable result yet.")
        return " ".join(lines)


def _fmt_r(value: float | None) -> str:
    return f"{value:+.3f}R" if value is not None else "n/a"


def _fmt_pct(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "n/a"


def _history(market: str, registry_path: Path) -> dict[str, dict[str, Any]]:
    """Latest registry row per candidate name for this market's search family."""
    if not registry_path.exists():
        return {}
    with Registry(registry_path, readonly=True) as registry:
        rows = registry.candidates(family_for(market))
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = (row.get("parameters") or {}).get("candidate")
        if name:
            latest[name] = row  # rows are oldest-first, so the last one wins
    return latest


def streak_rule_outdated(row: dict[str, Any]) -> bool:
    """True when this candidate's stored result failed ONLY the old fixed losing-streak cap.

    The kill criteria's streak limit now scales with sample size and loss rate
    (harness/spec.py::chance_streak_limit). A result recorded before that carries no
    `allowed_losing_streak`, so its verdict was made under a bar that no longer exists and
    is re-tested once instead of waiting out the 14-day stale window. Every other failure
    (sample size, drawdown, expectancy, an earlier gauntlet stage) is unaffected by the
    change, so those results stay as they were."""
    path = row.get("artifact_path")
    if not path or not Path(path).exists():
        return False
    try:
        evidence = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    kill = evidence.get("kill_criteria") or {}
    failures = kill.get("failures") or []
    return (
        bool(failures)
        and all(str(f).startswith("losing_streak") for f in failures)
        and "allowed_losing_streak" not in (kill.get("measured") or {})
    )


def plan_batch(
    space: list[Any],
    history: dict[str, dict[str, Any]],
    *,
    now: datetime,
    retest_after_days: int = DEFAULT_RETEST_AFTER_DAYS,
) -> list[Any]:
    """Results whose only failure was the superseded fixed streak cap first (best net R
    first); then untested candidates in `space`'s priority order; then previously-tested
    ones whose last test is older than `retest_after_days`, stalest first. Anything tested
    more recently is left alone."""

    outdated = [c for c in space if c.name in history and streak_rule_outdated(history[c.name])]
    outdated.sort(
        key=lambda c: -float((history[c.name].get("metrics") or {}).get("net_r") or 0.0)
    )
    skip = {c.name for c in outdated}
    untested = [c for c in space if c.name not in history]
    cutoff = now - timedelta(days=retest_after_days)
    stale = [
        c
        for c in space
        if c.name in history
        and c.name not in skip
        and datetime.fromisoformat(history[c.name]["started_at"]) < cutoff
    ]
    stale.sort(key=lambda c: history[c.name]["started_at"])
    return outdated + untested + stale


def leaderboard(
    history: dict[str, dict[str, Any]], descriptions: dict[str, str]
) -> list[dict[str, Any]]:
    board = []
    for name, row in history.items():
        metrics = row.get("metrics") or {}
        board.append(
            {
                "name": name,
                "description": descriptions.get(name, name),
                "passed": bool(metrics.get("passed_gauntlet")),
                "net_r": metrics.get("net_r"),
                "win_rate": metrics.get("win_rate"),
                "n_trades": metrics.get("n_trades") or 0,
                "stopped_at": metrics.get("stopped_at"),
                "fail_reasons": metrics.get("fail_reasons") or [],
                "tested_at": row.get("started_at"),
            }
        )
    board.sort(
        key=lambda r: (r["passed"], r["net_r"] if r["net_r"] is not None else float("-inf")),
        reverse=True,
    )
    return board


def run_replacement_search(
    market: str,
    failing_setups: list[str],
    *,
    root: Path = ROOT,
    db: Path,
    max_codes: int = 150,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    retest_after_days: int = DEFAULT_RETEST_AFTER_DAYS,
    registry_path: Path = OUTPUT / "registry.sqlite",
    search_dir: Path = SEARCH_DIR,
    output_dir: Path = OUTPUT,
    log_path: Path | None = None,
    now: datetime | None = None,
) -> SearchOutcome:
    now = now or datetime.now(IST)
    settings = load_config(root)
    setup_params = {k: v.model_dump() for k, v in settings.setups.setups.items()}
    space = candidate_space(failing_setups, setup_params)
    descriptions = {c.name: getattr(c, "describe", lambda c=c: c.name)() for c in space}
    by_name = {c.name: c for c in space}
    threshold = search_p_value_threshold(len(space))
    baseline, baseline_hit = retired_baseline(market, failing_setups, log_path)
    history = _history(market, registry_path)

    outcome = SearchOutcome(
        market=market,
        status="ran",
        failing_setups=list(failing_setups),
        baseline_net_r=baseline,
        baseline_hit_rate=baseline_hit,
        p_value_threshold=threshold,
        space_size=len(space),
        tested_total=len([n for n in history if n in by_name]),
        untested_remaining=len([c for c in space if c.name not in history]),
        candidates_by_name=by_name,
    )

    if root == ROOT:  # real run only; a test's temp root never touches the manifest
        try:  # narrow auto-refresh: only when every drifted file is committed history
            refresh_if_explained()
        except Exception:  # noqa: BLE001 - a refresh failure must only leave the run blocked
            pass
    base_state = verify_base()
    if not base_state["unchanged"]:
        outcome.status = "blocked"
        outcome.blocked_reason = (
            "the lab's base manifest no longer matches production "
            f"({', '.join(base_state['changed'])}) - after reviewing those changes, refresh "
            "it: back up and delete data/m14_m18/base_manifest.json, then re-run "
            "tradedesk_lab.artifacts.protect()"
        )
        outcome.leaderboard = leaderboard(history, descriptions)
        _write_report(outcome, search_dir, now)
        return outcome

    batch = plan_batch(space, history, now=now, retest_after_days=retest_after_days)
    if not batch:
        outcome.leaderboard = leaderboard(history, descriptions)
        _write_report(outcome, search_dir, now)
        return outcome

    mkt = market_bundle(market, settings)
    frames = load_real_daily_frames(
        db, max_codes=max_codes, exclude=frozenset(mkt.universe_rules.exclude_codes)
    )
    clear_base_cache()
    started = time.monotonic()
    for candidate in batch:
        if outcome.tested_this_run and time.monotonic() - started >= time_budget_s:
            break
        try:
            report, _beats, experiment_id, candidate_id, artifact_path = author_and_validate(
                candidate,
                market,
                root=root,
                db=db,
                registry_path=registry_path,
                max_codes=max_codes,
                family=family_for(market),
                frames=frames,
                baseline_net_r=baseline,
                max_null_p_value=threshold,
                skip_base_check=True,
                output_dir=output_dir,
            )
        except BaseDrifted as exc:  # pragma: no cover - verify_base already ran above
            outcome.status = "blocked"
            outcome.blocked_reason = str(exc)
            break
        evidence = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        bench = report.stages.get("random_entry_benchmark") or {}
        net_r = (report.stages.get("in_sample") or {}).get("net_expectancy_r")
        outcome.tested_this_run.append(
            CandidateResult(
                name=candidate.name,
                description=descriptions[candidate.name],
                family=getattr(candidate, "family", "library"),
                passed=bool(evidence.get("passed")),
                net_r=evidence.get("net_r", net_r),
                win_rate=evidence.get("win_rate"),
                n_trades=int(evidence.get("n_trades") or 0),
                stopped_at=report.stopped_at,
                p_value=bench.get("p_value"),
                fail_reasons=list(evidence.get("fail_reasons") or []),
                experiment_id=experiment_id,
                candidate_id=candidate_id,
                artifact_path=str(artifact_path),
                tested_at=now.isoformat(),
            )
        )

    history = _history(market, registry_path)
    outcome.tested_total = len([n for n in history if n in by_name])
    outcome.untested_remaining = len([c for c in space if c.name not in history])
    outcome.leaderboard = leaderboard(history, descriptions)
    _write_report(outcome, search_dir, now)
    return outcome


def _write_report(outcome: SearchOutcome, search_dir: Path, now: datetime) -> None:
    path = search_dir / outcome.market / f"{now.strftime('%Y-%m-%dT%H%M%S')}.json"
    payload = asdict(outcome)
    payload.pop("candidates_by_name", None)
    payload["summary"] = outcome.summary()
    payload["leaderboard"] = outcome.leaderboard[:25]
    write_json(path, payload)
    outcome.report_path = str(path)
