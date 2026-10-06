from __future__ import annotations

from tradedesk.exit_contract_race import evaluate_exit_contract_race


def _record(
    base: str,
    version: str,
    *,
    label: int,
    net_r: float,
    outcome_state: str = "resolved_call",
) -> dict:  # type: ignore[type-arg]
    return {
        "signal_id": f"{base}::{version}",
        "market": "nse",
        "symbol": base,
        "setup": "base_breakout",
        "armed_on": "2026-09-01",
        "entry": 100.0,
        "stop": 95.0,
        "source": "live",
        "evidence_class": "qualified_call",
        "contract_version": version,
        "outcome_state": outcome_state,
        "label": label if outcome_state == "resolved_call" else None,
        "gross_r": 0.75 if label else -1.0,
        "net_r": net_r,
        "after_tax_r": None,
        "holding_sessions": 1 if version == "quick-profit-v1" else 4,
    }


def test_exit_race_uses_only_complete_identical_entry_pairs() -> None:
    records = [
        _record("A", "quick-profit-v1", label=1, net_r=0.6),
        _record("A", "swing-v1", label=0, net_r=-1.1),
        _record("B", "quick-profit-v1", label=1, net_r=0.6),  # missing swing
        _record("C", "quick-profit-v1", label=0, net_r=-1.1),
        _record("C", "swing-v1", label=1, net_r=1.8, outcome_state="pending_call"),
    ]
    report = evaluate_exit_contract_race(records, market="nse")
    assert report["paired_mature_entries"] == 1
    assert report["quick_profit"]["n"] == report["swing"]["n"] == 1
    assert report["exclusions"] == {"missing_pair": 1, "not_both_mature_valid": 1}
    assert report["entries_are_paired"] is True


def test_higher_quick_hit_rate_is_not_a_pass_when_net_expectancy_is_worse() -> None:
    records = []
    for i in range(3):
        records.extend(
            (
                _record(str(i), "quick-profit-v1", label=1, net_r=-0.1),
                _record(
                    str(i),
                    "swing-v1",
                    label=1 if i == 0 else 0,
                    net_r=1.0 if i == 0 else -0.4,
                ),
            )
        )
    report = evaluate_exit_contract_race(records, market="nse")
    assert report["quick_profit"]["strict_accuracy"] == 1.0
    assert report["swing"]["strict_accuracy"] == 1 / 3
    assert report["quick_profit"]["mean_net_r"] < report["swing"]["mean_net_r"]
    assert report["quick_profit_candidate_for_further_evaluation"] is False
    assert report["higher_hit_rate_alone_is_never_a_pass"] is True


def test_quick_candidate_requires_both_accuracy_and_positive_relative_net_r() -> None:
    records = []
    for i in range(4):
        records.extend(
            (
                _record(str(i), "quick-profit-v1", label=1 if i < 3 else 0, net_r=0.3),
                _record(str(i), "swing-v1", label=1 if i < 2 else 0, net_r=0.1),
            )
        )
    report = evaluate_exit_contract_race(records, market="nse")
    assert report["quick_profit_candidate_for_further_evaluation"] is True
    assert report["promotion_authorized"] is False
