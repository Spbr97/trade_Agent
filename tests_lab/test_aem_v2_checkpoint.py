import json
from pathlib import Path

from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import DEFAULT_EVENT_ENGINE_CONTRACT
from tradedesk_lab.artifacts import digest


def test_milestone_one_evidence_matches_engine_and_test_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/aem-v2-engine.json").read_text(encoding="utf-8"))

    assert evidence["status"] == "causal_engine_implemented_not_evaluated"
    assert evidence["milestone"] == 1
    assert evidence["opportunity_engine_implemented"] is True
    assert evidence["outcome_engine_implemented"] is True
    assert evidence["selector_implemented"] is False
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_AEM_V2_CONTRACT.sha256
    assert evidence["event_contract_sha256"] == DEFAULT_EVENT_ENGINE_CONTRACT.sha256
    assert evidence["implementation_sha256"] == digest(
        root / "tradedesk_lab/aem_v2_events.py"
    )
    assert evidence["tests_sha256"] == digest(root / "tests_lab/test_aem_v2_events.py")
    assert evidence["decision"]["change_live_behavior"] is False
