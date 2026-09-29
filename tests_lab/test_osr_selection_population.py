import json
from pathlib import Path

import pandas as pd
import pytest

import tradedesk_lab.osr_selection_population as mod
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT


def _features(value: float = 1.0) -> dict[str, float]:
    return {name: value for name in mod.FEATURE_NAMES}


def _write_source(tmp_path: Path) -> tuple[Path, str]:
    output = tmp_path / "lab"
    run_id = "development-run"
    folder = output / "osr/development_dataset/runs" / run_id
    folder.mkdir(parents=True)
    events = pd.DataFrame(
        [
            {
                **_features(),
                "label": 1,
                "net_r": 1.2,
                "gross_r": 1.3,
                "session": "2026-01-02",
                "scrip_code": "NSE_1",
                "mode": "gap_down_reclaim",
                "geometry_id": "reclaim_30_22_20",
                "opportunity_id": "OPP_1",
                "decision_at": "2026-01-02T09:35:00+05:30",
            }
        ]
    )
    events.to_csv(folder / "events.csv", index=False)
    exclusions = [
        {
            "scrip_code": "NSE_1",
            "session": "2026-01-02",
            "opportunity_id": "OPP_1",
            "geometry_id": "reclaim_40_28_35",
            "reason": "unfilled:entry_expired",
        },
        {
            "scrip_code": "NSE_2",
            "session": "2026-01-03",
            "opportunity_id": "OPP_2",
            "geometry_id": "reclaim_30_22_20",
            "reason": "unresolved:invalid_geometry",
        },
    ]
    (folder / "excluded_events.json").write_text(json.dumps(exclusions), encoding="utf-8")
    (folder / "report.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "resolved_rows": 1,
                "source_dataset_id": "source-dataset",
                "source_universe_audit_id": "source-audit",
                "osr_contract_sha256": DEFAULT_OSR_CONTRACT.sha256,
                "evidence_class": "consumed_historical_development",
            }
        ),
        encoding="utf-8",
    )
    pointer = output / "osr/development_dataset/latest.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"id": run_id}), encoding="utf-8")
    return output, run_id


def test_outcome_fields_are_not_registered_features():
    forbidden = {"label", "net_r", "gross_r", "outcome_status", "filled", "outcome_reason"}
    assert len(mod.FEATURE_NAMES) == 30
    assert forbidden.isdisjoint(mod.FEATURE_NAMES)


def test_build_population_restores_known_and_missing_exclusions(monkeypatch, tmp_path):
    output, run_id = _write_source(tmp_path)
    recovered = {
        "OPP_2": {
            "features": _features(2.0),
            "session": "2026-01-03",
            "scrip_code": "NSE_2",
            "mode": "opening_low_sweep_reclaim",
            "decision_at": "2026-01-03T10:00:00+05:30",
        }
    }
    monkeypatch.setattr(mod, "_recover_missing_features", lambda *args: recovered)

    frame, metadata = mod.build_selection_population(
        tmp_path, output, dataset_run_id=run_id
    )

    assert len(frame) == 3
    assert frame[list(mod.IDENTITY_COLUMNS)].duplicated().sum() == 0
    assert set(frame["outcome_status"]) == {"resolved", "unfilled", "unresolved"}
    assert frame.loc[frame["outcome_status"] != "resolved", "label"].eq(0).all()
    assert metadata["resolved_calls"] == 1
    assert metadata["unfilled_calls"] == 1
    assert metadata["unresolved_calls"] == 1
    assert metadata["strict_success_rate_all_calls"] == pytest.approx(1 / 3)
    assert metadata["strict_success_rate_resolved"] == 1.0
    restored = frame.loc[frame["opportunity_id"] == "OPP_2"].iloc[0]
    assert restored[mod.FEATURE_NAMES[0]] == 2.0
    assert restored["mode"] == "opening_low_sweep_reclaim"


def test_invalid_exclusion_reason_fails_closed(monkeypatch, tmp_path):
    output, run_id = _write_source(tmp_path)
    folder = output / "osr/development_dataset/runs" / run_id
    exclusions = json.loads((folder / "excluded_events.json").read_text())
    exclusions[0]["reason"] = "feature_computation_failed:bad"
    (folder / "excluded_events.json").write_text(json.dumps(exclusions))
    monkeypatch.setattr(
        mod,
        "_recover_missing_features",
        lambda *args: {
            "OPP_2": {
                "features": _features(),
                "session": "2026-01-03",
                "scrip_code": "NSE_2",
                "mode": "opening_low_sweep_reclaim",
                "decision_at": "2026-01-03T10:00:00+05:30",
            }
        },
    )
    with pytest.raises(ValueError, match="unsupported OSR exclusion reason"):
        mod.build_selection_population(tmp_path, output, dataset_run_id=run_id)


def test_freeze_population_writes_research_only_artifact(monkeypatch, tmp_path):
    output, run_id = _write_source(tmp_path)
    monkeypatch.setattr(
        mod,
        "_recover_missing_features",
        lambda *args: {
            "OPP_2": {
                "features": _features(),
                "session": "2026-01-03",
                "scrip_code": "NSE_2",
                "mode": "opening_low_sweep_reclaim",
                "decision_at": "2026-01-03T10:00:00+05:30",
            }
        },
    )

    report = mod.freeze_selection_population(tmp_path, output, dataset_run_id=run_id)

    assert report["status"] == "selection_population_frozen_not_evaluated"
    assert report["algorithm_evaluated"] is False
    assert report["baseline_improved"] is False
    assert report["decision"]["change_live_behavior"] is False
    target = output / "osr/selection_population/runs" / report["id"]
    assert len(pd.read_csv(target / "calls.csv")) == 3
    latest = json.loads((output / "osr/selection_population/latest.json").read_text())
    assert latest["id"] == report["id"]
