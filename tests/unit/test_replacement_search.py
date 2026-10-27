"""self_review/replacement_search.py: the part of the loop that looks for something better.
The real gauntlet/data path is exercised by the run itself; here `author_and_validate`,
`verify_base` and data loading are stubbed so the planning/memory/reporting logic - what
gets tried next, what gets remembered, what the plan says - is tested exactly."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from tradedesk_lab.harness.gauntlet import GauntletReport
from tradedesk_lab.registry import Registry

from tradedesk.broker.indstocks.models import IST
from tradedesk.self_review import replacement_search as rs

NOW = datetime(2026, 9, 29, 0, 20, tzinfo=IST)


def _c(name: str):
    return SimpleNamespace(name=name, describe=lambda: f"desc {name}", family="momentum")


def test_plan_batch_tries_untested_first_then_stale_and_skips_recent() -> None:
    space = [_c("a"), _c("b"), _c("c"), _c("d")]
    history = {
        "b": {"started_at": (NOW - timedelta(days=30)).isoformat()},  # stale
        "c": {"started_at": (NOW - timedelta(days=2)).isoformat()},  # recent - leave it
        "d": {"started_at": (NOW - timedelta(days=60)).isoformat()},  # stalest
    }
    batch = rs.plan_batch(space, history, now=NOW, retest_after_days=14)
    assert [c.name for c in batch] == ["a", "d", "b"]


def test_search_p_value_threshold_tightens_with_space_and_respects_resolution() -> None:
    assert rs.search_p_value_threshold(5) == pytest.approx(0.01)
    assert rs.search_p_value_threshold(250) == pytest.approx(0.0002)
    assert rs.search_p_value_threshold(777) == pytest.approx(0.0001)  # fair null's floor


def test_a_pass_from_before_the_fair_benchmark_is_retested_first(tmp_path: Path) -> None:
    def row(name: str, evidence: dict) -> dict:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return {"artifact_path": str(path), "started_at": "2026-10-24T10:00:00+00:00",
                "metrics": {"net_r": 0.2}}  # fmt: skip

    history = {
        "old_pass": row("old_pass", {"passed": True, "stages": {"random_entry_benchmark": {}}}),
        "fair_pass": row("fair_pass", {"passed": True, "stages": {"fair_random_benchmark": {}}}),
        "old_fail": row("old_fail", {"passed": False, "stages": {}}),
    }
    space = [_c("new"), _c("fair_pass"), _c("old_fail"), _c("old_pass")]
    now = datetime(2026, 10, 25, tzinfo=IST)
    assert [c.name for c in rs.plan_batch(space, history, now=now)] == ["old_pass", "new"]


def test_retired_baseline_is_the_best_of_the_setups_being_replaced(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    rows = []
    for i, (setup, outcome, r) in enumerate(
        [("a", "target", 2.0), ("a", "stop", -1.0), ("a", "stop", -1.0),
         ("b", "stop", -1.0), ("b", "stop", -1.0), ("c", None, None)]
    ):  # fmt: skip
        rows.append({
            "signal_id": f"s{i}", "scrip_code": "X", "symbol": "X", "setup": setup,
            "grade": "C", "armed_on": "2026-09-01", "entry": 1, "stop": 0.9, "t1": 1.2,
            "t2": 1.3, "net_rr_t1": None, "net_rr_t2": None, "rejected_for": [],
            "logged_at": "2026-09-01T00:00:00+05:30", "outcome": outcome, "r_multiple": r,
        })  # fmt: skip
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    best_r, best_hit = rs.retired_baseline("crypto", ["a", "b"], log)
    assert best_r == pytest.approx(0.0)  # a: (2-1-1)/3 beats b: -1
    assert best_hit == pytest.approx(1 / 3)


def _stub_world(monkeypatch, tmp_path: Path, *, verdicts: dict[str, bool], drifted=False):
    space = [_c(n) for n in verdicts]
    monkeypatch.setattr(rs, "candidate_space", lambda failing, params: space)
    monkeypatch.setattr(
        rs, "load_config", lambda root: SimpleNamespace(setups=SimpleNamespace(setups={}))
    )
    monkeypatch.setattr(
        rs, "market_bundle",
        lambda m, s: SimpleNamespace(universe_rules=SimpleNamespace(exclude_codes=())),
    )  # fmt: skip
    monkeypatch.setattr(rs, "load_real_daily_frames", lambda db, **kw: ({}, {}))
    monkeypatch.setattr(rs, "retired_baseline", lambda m, s, p=None: (-0.5, 0.2))
    monkeypatch.setattr(
        rs, "verify_base",
        lambda: {"unchanged": not drifted, "changed": ["src/x.py"] if drifted else []},
    )  # fmt: skip
    calls: list[str] = []

    def _author(candidate, market, *, registry_path, family, **kw):
        calls.append(candidate.name)
        passed = verdicts[candidate.name]
        with Registry(registry_path) as reg:
            eid = reg.begin({"candidate": candidate.name, "market": market})
            artifact = tmp_path / f"{candidate.name}.json"
            artifact.write_text(json.dumps({
                "passed": passed, "n_trades": 400, "net_r": 0.1 if passed else -0.2,
                "win_rate": 0.4, "fail_reasons": [] if passed else ["stopped at walk_forward"],
            }), encoding="utf-8")  # fmt: skip
            cid = reg.candidate(
                eid, family, {"candidate": candidate.name, "market": market},
                {"net_r": 0.1 if passed else -0.2, "win_rate": 0.4, "n_trades": 400,
                 "passed_gauntlet": passed, "stopped_at": None if passed else "walk_forward",
                 "fail_reasons": [] if passed else ["stopped at walk_forward"]},
                status="completed" if passed else "rejected",
            )  # fmt: skip
            reg.finish(eid, report={"passed": passed})
        report = GauntletReport(
            strategy_name=candidate.name,
            stopped_at=None if passed else "walk_forward",
            stages={"random_entry_benchmark": {"p_value": 0.0}},
            kill_criteria={"passed": passed},
        )
        return report, passed, eid, cid, artifact

    monkeypatch.setattr(rs, "author_and_validate", _author)
    return calls


def _run(tmp_path: Path, **kw):
    return rs.run_replacement_search(
        "crypto", ["nr7_breakout"], db=tmp_path / "x.duckdb",
        registry_path=tmp_path / "registry.sqlite", search_dir=tmp_path / "search",
        output_dir=tmp_path, now=kw.pop("now", NOW), **kw,
    )  # fmt: skip


def test_search_remembers_what_it_tried_and_moves_on(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_world(monkeypatch, tmp_path, verdicts={"a": False, "b": False, "c": False})

    first = _run(tmp_path, time_budget_s=0)  # budget exhausted after the first candidate
    assert calls == ["a"]
    assert first.tested_total == 1 and first.untested_remaining == 2

    second = _run(tmp_path, time_budget_s=0)
    assert calls == ["a", "b"]  # never re-checks "a" - it moves on
    assert "still untested" in second.summary()
    assert Path(second.report_path).exists()


def test_a_passing_candidate_is_reported_as_the_replacement(monkeypatch, tmp_path: Path) -> None:
    _stub_world(monkeypatch, tmp_path, verdicts={"a": False, "b": True})
    outcome = _run(tmp_path, time_budget_s=600)
    assert [r.name for r in outcome.passed] == ["b"]
    assert "REPLACEMENT FOUND: b" in outcome.summary()
    assert outcome.leaderboard[0]["name"] == "b"


def test_a_passing_leader_from_an_earlier_run_is_not_called_a_near_miss(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_world(monkeypatch, tmp_path, verdicts={"a": False, "b": True})
    _run(tmp_path, time_budget_s=600)
    text = _run(tmp_path, time_budget_s=0).summary()
    assert "Best so far: b" in text and "not proposed again" in text
    assert "failed on" not in text


def test_a_candidate_with_the_same_trades_takes_the_first_ones_result(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_world(monkeypatch, tmp_path, verdicts={"a": False, "b": False, "c": True})
    monkeypatch.setattr(rs, "entry_signature", lambda c, f: {"a": "s1", "b": "s1"}.get(c.name))
    outcome = _run(tmp_path, time_budget_s=600)
    assert calls == ["a", "c"]
    assert outcome.duplicates_skipped == 1
    assert outcome.untested_remaining == 0 and outcome.tested_total == 3
    assert "made the same trades" in outcome.summary()


def test_near_misses_are_ranked_and_named_in_the_plan(monkeypatch, tmp_path: Path) -> None:
    _stub_world(monkeypatch, tmp_path, verdicts={"a": False})
    outcome = _run(tmp_path, time_budget_s=600)
    text = outcome.summary()
    assert "Closest so far: a" in text and "walk_forward" in text
    assert "-0.500R/call, 20% hit rate" in text  # the bar it has to beat, stated


def test_a_drifted_base_blocks_the_search_loudly_not_silently(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_world(monkeypatch, tmp_path, verdicts={"a": True}, drifted=True)
    outcome = _run(tmp_path, time_budget_s=600)
    assert calls == []
    assert outcome.status == "blocked"
    assert "BLOCKED" in outcome.summary() and "src/x.py" in outcome.summary()


def test_everything_recently_tested_means_an_empty_batch(monkeypatch, tmp_path: Path) -> None:
    # The replacement registry timestamps attempts with its own wall clock. Pin that
    # clock to the scenario's fixed NOW as well, otherwise this test starts depending on
    # the calendar date on which the suite happens to run.
    monkeypatch.setattr("tradedesk_lab.registry.now", lambda: NOW.isoformat())
    calls = _stub_world(monkeypatch, tmp_path, verdicts={"a": False})
    _run(tmp_path, time_budget_s=600)
    again = _run(tmp_path, time_budget_s=600, now=NOW + timedelta(days=1))
    assert calls == ["a"]  # tested yesterday: not re-run until retest_after_days
    assert again.tested_this_run == []
    later = _run(tmp_path, time_budget_s=600, now=NOW + timedelta(days=20))
    assert calls == ["a", "a"]  # ...but it IS re-tested on fresher data later
    assert later.tested_this_run


def _evidence(tmp_path, name: str, failures: list[str], *, scaled: bool = False) -> str:
    import json

    measured = {"losing_streak": 18.0}
    if scaled:
        measured["allowed_losing_streak"] = 23.0
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"kill_criteria": {"failures": failures, "measured": measured}}))
    return str(path)


def test_streak_only_failures_under_the_old_cap_are_retested_first_best_first(tmp_path) -> None:
    space = [_c("new"), _c("s1"), _c("s2"), _c("size"), _c("done")]
    recent = (NOW - timedelta(days=1)).isoformat()

    def row(name, failures, net_r, scaled=False):
        return {
            "started_at": recent,
            "metrics": {"net_r": net_r},
            "artifact_path": _evidence(tmp_path, name, failures, scaled=scaled),
        }

    history = {
        "s1": row("s1", ["losing_streak 18 > allowed 10"], 0.05),
        "s2": row("s2", ["losing_streak 13 > allowed 10"], 0.13),
        "size": row(
            "size", ["sample_size 183 < required 300", "losing_streak 15 > allowed 10"], 0.15
        ),
        "done": row("done", ["losing_streak 18 > allowed 23"], 0.05, scaled=True),
    }
    batch = rs.plan_batch(space, history, now=NOW, retest_after_days=14)
    # Streak-only under the old cap first (best net R first), then untested; a result that
    # also failed sample size, or was already judged under the scaled cap, is left alone.
    assert [c.name for c in batch] == ["s2", "s1", "new"]


def test_select_for_submission_keeps_the_best_per_idea_and_caps_the_run() -> None:
    from tradedesk.self_review.orchestrate import MAX_NEW_DETECTORS_PER_RUN, select_for_submission

    def r(name, net):
        return SimpleNamespace(name=name, net_r=net)

    cands = {
        n: SimpleNamespace(trigger=t)
        for n, t in [("a1", "donchian20"), ("a2", "donchian20"), ("b", "tsmom20"),
                     ("c", "high52_break"), ("d", "ibs_low")]
    }  # fmt: skip
    passed = [r("a1", 0.10), r("a2", 0.135), r("b", 0.07), r("c", 0.09), r("d", 0.05)]
    chosen = [x.name for x in select_for_submission(passed, cands)]
    assert chosen == ["a2", "c", "b"] and len(chosen) == MAX_NEW_DETECTORS_PER_RUN


def test_improvements_of_one_setup_are_one_idea() -> None:
    from tradedesk.self_review.orchestrate import select_for_submission

    cands = {
        "i1": SimpleNamespace(base="r_high52_break_strong_trend_trend_trail"),
        "i2": SimpleNamespace(base="r_high52_break_strong_trend_trend_trail"),
        "j": SimpleNamespace(base="r_donchian55_any_trend_trail"),
    }
    passed = [SimpleNamespace(name=n, net_r=v) for n, v in [("i1", 0.25), ("i2", 0.25), ("j", 0.2)]]
    assert [x.name for x in select_for_submission(passed, cands)] == ["i1", "j"]


def test_is_repo_root_accepts_the_relative_root_the_cli_passes(tmp_path) -> None:
    import os
    from pathlib import Path

    assert rs.is_repo_root(rs.ROOT)
    assert rs.is_repo_root(Path(os.path.relpath(rs.ROOT)))  # relative form, like "."
    assert not rs.is_repo_root(tmp_path)


def _rising_frame(n: int = 80):
    import pandas as pd

    close = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "close": close,
            "high": [c + 0.5 for c in close],
            "adx14": 40.0,
            "plus_di": 30.0,
            "minus_di": 10.0,
        },
        index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )


def test_a_candidate_whose_entries_a_live_setup_takes_already_overlaps_fully() -> None:
    from tradedesk_lab.candidates.rules import RuleCandidate

    d55 = RuleCandidate("donchian55", "strong_trend", "trend_trail")
    d20 = RuleCandidate("donchian20", "strong_trend", "trend_trail")
    frames = {"X": _rising_frame()}
    assert rs.live_overlap(d55, [d20], frames) == 1.0  # a 55-day high is a 20-day high too
    assert rs.live_overlap(d20, [d55], frames) < rs.MAX_LIVE_OVERLAP
    assert rs.live_overlap(d55, [], frames) is None
    assert rs.live_overlap(SimpleNamespace(name="library"), [d20], frames) is None


def test_an_improvement_of_a_rule_setup_is_measured_against_live_setups() -> None:
    from tradedesk_lab.candidates.rules import ImprovedSetup, RuleCandidate

    d20 = RuleCandidate("donchian20", "strong_trend", "trend_trail")
    imp = ImprovedSetup("r_donchian55_strong_trend_trend_trail", "any", "trend_trail")
    frames = {"X": _rising_frame()}
    assert rs.live_overlap(imp, [d20], frames) == 1.0
    hand_written = ImprovedSetup("base_breakout", "any", "trend_trail")
    assert rs.live_overlap(hand_written, [d20], frames) is None


def test_candidates_that_make_the_same_trades_share_one_signature() -> None:
    from tradedesk_lab.candidates.rules import ImprovedSetup, RuleCandidate

    frames = {"X": _rising_frame()}
    base = "r_donchian55_strong_trend_trend_trail"
    rule = RuleCandidate("donchian55", "strong_trend", "trend_trail")
    same = rs.entry_signature(ImprovedSetup(base, "any", "trend_trail"), frames)
    assert same is not None and same == rs.entry_signature(rule, frames)
    other_exit = rs.entry_signature(ImprovedSetup(base, "any", "target_3r"), frames)
    other_entry = rs.entry_signature(RuleCandidate("donchian20", "strong_trend", "trend_trail"),
                                     frames)  # fmt: skip
    assert len({same, other_exit, other_entry}) == 3
    assert rs.entry_signature(ImprovedSetup("base_breakout", "any", "trend_trail"), frames) is None
