from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import pytest
import tradedesk_lab.bse_accuracy_quick_profit as bse_module
from tradedesk_lab.artifacts import ROOT
from tradedesk_lab.bse_accuracy_quick_profit import (
    CANDIDATE_RULES,
    DEFAULT_BSE_QUICK_PROFIT_PROTOCOL,
    QuickProfitRecord,
    _cohort_records,
    _cohort_rows,
    holm_adjusted_pvalues,
    one_sided_sign_flip_pvalue,
    refresh_bse_quick_profit,
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


def test_prospective_b1_requires_same_session_registration() -> None:
    base = {
        "rule": "rsi2_dip_ema50",
        "scrip_code": "BSE_1",
        "armed_on": "2026-10-05",
    }
    rows = [
        base | {
            "call_id": "on-time",
            "logged_at": "2026-10-05T16:40:00+05:30",
        },
        base | {
            "call_id": "late-catch-up",
            "logged_at": "2026-10-06T08:00:00+05:30",
        },
        base | {"call_id": "missing-registration"},
    ]

    prospective = _cohort_rows(
        rows, prospective=True, protocol=DEFAULT_BSE_QUICK_PROFIT_PROTOCOL
    )

    assert [row["call_id"] for row in prospective] == ["on-time"]


def test_prospective_registration_uses_ist_date_and_rejects_duplicate_ids() -> None:
    base = {
        "rule": "rsi2_dip_ema50",
        "scrip_code": "BSE_1",
        "armed_on": "2026-10-05",
    }
    rows = [
        base
        | {
            "call_id": "utc-on-time",
            "logged_at": "2026-10-04T19:00:00+00:00",
        },
        base
        | {
            "call_id": "duplicate",
            "logged_at": "2026-10-05T16:40:00+05:30",
        },
        base
        | {
            "call_id": "duplicate",
            "logged_at": "2026-10-06T16:40:00+05:30",
        },
    ]

    prospective = _cohort_rows(
        rows, prospective=True, protocol=DEFAULT_BSE_QUICK_PROFIT_PROTOCOL
    )

    assert [row["call_id"] for row in prospective] == ["utc-on-time"]


def test_duplicate_resolved_call_id_cannot_hitchhike_into_cohort() -> None:
    source = [{"call_id": "same-id"}]
    records = [
        QuickProfitRecord(
            "same-id",
            "rsi2_dip_ema50",
            "BSE_1",
            date(2026, 10, 5),
            None,
            "resolved",
            1,
            0.5,
        ),
        QuickProfitRecord(
            "same-id",
            "rsi2_dip_ema50",
            "BSE_1",
            date(2026, 10, 5),
            None,
            "resolved",
            0,
            -1.0,
        ),
    ]

    assert _cohort_records(records, source) == []


def test_development_b1_rows_remain_transport_only_without_registration_time() -> None:
    row = {
        "call_id": "development",
        "rule": "rsi2_dip_ema50",
        "scrip_code": "BSE_1",
        "armed_on": "2026-10-02",
    }

    development = _cohort_rows(
        [row], prospective=False, protocol=DEFAULT_BSE_QUICK_PROFIT_PROTOCOL
    )

    assert development == [row]


def test_protocol_is_one_transported_geometry_and_is_bse_only() -> None:
    protocol = DEFAULT_BSE_QUICK_PROFIT_PROTOCOL
    assert protocol.version == "bse-accuracy-quick-profit-v2"
    assert protocol.market == "bse"
    assert protocol.entry_mode == "next_session_open"
    assert (protocol.stop_atr, protocol.target_r, protocol.max_hold_sessions) == (1.0, 0.5, 3)
    assert protocol.activation_date == "2026-10-04"
    assert protocol.registration_timing == "logged_on_arming_session"
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


def _terminal_result(status: str = "rejected") -> dict[str, object]:
    qualified = status == "research_qualified"
    trials = [
        {
            "rule": rule,
            "verdict": "qualified_research_only" if qualified and offset == 0 else "rejected",
        }
        for offset, rule in enumerate(CANDIDATE_RULES)
    ]
    return {
        "schema_version": DEFAULT_BSE_QUICK_PROFIT_PROTOCOL.state_schema_version,
        "created_at": "2026-12-01T16:00:00+05:30",
        "market": "bse",
        "status": status,
        "live": False,
        "baseline_improved": False,
        "promotion_allowed": False,
        "protocol": asdict(DEFAULT_BSE_QUICK_PROFIT_PROTOCOL)
        | {"sha256": DEFAULT_BSE_QUICK_PROFIT_PROTOCOL.sha256},
        "readiness": {
            "prospective_source_sessions": 30,
            "rules_sample_ready": 4,
        },
        "prospective": {
            "trials": trials,
            "qualified_rules": [CANDIDATE_RULES[0]] if qualified else [],
        },
    }


def test_first_terminal_bse_look_is_latched(monkeypatch, tmp_path) -> None:
    output = tmp_path / "bse-b1.json"
    calls = 0

    def first_run(**_kwargs):
        nonlocal calls
        calls += 1
        return _terminal_result("rejected")

    monkeypatch.setattr(bse_module, "run_bse_quick_profit", first_run)
    first = refresh_bse_quick_profit(output=output, root=tmp_path)
    terminal_bytes = output.read_bytes()
    assert first["status"] == "rejected"
    assert first["terminal_first_look"]["result_sha256"]

    monkeypatch.setattr(
        bse_module,
        "run_bse_quick_profit",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must stay latched")),
    )
    second = refresh_bse_quick_profit(output=output, root=tmp_path)
    assert second["status"] == "rejected"
    assert output.read_bytes() == terminal_bytes
    assert calls == 1


def test_tampered_terminal_bse_look_fails_closed(monkeypatch, tmp_path) -> None:
    output = tmp_path / "bse-b1.json"
    monkeypatch.setattr(
        bse_module,
        "run_bse_quick_profit",
        lambda **_kwargs: _terminal_result("rejected"),
    )
    refresh_bse_quick_profit(output=output, root=tmp_path)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    artifact["status"] = "research_qualified"
    output.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ValueError, match="terminal BSE B1 evidence failed verification"):
        refresh_bse_quick_profit(output=output, root=tmp_path)


def test_bse_research_tracker_refresh_is_failure_isolated(
    monkeypatch, tmp_path, capsys
) -> None:
    import scripts.research_tracker as tracker_script

    monkeypatch.setattr(tracker_script, "resolve", lambda **_kwargs: None)
    monkeypatch.setattr(tracker_script, "scan", lambda **_kwargs: None)
    monkeypatch.setattr(tracker_script, "load_log", lambda _path: [])
    monkeypatch.setattr(tracker_script, "cost_r_for", lambda _market: 0.0)
    monkeypatch.setattr(
        tracker_script, "flag_research_findings", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(tracker_script, "eod_learn", lambda **_kwargs: None)
    monkeypatch.setattr(tracker_script, "report", lambda **_kwargs: None)
    monkeypatch.setattr(
        bse_module,
        "refresh_bse_quick_profit",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("locked")),
    )

    tracker_script.run(
        market="bse",
        db=tmp_path / "bse.duckdb",
        root=tmp_path,
        max_codes=0,
        log=tmp_path / "calls.jsonl",
    )

    assert "BSE B1 accuracy evidence degraded: RuntimeError: locked" in capsys.readouterr().out
