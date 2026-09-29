import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tradedesk_lab.ssm_mechanism as mod
from tradedesk_lab.artifacts import digest
from tradedesk_lab.ssm_contract import DEFAULT_SSM_CONTRACT


def _session_frame(session: str, *, missing_at: str | None = None) -> pd.DataFrame:
    index = pd.date_range(f"{session} 09:15", periods=375, freq="1min", tz=mod.IST)
    close = 100.0 + np.arange(375, dtype=float) * 0.001
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.01,
            "low": close - 0.01,
            "close": close,
            "volume": np.full(375, 1_000.0),
        },
        index=index,
    )
    if missing_at is not None:
        frame = frame.drop(pd.Timestamp(f"{session} {missing_at}", tz=mod.IST))
    return frame


def _direct_panel(*, session_count: int = 90, code_count: int = 31) -> mod.SlotReturnPanel:
    rng = np.random.default_rng(17)
    sessions = tuple(str(day.date()) for day in pd.bdate_range("2026-01-01", periods=session_count))
    codes = tuple(f"NSE_{number:02d}" for number in range(code_count))
    slot_factor = rng.normal(0.0, 0.002, (len(DEFAULT_SSM_CONTRACT.slot_starts), code_count))
    values = np.empty((session_count, len(DEFAULT_SSM_CONTRACT.slot_starts), code_count))
    for session_position in range(session_count):
        values[session_position] = slot_factor + rng.normal(
            0.0, 0.00008, slot_factor.shape
        )
    return mod.SlotReturnPanel(
        codes=codes,
        symbols={code: code for code in codes},
        sessions=sessions,
        slot_starts=DEFAULT_SSM_CONTRACT.slot_starts,
        values=values,
        source_exclusions=(),
    )


def _eligible_population(panel: mod.SlotReturnPanel, evaluation_sessions: tuple[str, ...]):
    rows = []
    for session in evaluation_sessions:
        for code in panel.codes:
            for slot_start in panel.slot_starts:
                rows.append(
                    {
                        "session": session,
                        "scrip_code": code,
                        "slot_start": slot_start,
                        "mechanism_eligible": True,
                    }
                )
    return pd.DataFrame(rows)


def test_mechanism_contract_is_frozen_and_cannot_weaken_controls():
    contract = mod.DEFAULT_SSM_MECHANISM_CONTRACT
    assert contract.lag_sessions == tuple(range(1, 41))
    assert contract.adjacent_slot_offsets == (-1, 1)
    assert contract.shuffle_repetitions == 128
    assert contract.minimum_candidates_per_group == 31
    assert len(contract.sha256) == 64
    with pytest.raises(ValueError, match="at least 100"):
        replace(contract, shuffle_repetitions=99)
    with pytest.raises(ValueError, match="cannot exceed"):
        replace(contract, maximum_shuffle_empirical_p=0.10)


def test_slot_panel_uses_exact_half_hours_and_records_incomplete_slot():
    session = "2026-01-05"
    complete = mod.build_slot_return_panel(
        {"NSE_1": {session: _session_frame(session)}}, {"NSE_1": "ONE"}
    )
    assert complete.values.shape == (1, 11, 1)
    assert np.isfinite(complete.values).all()
    assert not complete.source_exclusions

    incomplete = mod.build_slot_return_panel(
        {"NSE_1": {session: _session_frame(session, missing_at="10:20")}},
        {"NSE_1": "ONE"},
    )
    assert np.isnan(incomplete.values[0, 1, 0])
    assert len(incomplete.source_exclusions) == 1
    assert incomplete.source_exclusions[0]["slot_start"] == "10:15"


def test_decision_population_accounts_for_every_coordinate_and_exclusion():
    panel = _direct_panel(session_count=21)
    evaluation = [panel.sessions[-1]]
    included = {code: set(evaluation) for code in panel.codes}
    included[panel.codes[-1]] = set()

    population = mod.build_decision_population(
        panel,
        evaluation_sessions=evaluation,
        included_sessions=included,
    )

    assert len(population) == 31 * 11
    assert int(population["mechanism_eligible"].sum()) == 30 * 11
    excluded = population.loc[~population["mechanism_eligible"]]
    assert set(excluded["exclusion_reason"]) == {"source_universe_pair_excluded"}
    eligible = population.loc[population["mechanism_eligible"]]
    assert eligible["historical_same_slot_sessions"].eq(20).all()
    assert eligible["candidate_excluded_peer_count"].eq(30).all()
    assert population.duplicated(["session", "scrip_code", "slot_start"]).sum() == 0


