from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.bse_accuracy_quick_profit import (
    CANDIDATE_RULES,
    DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
    QuickProfitRecord,
    holm_adjusted_pvalues,
    one_sided_sign_flip_pvalue,
    resolve_quick_profit_call,
    summarize_cohort,
)

from tradedesk.config import load_config
from tradedesk.markets.market import bse_market


def _bars(*, both_hit: bool = False) -> pd.DataFrame:
    index = pd.date_range("2026-10-05", periods=4, freq="B", tz="Asia/Kolkata")
    if both_hit:
        high = [106.0, 101.0, 101.0, 101.0]
        low = [89.0, 99.0, 99.0, 99.0]
    else:
        high = [106.0, 101.0, 101.0, 101.0]
        low = [99.0, 99.0, 99.0, 99.0]
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": high,
            "low": low,
            "close": [105.0, 100.0, 100.0, 100.0],
            "volume": [1000, 1000, 1000, 1000],
        },
        index=index,
    )


def _row(rule: str = "rsi2_dip_ema50") -> dict[str, object]:
    return {
        "call_id": f"{rule}:BSE_1:2026-10-02",
        "rule": rule,
        "scrip_code": "BSE_1",
        "armed_on": "2026-10-02",
        "atr_at_arm": 10.0,
    }


def test_protocol_is_one_transported_geometry_and_is_bse_only() -> None:
    protocol = DEFAULT_BSE_QUICK_PROFIT_PROTOCOL
    assert protocol.market == "bse"
    assert protocol.entry_mode == "next_session_open"
    assert (protocol.stop_atr, protocol.target_r, protocol.max_hold_sessions) == (1.0, 0.5, 3)
    assert protocol.activation_date == "2026-10-04"
    assert len(protocol.sha256) == 64


def test_resolver_uses_stop_first_when_one_bar_touches_both_barriers() -> None:
    costs = bse_market(load_config(ROOT)).costs
    result = resolve_quick_profit_call(_row(), _bars(both_hit=True), qty=10, costs=costs)
    assert result.status == "resolved"
    assert result.strict_success == 0
    assert result.net_r is not None and result.net_r < 0


def test_resolver_marks_missing_future_as_unresolved_not_failure_or_pass() -> None:
    costs = bse_market(load_config(ROOT)).costs
    result = resolve_quick_profit_call(_row(), _bars().iloc[:2], qty=10, costs=costs)
    assert result.status == "awaiting_outcome_sessions"
    assert result.strict_success is None
    assert result.net_r is None


def test_resolver_excludes_non_bse_evidence() -> None:
    costs = bse_market(load_config(ROOT)).costs
    row = _row() | {"scrip_code": "NSE_1"}
    result = resolve_quick_profit_call(row, _bars(), qty=10, costs=costs)
    assert result.status == "excluded_non_bse"
    assert result.strict_success is None


def test_sign_flip_and_holm_are_deterministic_and_familywise() -> None:
    pvalue = one_sided_sign_flip_pvalue([1.0] * 8, seed=7, draws=100)
    assert pvalue == pytest.approx(2 / 257)
    adjusted = holm_adjusted_pvalues({"a": pvalue, "b": 0.04, "c": None, "d": 0.5})
    assert adjusted["a"] == pytest.approx(pvalue * 4)
    assert adjusted["b"] >= adjusted["a"]
    assert adjusted["c"] is None


def test_sample_gate_keeps_high_percentage_trial_collecting() -> None:
    protocol = replace(
        DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
        min_resolved_calls=100,
        min_active_sessions=30,
        min_matched_control_sessions=30,
    )
    records: list[QuickProfitRecord] = []
    source: list[dict[str, object]] = []
    for offset in range(10):
        armed = date(2026, 10, 5 + offset)
        for rule in ("random_eligible", *CANDIDATE_RULES):
            source.append({"rule": rule, "armed_on": armed.isoformat()})
            records.append(
                QuickProfitRecord(
                    call_id=f"{rule}:{offset}",
                    rule=rule,
                    scrip_code="BSE_1",
                    armed_on=armed,
                    entry_on=armed,
                    status="resolved",
                    strict_success=1,
                    net_r=0.25 if rule != "random_eligible" else 0.0,
                )
            )
    result = summarize_cohort(
        records, source, protocol=protocol, development_only=False
    )
    assert all(trial["observed_strict_success"] == 1.0 for trial in result["trials"])
    assert all(trial["verdict"] == "collecting" for trial in result["trials"])
    assert not result["qualified_rules"]


def test_preactivation_cohort_can_never_qualify() -> None:
    protocol = replace(
        DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
        min_resolved_calls=1,
        min_active_sessions=1,
        min_matched_control_sessions=1,
        min_session_coverage=0.0,
        min_wilson_lower=0.0,
        min_session_target_rate=0.0,
        min_control_advantage_r=-1.0,
        familywise_alpha=1.0,
    )
    records = [
        QuickProfitRecord(
            call_id=f"{rule}:1",
            rule=rule,
            scrip_code="BSE_1",
            armed_on=date(2026, 10, 1),
            entry_on=date(2026, 10, 2),
            status="resolved",
            strict_success=1,
            net_r=0.5 if rule != "random_eligible" else 0.0,
        )
        for rule in ("random_eligible", *CANDIDATE_RULES)
    ]
    source = [{"rule": row.rule, "armed_on": row.armed_on.isoformat()} for row in records]
    result = summarize_cohort(records, source, protocol=protocol, development_only=True)
    assert all(trial["verdict"] == "development_only" for trial in result["trials"])
    assert not result["qualified_rules"]
