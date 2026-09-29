import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradedesk_lab.artifacts import digest
import tradedesk_lab.osr_selector_race as mod
import tradedesk_lab.osr_selection_population as population_mod
import tradedesk_lab.osr_selectors as selectors_mod
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT
from tradedesk_lab.osr_selectors import FEATURE_NAMES

GEOMETRIES = [item.id for item in DEFAULT_OSR_CONTRACT.geometries]
MODES = list(DEFAULT_OSR_CONTRACT.entry_modes)


def _synthetic_population(
    *, n_sessions: int = 78, calls_per_spec_session: int = 6, seed: int = 17
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    sessions = [str(item.date()) for item in pd.bdate_range("2026-01-01", periods=n_sessions)]
    rows = []
    counter = 0
    for session in sessions:
        for mode in MODES:
            latent_values = rng.normal(size=calls_per_spec_session)
            for geometry_id in GEOMETRIES:
                for call_index, latent in enumerate(latent_values):
                    counter += 1
                    values = rng.normal(scale=0.2, size=len(FEATURE_NAMES))
                    values[0] = latent
                    values[1] = latent + rng.normal(scale=0.1)
                    label = int(latent > 0.15)
                    status = "resolved"
                    if call_index == 0 and counter % 11 == 0:
                        status = "unfilled"
                        label = 0
                    if call_index == 1 and counter % 97 == 0:
                        status = "unresolved"
                        label = 0
                    rows.append(
                        {
                            **dict(zip(FEATURE_NAMES, values, strict=True)),
                            "label": label,
                            "net_r": (1.1 if label else -1.0) if status == "resolved" else np.nan,
                            "gross_r": (1.2 if label else -1.0) if status == "resolved" else np.nan,
                            "outcome_status": status,
                            "filled": status == "resolved",
                            "outcome_reason": status,
                            "session": session,
                            "scrip_code": f"NSE_{call_index}",
                            "mode": mode,
                            "geometry_id": geometry_id,
                            "opportunity_id": f"OPP_{mode}_{session}_{call_index}",
                            "decision_at": f"{session}T09:{30 + call_index:02d}:00+05:30",
                        }
                    )
    return pd.DataFrame(rows)


def _fast_settings() -> mod.RaceSettings:
    return mod.RaceSettings(
        outer_splits=2,
        outer_embargo_sessions=2,
        inner_splits=2,
        inner_embargo_sessions=1,
        matched_random_cohorts=5,
        seed=123,
        minimum_scored_outer_folds=1,
        minimum_positive_net_r_fold_share=0.5,
    )


def test_registered_specs_are_exactly_the_frozen_12():
    specs = mod.registered_pipeline_specs()
    assert len(specs) == 12
    assert len({item.id for item in specs}) == 12
    assert {item.mode for item in specs} == set(MODES)
    assert {item.geometry_id for item in specs} == set(GEOMETRIES)
    assert {item.selector_kind for item in specs} == {
        "transparent_additive",
        "regularized_tree",
    }


def test_wilson_matches_the_canonical_baseline():
    assert mod._wilson95_lower(149, 693) == pytest.approx(0.18603500195342146, abs=1e-6)


def test_selection_thresholds_before_ranking_and_deduplicates_symbols():
    scored = pd.DataFrame(
        {
            "session": ["s1"] * 4,
            "scrip_code": ["A", "A", "B", "C"],
            "opportunity_id": ["a1", "a2", "b", "c"],
            "selection_probability": [0.51, 0.9, 0.8, 0.49],
        }
    )
    selected = mod._select_calls(scored, top_k=3)
    assert selected["opportunity_id"].tolist() == ["a2", "b"]
    assert selected["selection_probability"].min() >= 0.50


def test_evaluation_exposes_unfilled_unresolved_and_session_consistency():
    frame = _synthetic_population(n_sessions=2, calls_per_spec_session=3)
    frame = frame.iloc[:6].copy()
    frame.loc[frame.index[0], "outcome_status"] = "unfilled"
    frame.loc[frame.index[0], "label"] = 0
    frame.loc[frame.index[0], "net_r"] = np.nan
    frame.loc[frame.index[1], "outcome_status"] = "unresolved"
    frame.loc[frame.index[1], "label"] = 0
    frame.loc[frame.index[1], "net_r"] = np.nan
    frame["_outer_fold"] = 0
    metrics = mod._evaluate_selection(frame, set(frame["session"]))
    assert metrics["selected_calls"] == 6
    assert metrics["unfilled_calls"] == 1
    assert metrics["unresolved_calls"] == 1
    assert metrics["resolved_fills"] == 4
    assert "sessions_at_or_above_70pct" in metrics
    assert "maximum_drawdown_r" in metrics


def test_matched_random_control_uses_the_same_per_session_call_counts():
    population = _synthetic_population(n_sessions=4, calls_per_spec_session=5)
    population = population.loc[
        (population["mode"] == MODES[0])
        & (population["geometry_id"] == GEOMETRIES[0])
    ]
    selected = population.groupby("session", sort=True).head(2)
    random = mod._matched_random_selection(population, selected, seed=7)
    assert random.groupby("session").size().to_dict() == selected.groupby("session").size().to_dict()
    assert not random.duplicated(["session", "scrip_code"]).any()


def test_development_gates_fail_each_accuracy_and_reliability_requirement():
    passing = {
        "resolved_fills": 150,
        "active_sessions": 50,
        "active_session_coverage": 0.6,
        "strict_success_rate": 0.55,
        "wilson95_lower": 0.45,
        "unresolved_calls": 0,
        "mean_net_r": 0.3,
        "max_single_symbol_win_share": 0.2,
        "folds_with_resolved_calls": 3,
        "positive_mean_net_r_fold_share": 1.0,
    }
    settings = mod.DEFAULT_RACE_SETTINGS
    assert mod._check_development_gates(passing, {"mean_net_r": 0.1}, settings)["passed"]
    overrides = [
        {"resolved_fills": 10},
        {"active_sessions": 10},
        {"active_session_coverage": 0.2},
        {"strict_success_rate": 0.4},
        {"wilson95_lower": 0.3},
        {"unresolved_calls": 1},
        {"mean_net_r": 0.0},
        {"max_single_symbol_win_share": 0.3},
        {"folds_with_resolved_calls": 1},
        {"positive_mean_net_r_fold_share": 0.5},
    ]
    for override in overrides:
        result = mod._check_development_gates({**passing, **override}, {"mean_net_r": 0.1}, settings)
        assert result["passed"] is False
        assert result["failures"]


def test_one_real_selector_spec_is_nested_chronological_and_deterministic():
    population = _synthetic_population()
    spec = mod.registered_pipeline_specs()[0]
    settings = _fast_settings()
    first = mod._run_one_spec(population, spec, settings=settings, keep_selected_rows=True)
    second = mod._run_one_spec(population, spec, settings=settings, keep_selected_rows=True)
    assert first["status"] == "evaluated"
    assert first["outer_folds_scored"] >= 1
    assert first["top_k"][1]["metrics"] == second["top_k"][1]["metrics"]
    for fold in first["fold_diagnostics"]:
        assert fold["train_session_max"] < fold["test_session_min"]
        if fold["scored"]:
            for inner in fold["inner"]["boundaries"]:
                assert inner["train_session_max"] < inner["test_session_min"]


def test_run_race_reports_all_12_specs(monkeypatch):
    population = _synthetic_population(n_sessions=3, calls_per_spec_session=2)

    def fake_trial(_dataset, spec, *, settings, keep_selected_rows=False):
        return {
            "spec_id": spec.id,
            "status": "evaluated",
            "top_k": {
                top_k: {"gate": {"passed": False}, "metrics": {}, "matched_random_control": {}}
                for top_k in mod.TOP_K_POLICIES
            },
        }

    monkeypatch.setattr(mod, "_run_one_spec", fake_trial)
    result = mod.run_selector_race(population, settings=_fast_settings())
    assert result["trial_budget"] == 12
    assert len(result["trials"]) == 12
    assert result["qualified_candidates"] == []
    assert result["evidence_class"] == "consumed_historical_development"


def test_freeze_race_never_changes_the_canonical_baseline(monkeypatch, tmp_path):
    population = _synthetic_population(n_sessions=3, calls_per_spec_session=2)
    output = tmp_path / "lab"
    population_id = "population-1"
    folder = output / "osr/selection_population/runs" / population_id
    folder.mkdir(parents=True)
    calls_path = folder / "calls.csv"
    population.to_csv(calls_path, index=False)
    from tradedesk_lab.artifacts import digest

    (folder / "report.json").write_text(
        json.dumps(
            {
                "id": population_id,
                "source_osr_contract_sha256": DEFAULT_OSR_CONTRACT.sha256,
                "evidence_class": "consumed_historical_development",
                "calls_sha256": digest(calls_path),
                "rows": len(population),
            }
        )
    )
    latest = output / "osr/selection_population/latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps({"id": population_id}))
    monkeypatch.setattr(
        mod,
        "run_selector_race",
        lambda *args, **kwargs: {
            "trial_budget": 12,
            "trials_registered": 12,
            "trials": [],
            "qualified_candidates": [],
            "stress_gates_evaluated": False,
            "evidence_class": "consumed_historical_development",
            "canonical_baseline_strict_success_rate": 149 / 693,
            "canonical_baseline_wilson95_lower": 0.18603500195342146,
            "registry": mod.race_registry(),
        },
    )
    report = mod.freeze_selector_race(tmp_path, output, population_run_id=population_id)
    assert report["baseline_improved"] is False
    assert report["development_candidate_nominated"] is False
    assert report["decision"]["change_canonical_baseline"] is False
    assert report["decision"]["change_live_behavior"] is False


def test_tracked_osr_selector_race_evidence_matches_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/osr-selector-race.json").read_text(encoding="utf-8")
    )

    assert evidence["status"] == "selector_race_rejected"
    assert evidence["milestone"] == 3
    assert evidence["algorithm_evaluated"] is True
    assert evidence["real_run_completed"] is True
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_OSR_CONTRACT.sha256
    assert evidence["population_implementation_sha256"] == digest(
        Path(population_mod.__file__)
    )
    assert evidence["selector_implementation_sha256"] == digest(Path(selectors_mod.__file__))
    assert evidence["race_implementation_sha256"] == digest(Path(mod.__file__))
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_osr_selector_race.py")
    assert evidence["selection_population"]["rows"] == 8091
    assert evidence["selection_population"]["unfilled_calls"] == 720
    assert evidence["selection_population"]["unresolved_calls"] == 68
    assert evidence["real_run"]["trials_evaluated"] == 12
    assert evidence["real_run"]["selected_calls_at_fixed_threshold"] == 0
    assert evidence["real_run"]["qualified_candidates"] == []
    assert evidence["decision"]["change_canonical_baseline"] is False
    assert evidence["decision"]["change_live_behavior"] is False