def test_same_slot_mechanism_beats_adjacent_and_shuffled_controls():
    panel = _direct_panel()
    evaluation = panel.sessions[-50:]
    population = _eligible_population(panel, evaluation)

    diagnostics, shuffled, summary = mod.estimate_same_slot_mechanism(panel, population)

    assert diagnostics["lag"].tolist() == list(range(1, 41))
    assert len(shuffled) == 128
    assert summary["same_slot_lag1_coefficient"] > 0.95
    assert summary["same_slot_mean_lag1_40_coefficient"] > 0.95
    assert summary["same_slot_positive_lag_fraction"] == 1.0
    assert summary["same_slot_mean_lag1_40_coefficient"] > summary[
        "strongest_adjacent_mean_lag1_40_coefficient"
    ]
    assert summary["same_slot_mean_lag1_40_coefficient"] > summary[
        "shuffle_mean_lag1_40_p95"
    ]
    assert summary["lag1_top_bucket_gross_excess_return"] > 0
    assert summary["passed"] is True
    assert all(summary["gates"].values())


def test_freeze_writes_auditable_research_only_artifacts(monkeypatch, tmp_path):
    panel = _direct_panel(session_count=21)
    population = pd.DataFrame(
        [
            {
                "session": panel.sessions[-1],
                "scrip_code": panel.codes[0],
                "slot_start": panel.slot_starts[0],
                "mechanism_eligible": True,
            }
        ]
    )
    diagnostics = pd.DataFrame([{"lag": 1, "same_slot_coefficient": 0.1}])
    shuffled = pd.DataFrame([{"repetition": 0, "mean_lag1_40_coefficient": 0.0}])
    metadata = {
        "source_dataset_id": "dataset",
        "source_universe_audit_id": "audit",
        "source_sha256": "source",
        "source_sessions": 21,
        "evaluation_sessions": 1,
        "universe_symbols": 31,
        "planned_decisions": 1,
        "eligible_decisions": 1,
        "excluded_decisions": 0,
        "exclusion_reason_counts": {},
        "slot_source_exclusions": 0,
        "evidence_class": "consumed_historical_development",
        "mechanism": {"passed": True, "gates": {"example": True}},
    }
    monkeypatch.setattr(
        mod,
        "build_real_mechanism_check",
        lambda *args, **kwargs: (panel, population, diagnostics, shuffled, metadata),
    )

    report = mod.freeze_real_mechanism_check(tmp_path, tmp_path / "lab")

    assert report["status"] == "mechanism_present_selector_race_allowed"
    assert report["mechanism_evaluated"] is True
    assert report["algorithm_evaluated"] is False
    assert report["baseline_improved"] is False
    assert report["decision"]["register_candidate"] is False
    assert report["decision"]["change_live_behavior"] is False
    target = tmp_path / f"lab/ssm/mechanism/runs/{report['id']}"
    assert (target / "decisions.csv").is_file()
    assert (target / "lag_diagnostics.csv").is_file()
    assert (target / "shuffled_controls.csv").is_file()
    assert json.loads((tmp_path / "lab/ssm/mechanism/latest.json").read_text())["id"] == report[
        "id"
    ]


def test_tracked_mechanism_evidence_matches_frozen_sources():
    root = Path(mod.__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/ssm-mechanism.json").read_text("utf-8"))

    assert evidence["status"] == "mechanism_absent_stop"
    assert evidence["mechanism_passed"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["planned_decisions"] == 66_000
    assert evidence["eligible_decisions"] == 65_989
    assert evidence["excluded_decisions"] == 11
    assert evidence["ssm_contract_sha256"] == DEFAULT_SSM_CONTRACT.sha256
    assert (
        evidence["mechanism_contract_sha256"]
        == mod.DEFAULT_SSM_MECHANISM_CONTRACT.sha256
    )
    assert evidence["implementation_sha256"] == digest(Path(mod.__file__))
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_ssm_mechanism.py")
    assert evidence["cli_sha256"] == digest(root / "tradedesk_lab/__main__.py")
    assert evidence["decision"]["proceed_to_selector_race"] is False
    assert evidence["decision"]["change_canonical_baseline"] is False
    assert evidence["decision"]["change_live_behavior"] is False
