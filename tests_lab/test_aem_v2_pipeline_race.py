import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_pipeline_race import (
    ENTRY_MODE_GROUPS,
    MODEL_KINDS,
    TOP_K_POLICIES,
    _check_development_gates,
    _prepare_pipeline_frame,
    _random_selection,
    _wilson95_lower,
    registered_pipeline_specs,
    run_pipeline_race,
    selected_fills_for_spec,
)
from tradedesk_lab.aem_v2_precision_ladder import REGISTERED_FEATURE_NAMES

GEOMETRY_IDS = [g.id for g in DEFAULT_AEM_V2_CONTRACT.geometries]
MODES = ["anticipatory_impulse", "confirmed_pullback", "breakout_retest"]
SYMBOLS = [f"NSE_{i}" for i in range(8)]


def _benign_values() -> dict[str, float]:
    values = dict.fromkeys(REGISTERED_FEATURE_NAMES, 0.01)
    values.update(
        modeled_round_trip_cost_pct=0.0005,
        impact_proxy=0.05,
        median_turnover_20d=50_000_000.0,
        causal_level_distance=0.02,
        daily_extension_atr=0.5,
        limit_distance=0.001,
        time_remaining_minutes=30.0,
    )
    return values


def _synthetic_dataset(*, n_sessions: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    sessions = [str(d.date()) for d in pd.bdate_range("2026-01-01", periods=n_sessions)]
    rows = []
    counter = 0
    for session in sessions:
        for mode in MODES:
            for geometry_id in GEOMETRY_IDS:
                for _ in range(3):
                    counter += 1
                    features = _benign_values()
                    features["return_5m"] = float(rng.normal(0, 0.02))
                    logit = 0.9 * features["return_5m"] / 0.02
                    prob = 1 / (1 + np.exp(-logit))
                    label = int(rng.uniform(0, 1) < prob)
                    net_r = float(rng.normal(1.5 if label else -1.0, 0.3))
                    rows.append(
                        {
                            **features,
                            "label": label,
                            "net_r": net_r,
                            "gross_r": net_r,
                            "session": session,
                            "scrip_code": SYMBOLS[counter % len(SYMBOLS)],
                            "mode": mode,
                            "geometry_id": geometry_id,
                            "opportunity_id": f"OPP_{counter}",
                            "decision_at": f"{session}T09:30:00+05:30",
                        }
                    )
    return pd.DataFrame(rows)


def test_registered_pipeline_specs_are_exactly_24_and_within_budget():
    specs = registered_pipeline_specs()
    assert len(specs) == 24
    assert len({spec.id for spec in specs}) == 24
    assert {spec.entry_mode_group for spec in specs} == set(ENTRY_MODE_GROUPS)
    assert {spec.geometry_id for spec in specs} == set(GEOMETRY_IDS)
    assert {spec.model_kind for spec in specs} == set(MODEL_KINDS)


def test_wilson95_lower_matches_the_known_aem_v1_baseline_value():
    assert _wilson95_lower(149, 693) == pytest.approx(0.18603500195342146, abs=1e-6)


def test_prepare_pipeline_frame_filters_by_mode_and_geometry():
    dataset = _synthetic_dataset(n_sessions=2)
    from tradedesk_lab.aem_v2_pipeline_race import PipelineSpec

    spec = PipelineSpec(
        id="x", entry_mode_group="anticipatory_impulse", geometry_id=GEOMETRY_IDS[0], model_kind="x"
    )
    subset = _prepare_pipeline_frame(dataset, spec)
    assert (subset["mode"] == "anticipatory_impulse").all()
    assert (subset["geometry_id"] == GEOMETRY_IDS[0]).all()

    pooled_spec = PipelineSpec(
        id="y", entry_mode_group="pooled", geometry_id=GEOMETRY_IDS[0], model_kind="x"
    )
    pooled = _prepare_pipeline_frame(dataset, pooled_spec)
    assert set(pooled["mode"]) == set(MODES)
    assert (pooled["geometry_id"] == GEOMETRY_IDS[0]).all()


def test_random_selection_respects_top_k_and_symbol_dedup():
    frame = pd.DataFrame(
        {
            "session": ["s1"] * 4,
            "scrip_code": ["A", "A", "B", "C"],
            "opportunity_id": ["1", "2", "3", "4"],
        }
    )
    picked = _random_selection(frame, top_k=2, seed=1)
    assert len(picked) == 2
    assert picked["scrip_code"].is_unique


def test_check_development_gates_flags_each_condition():
    passing = {
        "resolved_fills": 200,
        "active_sessions": 50,
        "active_session_coverage": 0.6,
        "strict_success_rate": 0.55,
        "wilson95_lower": 0.45,
        "mean_net_r": 0.2,
        "max_single_symbol_win_share": 0.2,
    }
    random_metrics = {"mean_net_r": -0.05}
    result = _check_development_gates(passing, random_metrics)
    assert result["passed"] is True
    assert result["failures"] == ()

    cases = {
        "resolved_fills": {"resolved_fills": 10},
        "active_sessions": {"active_sessions": 5},
        "active_session_coverage": {"active_session_coverage": 0.1},
        "strict_success_rate": {"strict_success_rate": 0.3},
        "wilson95_lower": {"wilson95_lower": 0.1},
        "mean_net_r": {"mean_net_r": -0.5},
        "max_single_symbol_win_share": {"max_single_symbol_win_share": 0.9},
    }
    for _name, override in cases.items():
        broken = {**passing, **override}
        result = _check_development_gates(broken, random_metrics)
        assert result["passed"] is False
        assert result["failures"]

    tiny_advantage = _check_development_gates(passing, {"mean_net_r": 0.15})
    assert tiny_advantage["passed"] is False
    assert "insufficient_advantage_over_matched_random" in tiny_advantage["failures"]


def test_run_pipeline_race_evaluates_every_registered_spec():
    dataset = _synthetic_dataset()
    result = run_pipeline_race(dataset, splits=3, embargo=2, seed=42)

    assert result["trial_budget"] == 24
    assert len(result["trials"]) == 24
    evaluated = [t for t in result["trials"] if t["status"] == "evaluated"]
    assert len(evaluated) >= 20

    sample = evaluated[0]
    assert set(sample["top_k"]) == {1, 2, 3}
    for top_k in (1, 2, 3):
        report = sample["top_k"][top_k]
        assert set(report) == {"metrics", "matched_random_control", "gate"}
        assert isinstance(report["gate"]["failures"], tuple)
    assert result["stress_gates_evaluated"] is False


def test_precision_ladder_shows_a_real_advantage_over_matched_random_somewhere():
    dataset = _synthetic_dataset()
    result = run_pipeline_race(dataset, splits=3, embargo=2, seed=42)
    advantages = [
        trial["top_k"][1]["gate"]["random_advantage_r"]
        for trial in result["trials"]
        if trial.get("status") == "evaluated"
    ]
    assert any(advantage > 0 for advantage in advantages)


def test_selected_fills_for_spec_is_deterministic_and_matches_the_race_metrics():
    dataset = _synthetic_dataset()
    result = run_pipeline_race(dataset, splits=3, embargo=2, seed=42)
    evaluated = next(t for t in result["trials"] if t["status"] == "evaluated")
    spec_id = evaluated["spec_id"]

    for top_k in TOP_K_POLICIES:
        selected = selected_fills_for_spec(
            dataset, spec_id, top_k=top_k, splits=3, embargo=2, seed=42
        )
        assert len(selected) == evaluated["top_k"][top_k]["metrics"]["resolved_fills"]
        again = selected_fills_for_spec(
            dataset, spec_id, top_k=top_k, splits=3, embargo=2, seed=42
        )
        assert sorted(selected["opportunity_id"]) == sorted(again["opportunity_id"])


def test_selected_fills_for_spec_rejects_unregistered_id_and_bad_top_k():
    dataset = _synthetic_dataset(n_sessions=2)
    with pytest.raises(ValueError):
        selected_fills_for_spec(dataset, "not_a_real_spec", top_k=1)
    real_id = registered_pipeline_specs()[0].id
    with pytest.raises(ValueError):
        selected_fills_for_spec(dataset, real_id, top_k=99)


def test_freeze_pipeline_race_reads_the_frozen_dataset_and_writes_a_report(tmp_path):
    from tradedesk_lab.aem_v2_pipeline_race import freeze_pipeline_race

    dataset = _synthetic_dataset(n_sessions=30)
    dataset_run_id = "dataset-run-1"
    folder = tmp_path / "lab/aem_v2/development_dataset/runs" / dataset_run_id
    folder.mkdir(parents=True)
    dataset.to_csv(folder / "events.csv", index=False)
    (folder / "report.json").write_text(
        json.dumps({"id": dataset_run_id, "resolved_rows": len(dataset)})
    )
    (tmp_path / "lab/aem_v2/development_dataset").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab/aem_v2/development_dataset/latest.json").write_text(
        json.dumps({"id": dataset_run_id})
    )

    report = freeze_pipeline_race(root=tmp_path, output=tmp_path / "lab", splits=3, embargo=2)

    assert report["milestone"] == 4
    assert report["development_dataset_run_id"] == dataset_run_id
    assert report["trial_budget"] == 24
    assert report["decision"]["change_live_behavior"] is False
    latest = json.loads((tmp_path / "lab/aem_v2/pipeline_race/latest.json").read_text())
    written = json.loads(Path(latest["path"]).read_text())
    assert written["id"] == report["id"]
