"""The end-to-end entry point that was the last real gap in this feature: ties
`rolling_failure_monitor.py`'s real output to `config_tuning.py`/`retire_replace.py`
and submits any resulting real proposal via `decision_packet.submit_for_review`.

Never applies anything itself and never approves anything - a human still has to click
Approve (which, per this session's dashboard wiring, is when it actually applies). This
module's only job is to make sure a real flagged failure actually gets a real, validated
proposal in front of that human, instead of that step staying a manual, easy-to-forget
one.

Market-data loading below mirrors `cli.py::backtest`'s own setup for a market (reference
code, sector config for NSE, VIX) - reused, not re-derived, since that is the one already
tested path for turning a market name into a real `MarketData`/`BacktestConfig`.

The `tradedesk_lab` import below needs the repo root on `sys.path` to work from the
`tradedesk` console script (not just under pytest) - fixed once, for this whole package, in
`self_review/__init__.py` (see its own docstring), not repeated here.

2026-09-29: replacement research is no longer a one-candidate side step. Every run with a
flagged or already-retired setup on the market runs `replacement_search.py` - improvement
variants of those setups plus a grammar of new methods, continuing from where the last run
stopped, under a time budget - and every candidate that clears the full bar (gauntlet, +0.10R
over random, a multiple-testing-adjusted p-value, and beating the retired setups' own
forward result) becomes a NEW_DETECTOR proposal. A retirement is per-market, keeps the
setup shadow-tracked, and always carries the search's current plan; a drifted lab base
manifest now blocks the search visibly (it is named on the item) instead of the silent
`break` that left the 2026-09-29 crypto retirements with no replacement research at all.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tradedesk_lab.artifacts import ROOT, digest

from tradedesk.backtest.runner import BacktestConfig, MarketData, prepare_market
from tradedesk.broker.indstocks.models import IST, Interval
from tradedesk.cli import _reference_code, _resolve_db, _sector_config, _store
from tradedesk.config import load_config
from tradedesk.engine.signals import SetupKind
from tradedesk.markets import bse_market, crypto_market, nse_market
from tradedesk.proposals import ProposalKind
from tradedesk.review_queue import ReviewItem
from tradedesk.rolling_failure_monitor import FailureSeverity, run_daily_check
from tradedesk.self_review.config_tuning import propose_config_patches
from tradedesk.self_review.decision_packet import refresh_retirement_plan, submit_for_review
from tradedesk.self_review.detector_authoring import detector_payload
from tradedesk.self_review.replacement_search import (
    DEFAULT_TIME_BUDGET_S,
    SearchOutcome,
    run_replacement_search,
)
from tradedesk.self_review.retire_replace import propose_retirement

DEFAULT_MAX_CODES = 150


def load_real_market_data(
    market: str, *, root: Path = ROOT, db: Path | None = None, max_codes: int = DEFAULT_MAX_CODES
) -> tuple[MarketData, BacktestConfig]:
    """The exact market-preparation shape `cli.py::backtest` already uses, restricted to
    all three setups (a caller narrows to one via `dataclasses.replace(cfg, setups=[kind])`
    before running `config_tuning.propose_config_patches`/the harness gauntlet)."""

    settings = load_config(root)
    resolved_db = _resolve_db(db if db is not None else Path("data/tradedesk.duckdb"), market)
    mkt = {"crypto": crypto_market, "bse": bse_market}.get(market, nse_market)(settings)
    cfg = BacktestConfig(
        setups=[SetupKind.BASE_BREAKOUT, SetupKind.TREND_PULLBACK, SetupKind.NR7_BREAKOUT],
        start=date(2023, 9, 1),
        end=datetime.now(IST).date(),
        capital=float(settings.risk.trading_capital),
        risk=settings.risk,
        engine=settings.engine,
        setup_params={k: v.model_dump() for k, v in settings.setups.setups.items()},
        universe_rules=mkt.universe_rules,
        slippage_pct=float(mkt.costs.slippage_pct),
        costs=mkt.costs,
        qty_step=mkt.qty_step,
        min_notional=mkt.min_notional_inr,
        vix_required=mkt.vix_required,
    )
    with _store(resolved_db) as store:
        if market == "crypto":
            ref = f"{mkt.code_prefix}{mkt.benchmark_name}"
            vix = None
        elif market == "bse":
            ref = _reference_code(store, root, mkt.benchmark_name, exch="BSE")
            vix = None
        else:
            ref = _reference_code(store, root)
            vix = store.index_code(settings.universe.volatility_index)
        cfg.vix_code = vix
        if market == "nse":
            cfg.sector_of, cfg.sector_codes = _sector_config(store, root)
        universe = [c for c in store.codes(Interval.D1) if c not in (ref, vix)][:max_codes]
        md = prepare_market(store, universe, ref, cfg)
    return md, cfg


def _train_test_split(md: MarketData) -> date:
    """70/30 chronological split of the loaded calendar - the same 'tune on the earlier
    slice, judge on the later one' discipline every geometry search in this project uses,
    just derived from whatever history is actually loaded rather than a hardcoded date."""

    calendar = sorted(md.calendar)
    if len(calendar) < 10:
        return calendar[-1] if calendar else datetime.now(IST).date()
    return calendar[int(len(calendar) * 0.7)]


@dataclass
class SelfReviewResult:
    items: list[ReviewItem] = field(default_factory=list)
    search: SearchOutcome | None = None


def _valid_setups(names: list[str]) -> list[str]:
    valid = []
    for name in names:
        try:
            SetupKind(name)
        except ValueError:
            continue  # a setup name the monitor tracks that isn't a real production SetupKind
        if name not in valid:
            valid.append(name)
    return valid


MAX_NEW_DETECTORS_PER_RUN = 3
"""Cap on NEW_DETECTOR items one run may submit. Passing candidates are usually variants of
one idea (Donchian-20 with different filters and exits); each needs its own human approval
and they are highly correlated, so the queue takes the best few, never the whole family."""


def select_for_submission(passed: list[Any], by_name: dict[str, Any]) -> list[Any]:
    """Best net R first, at most one per trigger (the idea), at most MAX per run. Only ever
    FEWER items than before - no candidate that failed a bar is added by this."""
    chosen: list[Any] = []
    seen: set[str] = set()
    for found in sorted(passed, key=lambda r: -(r.net_r if r.net_r is not None else -1e9)):
        idea = getattr(by_name.get(found.name), "trigger", None) or found.name
        if idea in seen:
            continue
        seen.add(idea)
        chosen.append(found)
        if len(chosen) >= MAX_NEW_DETECTORS_PER_RUN:
            break
    return chosen


def run_self_review(
    market: str,
    *,
    root: Path = ROOT,
    db: Path | None = None,
    max_codes: int = DEFAULT_MAX_CODES,
    search_time_budget_s: float = DEFAULT_TIME_BUDGET_S,
) -> SelfReviewResult:
    """The real daily job, in three parts:

    1. Rolling monitor (`run_daily_check`).
    2. Replacement search - whenever any setup on this market is flagged OR already
       retired here, one budgeted `run_replacement_search` covers all of them: improvement
       variants of those setups plus new-method candidates, continuing from where the last
       run stopped. A retired setup stays a research target for as long as it stays
       retired, so it keeps being re-tested with new filters/exits against fresh data.
       Every candidate that clears the full bar is submitted as a NEW_DETECTOR proposal.
    3. Per flagged setup: SUSTAINED -> a per-market retirement proposal carrying the
       search's current plan (or, if one is already pending, that item's plan is
       refreshed); SINGLE -> the config-tuning search, as before.
    """

    failures = run_daily_check(market)
    settings = load_config(root)
    retired_here = [k for k, v in settings.setups.setups.items() if market in v.retired_markets]
    flagged = _valid_setups([f.setup for f in failures])
    research_targets = _valid_setups(flagged + retired_here)
    result = SelfReviewResult()
    if not research_targets:
        return result

    resolved_db = _resolve_db(db if db is not None else Path("data/tradedesk.duckdb"), market)
    by_setup = {f.setup: f for f in failures}

    search = run_replacement_search(
        market,
        research_targets,
        root=root,
        db=resolved_db,
        max_codes=max_codes,
        time_budget_s=search_time_budget_s,
    )
    result.search = search
    for found in select_for_submission(search.passed, search.candidates_by_name):
        candidate = search.candidates_by_name[found.name]
        evidence = json.loads(Path(found.artifact_path).read_text(encoding="utf-8"))
        item = submit_for_review(
            ProposalKind.NEW_DETECTOR,
            detector_payload(candidate, market, found.experiment_id, found.candidate_id),
            failure=next((by_setup[s] for s in flagged if s in by_setup), None),
            gauntlet_report={
                "stopped_at": evidence.get("stopped_at"),
                "stages": evidence.get("stages"),
                "kill_criteria": evidence.get("kill_criteria"),
                "after_tax_r": evidence.get("after_tax_r"),
            },
            gauntlet_artifact_path=found.artifact_path,
            gauntlet_artifact_sha256=digest(Path(found.artifact_path)),
        )
        if item is not None:
            result.items.append(item)
            search.submitted_items.append(item.id)
    plan = search.summary()

    md = base_cfg = split = None
    for setup in flagged:
        failure = by_setup[setup]
        setup_kind = SetupKind(setup)
        if failure.severity == FailureSeverity.SUSTAINED:
            payload = propose_retirement(
                setup_kind,
                market,
                failure,
                already_retired=setup in retired_here,
                replacement_plan=plan,
                replacement_search_report=search.report_path,
            )
            if payload is None:
                continue
            item = submit_for_review(
                ProposalKind.RETIRE_SETUP,
                payload,
                failure=failure,
                gauntlet_report={},
                gauntlet_artifact_path="",
                gauntlet_artifact_sha256="",
            )
            if item is not None:
                result.items.append(item)
            else:
                refresh_retirement_plan(market, setup, plan)
            continue

        if md is None:
            md, base_cfg = load_real_market_data(market, root=root, db=db, max_codes=max_codes)
            split = _train_test_split(md)
        assert base_cfg is not None and split is not None
        cfg = replace(base_cfg, setups=[setup_kind])
        for candidate in propose_config_patches(setup_kind, market, md, cfg, split=split):
            item = submit_for_review(
                ProposalKind.CONFIG_PATCH,
                candidate.patch,
                failure=failure,
                gauntlet_report=asdict(candidate.gauntlet_report),
                gauntlet_artifact_path="",
                gauntlet_artifact_sha256="",
            )
            if item is not None:
                result.items.append(item)

    return result


def run_self_review_for_market(
    market: str,
    *,
    root: Path = ROOT,
    db: Path | None = None,
    max_codes: int = DEFAULT_MAX_CODES,
) -> list[ReviewItem]:
    """`run_self_review`, returning only the submitted items (the original entry point)."""
    return run_self_review(market, root=root, db=db, max_codes=max_codes).items
