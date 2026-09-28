from decimal import Decimal

import pandas as pd
import pytest
import test_aem_v2_development_experiment as dev_helpers
import tradedesk_lab.aem_v2_development_experiment as dev_mod
import tradedesk_lab.aem_v2_pipeline_race as race_mod
import tradedesk_lab.aem_v2_stress_gates as mod
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_PROTOCOL


class _FakeRoundTrip:
    def __init__(self, total: Decimal) -> None:
        self.total = total

    def model_copy(self, *, update):
        return _FakeRoundTrip(update["total"])


class _FakeBaseCosts:
    slippage_pct = Decimal("0.0005")

    def round_trip_cost(self, **kwargs):
        return _FakeRoundTrip(Decimal("10"))


def test_cost_stress_scales_charges_and_slippage_independently():
    stressed = mod._CostStress(
        _FakeBaseCosts(), charge_multiplier=Decimal("1.5"), slippage_multiplier=Decimal("2")
    )
    assert stressed.slippage_pct == Decimal("0.0010")
    assert stressed.round_trip_cost(x=1).total == Decimal("15")


def test_stress_stats_computes_rates_and_rejects_unexpected_status():
    rows = [
        {"status": "resolved", "strict_success": True, "net_r": 1.0},
        {"status": "resolved", "strict_success": False, "net_r": -1.0},
        {"status": "missed_fill", "strict_success": None, "net_r": None},
    ]
    stats = mod._stress_stats(rows)
    assert stats["attempts"] == 3
    assert stats["resolved_fills"] == 2
    assert stats["no_fills"] == 1
    assert stats["strict_successes"] == 1
    assert stats["strict_success_rate"] == pytest.approx(0.5)
    assert stats["mean_net_r"] == pytest.approx(0.0)

    with pytest.raises(ValueError, match="unresolved_stress_outcomes"):
        mod._stress_stats([{"status": "unfilled", "net_r": None}])


def _real_development_dataset(monkeypatch, tmp_path):
    daily, minute, symbols, included = dev_helpers._build_environment()
    dev_helpers._patch_common(monkeypatch, daily, minute, symbols, included)
    report = dev_mod.freeze_development_dataset(root=tmp_path, output=tmp_path / "lab")
    return report


def test_run_stress_gates_replays_real_selected_fills_and_reports_every_case(
    monkeypatch, tmp_path
):
    report = _real_development_dataset(monkeypatch, tmp_path)
    assert report["resolved_rows"] >= 1
    dataset_run_id = report["id"]
    events = pd.read_csv(
        tmp_path / f"lab/aem_v2/development_dataset/runs/{dataset_run_id}/events.csv"
    )
    spec_id = f"{events.iloc[0]['mode']}__{events.iloc[0]['geometry_id']}__precision_ladder"

    monkeypatch.setattr(
        race_mod,
        "_load_development_dataset",
        lambda output, run_id: (dataset_run_id, events),
    )
    monkeypatch.setattr(mod, "_load_development_dataset", race_mod._load_development_dataset)
    monkeypatch.setattr(
        mod, "selected_fills_for_spec", lambda dataset, sid, **kw: dataset
    )

    result = mod.run_stress_gates(
        root=tmp_path, output=tmp_path / "lab", spec_id=spec_id, top_k=1
    )

    assert result["selected_fills"] == len(events)
    assert set(result["cases"]) == set(DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases)
    for name in DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases:
        assert result["cases"][name]["mean_net_r"] is not None
    # Real, measured: inflating costs must never IMPROVE the worst-case mean net R.
    stressed_mean = result["cases"]["costs_1p50x"]["mean_net_r"]
    base_mean = result["cases"]["base_costs"]["mean_net_r"]
    assert stressed_mean <= base_mean
    assert isinstance(result["passed"], bool)


def test_run_stress_gates_raises_when_nothing_was_selected(monkeypatch, tmp_path):
    report = _real_development_dataset(monkeypatch, tmp_path)
    events = pd.read_csv(
        tmp_path / f"lab/aem_v2/development_dataset/runs/{report['id']}/events.csv"
    )
    monkeypatch.setattr(
        mod, "_load_development_dataset", lambda output, run_id: (report["id"], events)
    )
    monkeypatch.setattr(
        mod, "selected_fills_for_spec", lambda dataset, sid, **kw: dataset.iloc[0:0]
    )

    with pytest.raises(ValueError, match="selected no fills"):
        mod.run_stress_gates(root=tmp_path, output=tmp_path / "lab", spec_id="anything", top_k=1)


def test_freeze_stress_gates_writes_artifacts(monkeypatch, tmp_path):
    fake_result = {
        "spec_id": "s",
        "top_k": 1,
        "dataset_run_id": "d1",
        "selected_fills": 5,
        "cases": {
            name: {"mean_net_r": 0.3} for name in DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases
        },
        "minimum_mean_net_r": 0.3,
        "passed": True,
        "mandatory_stress_cases": list(DEFAULT_AEM_V2_PROTOCOL.mandatory_stress_cases),
    }
    monkeypatch.setattr(mod, "run_stress_gates", lambda *a, **kw: fake_result)

    report = mod.freeze_stress_gates(
        root=tmp_path, output=tmp_path / "lab", spec_id="s", top_k=1
    )

    assert report["status"] == "stress_gates_evaluated"
    assert report["decision"]["register_candidate"] is True
    assert report["decision"]["change_live_behavior"] is False
    latest = pd.read_json(tmp_path / "lab/aem_v2/stress_gates/latest.json", typ="series")
    assert latest["id"] == report["id"]
