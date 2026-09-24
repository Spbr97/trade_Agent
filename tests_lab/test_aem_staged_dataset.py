import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
from tradedesk_lab.aem_staged_dataset import prepare_staged_aem
from tradedesk_lab.artifacts import ROOT


def test_staged_dataset_is_isolated_and_fail_closed(monkeypatch, tmp_path):
    evaluation = [date(2026, 9, 1), date(2026, 9, 2)]
    settings = SimpleNamespace(
        universe=SimpleNamespace(benchmark="NIFTY 50"), risk=SimpleNamespace()
    )
    monkeypatch.setattr("tradedesk_lab.aem_staged_dataset.load_config", lambda root: settings)
    monkeypatch.setattr(
        "tradedesk_lab.aem_staged_dataset.nse_market",
        lambda configured: SimpleNamespace(costs=SimpleNamespace()),
    )
    source = {
        "source_version": "test",
        "sha256": "source-hash",
        "coverage": {
            "NSE_1": {"complete_sessions": 1, "missing_or_incomplete_sessions": ["2026-09-02"]}
        },
    }
    monkeypatch.setattr(
        "tradedesk_lab.aem_staged_dataset.read_staged_aem_source",
        lambda *args, **kwargs: (
            {"NSE_1": pd.DataFrame()},
            {"NSE_1": pd.DataFrame()},
            {"NSE_1": "ONE"},
            evaluation,
            evaluation,
            source,
        ),
    )
    events = pd.DataFrame(
        [
            {
                "event_id": "ONE:2026-09-01",
                "session_date": "2026-09-01",
                "symbol": "ONE",
                "decision": "TRADE",
                "status": "resolved",
                "strict_success": True,
                "target_hit": True,
                "label": 1,
                "net_pnl": 20.0,
                "net_r": 0.5,
                "reasons": [],
            }
        ]
    )
    monkeypatch.setattr(
        "tradedesk_lab.aem_staged_dataset.build_events",
        lambda *args, **kwargs: (events, {"incomplete_session": 1}),
    )

    result = prepare_staged_aem(ROOT, tmp_path, plan_id="frozen-plan")

    latest = json.loads((tmp_path / "aem_staged/latest.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (tmp_path / "aem_staged/datasets" / latest["id"] / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.manifest["id"] == latest["id"] == manifest["id"]
    assert manifest["eligible_for_live"] is False
    assert manifest["eligibility_assessment"]["status"] == "not_assessed"
    assert manifest["resolved_trades"] == 1
    assert manifest["strict_success_rate"] == 1.0
    assert manifest["accuracy_protocol"]["reported_top_k_policies"] == [1, 2, 3]
    assert len(manifest["accuracy_scorecard"]["core"]) == 12
    assert len(manifest["accuracy_scorecard"]["promotion_gates"]) == 4
    assert manifest["accuracy_scorecard"]["all_promotion_gates_pass"] is False
    assert not (tmp_path / "aem/latest.json").exists()
