"""self_review/orchestrate.py: the daily dispatch from a real flagged failure to real,
submitted proposals - a replacement search for every flagged or already-retired setup,
SUSTAINED -> a per-market retirement carrying that search's plan, SINGLE -> config tuning,
a passing candidate -> a NEW_DETECTOR proposal, and never an auto-approval.

`orchestrate.py` imports each dependency by name, so every monkeypatch below targets that
name on the `orchestrate` module itself, not on the function's origin module.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from tradedesk_lab.harness.gauntlet import GauntletReport

from tradedesk import review_queue as rq
from tradedesk.config.models import EngineConfig, RiskConfig, SetupConfig
from tradedesk.data.universe import UniverseRules
from tradedesk.engine.signals import SetupKind
from tradedesk.proposals import ConfigPatch, RetireSetup, load_proposal
from tradedesk.rolling_failure_monitor import FailureSeverity, SustainedFailure
from tradedesk.self_review import config_tuning as ct
from tradedesk.self_review import decision_packet as dp
from tradedesk.self_review import orchestrate as orch
from tradedesk.self_review.replacement_search import CandidateResult, SearchOutcome


def _failure(setup: str, severity: FailureSeverity) -> SustainedFailure:
    return SustainedFailure(
        market="nse", setup=setup, severity=severity, as_of_date="2026-09-27",
        window_hit_rate=0.1, window_n=200, consecutive_windows_failed=3, detail="d",
    )


def _settings(retired: dict[str, list[str]] | None = None):
    retired = retired or {}
    setups = {
        name: SetupConfig(enabled=True, retired_markets=retired.get(name, []))
        for name in ("base_breakout", "trend_pullback", "nr7_breakout")
    }
    return SimpleNamespace(setups=SimpleNamespace(setups=setups))


def _fake_market_data():
    from tradedesk.backtest.runner import BacktestConfig

    md = SimpleNamespace(calendar=[date(2026, 1, 1), date(2026, 6, 1), date(2026, 9, 1)])
    cfg = BacktestConfig(
        setups=[SetupKind.BASE_BREAKOUT, SetupKind.NR7_BREAKOUT],
        start=date(2023, 9, 1),
        end=date(2026, 9, 1),
        capital=1_000_000.0,
        risk=RiskConfig(trading_capital=Decimal("1000000")),
        engine=EngineConfig(),
        universe_rules=UniverseRules(),
    )
    return md, cfg


def _outcome(failing, *, passed: list[CandidateResult] | None = None, status="ran"):
    out = SearchOutcome(
        market="nse", status=status, failing_setups=list(failing), baseline_net_r=-0.4,
        baseline_hit_rate=0.2, p_value_threshold=0.001, space_size=10, tested_total=3,
        untested_remaining=7, tested_this_run=list(passed or []),
        blocked_reason="manifest drift" if status == "blocked" else None,
    )
    for r in passed or []:
        out.candidates_by_name[r.name] = SimpleNamespace(name=r.name)
    return out


def _passed_result(tmp_path: Path) -> CandidateResult:
    artifact = tmp_path / "report.json"
    artifact.write_text(
        json.dumps({"stopped_at": None, "stages": {}, "kill_criteria": {"passed": True}}),
        encoding="utf-8",
    )
    return CandidateResult(
        name="r_donchian55_bull_stack_trend_trail", description="d", family="momentum",
        passed=True, net_r=0.12, win_rate=0.41, n_trades=520, stopped_at=None, p_value=0.0,
        fail_reasons=[], experiment_id="exp1", candidate_id="cand1",
        artifact_path=str(artifact), tested_at="2026-09-29T00:20:00+05:30",
    )


def _wire(tmp_path: Path, monkeypatch, failures, *, retired=None, search=None):
    review_path = tmp_path / "queue.jsonl"
    proposals_dir = tmp_path / "proposals"
    search_calls: list[list[str]] = []

    def _submit(kind, payload, **kw):
        return dp.submit_for_review(
            kind, payload, review_path=review_path, proposals_dir=proposals_dir, **kw
        )

    def _refresh(market, setup, plan):
        return dp.refresh_retirement_plan(market, setup, plan, review_path=review_path)

    def _search(market, targets, **kw):
        search_calls.append(list(targets))
        return search(targets) if search else _outcome(targets)

    monkeypatch.setattr(orch, "run_daily_check", lambda market: failures)
    monkeypatch.setattr(orch, "load_config", lambda root: _settings(retired))
    monkeypatch.setattr(orch, "load_real_market_data", lambda market, **kw: _fake_market_data())
    monkeypatch.setattr(orch, "run_replacement_search", _search)
    monkeypatch.setattr(orch, "submit_for_review", _submit)
    monkeypatch.setattr(orch, "refresh_retirement_plan", _refresh)
    return review_path, search_calls


def test_sustained_failure_gets_a_retirement_that_carries_the_replacement_plan(
    tmp_path: Path, monkeypatch
) -> None:
    config_tuning_calls = []
    monkeypatch.setattr(
        orch, "propose_config_patches", lambda *a, **kw: config_tuning_calls.append(a) or []
    )
    _, search_calls = _wire(
        tmp_path, monkeypatch, [_failure("nr7_breakout", FailureSeverity.SUSTAINED)]
    )

    result = orch.run_self_review("nse", root=tmp_path)

    assert search_calls == [["nr7_breakout"]]  # research runs, it isn't just a retirement
    assert config_tuning_calls == []
    assert len(result.items) == 1
    item = result.items[0]
    assert item.title.startswith("RETIRE nr7_breakout on nse (shadow-tracked)")
    assert "other markets untouched" in item.proposal
    assert "Replacement search on nse" in item.proposal
    payload = load_proposal(Path(item.proposal_ref)).payload
    assert isinstance(payload, RetireSetup)
    assert payload.replacement_plan and "still untested" in payload.replacement_plan


def test_single_severity_gets_config_tuning_and_research_but_no_retirement(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(orch, "_train_test_split", lambda md: "2026-01-01")
    patch = ConfigPatch(
        setup="base_breakout", market="nse", path="setups.base_breakout.partial_at_r",
        old_value=2.0, new_value=2.5,
    )
    candidate = ct.CandidatePatch(
        patch=patch,
        gauntlet_report=GauntletReport(
            strategy_name="x", stopped_at=None, stages={}, kill_criteria={"passed": True}
        ),
    )
    monkeypatch.setattr(orch, "propose_config_patches", lambda *a, **kw: [candidate])
    _, search_calls = _wire(
        tmp_path, monkeypatch, [_failure("base_breakout", FailureSeverity.SINGLE)]
    )

    result = orch.run_self_review("nse", root=tmp_path)

    assert search_calls == [["base_breakout"]]
    assert [i.title for i in result.items] == ["TUNE base_breakout on nse: partial_at_r 2.0->2.5"]


def test_a_passing_candidate_is_submitted_as_a_new_detector_alongside_the_retirement(
    tmp_path: Path, monkeypatch
) -> None:
    found = _passed_result(tmp_path)
    _wire(
        tmp_path, monkeypatch, [_failure("nr7_breakout", FailureSeverity.SUSTAINED)],
        search=lambda targets: _outcome(targets, passed=[found]),
    )

    result = orch.run_self_review("nse", root=tmp_path)

    titles = sorted(item.title for item in result.items)
    assert titles[0] == "NEW DETECTOR r_donchian55_bull_stack_trend_trail on nse"
    assert titles[1].startswith("RETIRE nr7_breakout")
    retire = next(i for i in result.items if i.title.startswith("RETIRE"))
    detector = next(i for i in result.items if i.title.startswith("NEW DETECTOR"))
    assert "REPLACEMENT FOUND" in retire.proposal
    assert detector.id in retire.proposal  # the plan points at the item to approve


def test_a_setup_already_retired_here_keeps_being_researched(
    tmp_path: Path, monkeypatch
) -> None:
    # Nothing flagged today, but base_breakout is retired on nse: research continues so a
    # better version can still be found; no new retirement is raised for it.
    _, search_calls = _wire(tmp_path, monkeypatch, [], retired={"base_breakout": ["nse"]})

    result = orch.run_self_review("nse", root=tmp_path)

    assert search_calls == [["base_breakout"]]
    assert result.items == []
    assert result.search is not None


def test_nothing_flagged_and_nothing_retired_means_no_search(tmp_path: Path, monkeypatch) -> None:
    _, search_calls = _wire(tmp_path, monkeypatch, [], retired={"base_breakout": ["crypto"]})
    result = orch.run_self_review("nse", root=tmp_path)
    assert search_calls == []  # retired on crypto, not on nse
    assert result.items == [] and result.search is None


def test_a_second_run_refreshes_the_pending_retirement_instead_of_duplicating_it(
    tmp_path: Path, monkeypatch
) -> None:
    review_path, _ = _wire(
        tmp_path, monkeypatch, [_failure("nr7_breakout", FailureSeverity.SUSTAINED)]
    )
    first = orch.run_self_review("nse", root=tmp_path)
    assert len(first.items) == 1

    found = _passed_result(tmp_path)
    _wire(
        tmp_path, monkeypatch, [_failure("nr7_breakout", FailureSeverity.SUSTAINED)],
        search=lambda targets: _outcome(targets, passed=[found]),
    )
    second = orch.run_self_review("nse", root=tmp_path)

    assert [i.title for i in second.items] == [
        "NEW DETECTOR r_donchian55_bull_stack_trend_trail on nse"
    ]
    queue = rq.load_queue(review_path)
    retirements = [i for i in queue.values() if i.title.startswith("RETIRE")]
    assert len(retirements) == 1  # not stacked
    assert "REPLACEMENT FOUND" in retirements[0].proposal  # but updated with the news


def test_a_blocked_search_still_raises_the_retirement_and_says_why(
    tmp_path: Path, monkeypatch
) -> None:
    _wire(
        tmp_path, monkeypatch, [_failure("nr7_breakout", FailureSeverity.SUSTAINED)],
        search=lambda targets: _outcome(targets, status="blocked"),
    )
    result = orch.run_self_review("nse", root=tmp_path)
    assert len(result.items) == 1
    assert "BLOCKED" in result.items[0].proposal and "manifest drift" in result.items[0].proposal


def test_an_unknown_setup_name_is_skipped_not_crashed_on(tmp_path: Path, monkeypatch) -> None:
    _, search_calls = _wire(
        tmp_path, monkeypatch, [_failure("some_experimental_rule", FailureSeverity.SUSTAINED)]
    )
    result = orch.run_self_review("nse", root=tmp_path)
    assert result.items == [] and search_calls == []


def test_run_self_review_for_market_returns_just_the_items(tmp_path: Path, monkeypatch) -> None:
    _wire(tmp_path, monkeypatch, [_failure("nr7_breakout", FailureSeverity.SUSTAINED)])
    items = orch.run_self_review_for_market("nse", root=tmp_path)
    assert len(items) == 1 and items[0].title.startswith("RETIRE nr7_breakout")


def test_load_real_market_data_against_the_real_local_database() -> None:
    """Not mocked - confirms load_real_market_data actually works against the real
    NSE duckdb store (reference code, VIX, sector config, prepare_market), the one piece
    of this module too tied to the real data layer to fake convincingly."""
    md, cfg = orch.load_real_market_data("nse", max_codes=20)
    assert len(md.features) > 0
    assert len(md.calendar) > 0
    assert set(cfg.setups) == {
        SetupKind.BASE_BREAKOUT, SetupKind.TREND_PULLBACK, SetupKind.NR7_BREAKOUT
    }
