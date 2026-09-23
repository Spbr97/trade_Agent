import json

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_report import summarize_aem


def _resolved(day, identifier, *, target=True, pnl=100.0, symbol="TEST"):
    strict = target and pnl > 0
    return {
        "event_id": identifier,
        "session_date": day,
        "decision": "TRADE",
        "status": "resolved",
        "strict_success": strict,
        "label": int(strict),
        "target_hit": target,
        "net_pnl": pnl,
        "net_r": pnl / 100.0,
        "symbol": symbol,
        "intraday_entry_pattern": "momentum_ignition",
    }


def test_report_distinguishes_target_success_from_any_profit_and_keeps_zero_call_days():
    events = pd.DataFrame(
        [
            _resolved("2026-09-17", "target_win"),
            _resolved("2026-09-17", "time_profit", target=False, pnl=25.0),
            _resolved("2026-09-17", "target_net_loss", pnl=-5.0, symbol="OTHER"),
            _resolved("2026-09-18", "next_win"),
            {
                "event_id": "not_filled",
                "session_date": "2026-09-18",
                "decision": "TRADE",
                "status": "no_pullback_fill",
                "symbol": "TEST",
                "intraday_entry_pattern": "impulse_pullback",
            },
        ]
    )
    report = summarize_aem(events, ["2026-09-16", "2026-09-17", "2026-09-18"])
    overall = report["overall"]
    assert overall["resolved_trades"] == 4
    assert overall["trade_decisions"] == 5
    assert overall["unfilled_decisions"] == 1
    assert overall["unresolved_decisions"] == 0
    assert overall["strict_success_rate"] == 0.5
    assert overall["positive_net_rate"] == 0.75
    assert overall["mean_net_r"] == pytest.approx(0.55)
    interval = overall["strict_success_wilson95"]
    assert interval["lower"] == pytest.approx(0.150038989, abs=1e-8)
    assert interval["upper"] == pytest.approx(0.849961011, abs=1e-8)
    zero = report["by_session"][0]
    assert zero["resolved_trades"] == 0 and zero["strict_success_rate"] is None
    assert zero["at_least_70pct"] is None
    coverage = report["session_coverage"]
    assert coverage["zero_trade_decision_sessions"] == 1
    assert coverage["active_sessions_at_least_70pct"] == 0.5
    assert coverage["active_sessions_at_least_80pct"] == 0.5
    assert report["by_symbol"]["OTHER"]["positive_net_rate"] == 0.0
    assert report["by_entry_pattern"]["impulse_pullback"]["resolved_trades"] == 0
    assert report["eligible_for_live"] is False and report["never_live"] is True
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("status", [None, "no_executable_bar", "incomplete_execution_session"])
def test_unknown_outcomes_are_counted_separately_and_cannot_establish_session_target(status):
    events = pd.DataFrame(
        [
            _resolved("2026-09-18", "win"),
            {
                "event_id": "pending",
                "session_date": "2026-09-18",
                "decision": "TRADE",
                "status": status,
            },
            {
                "event_id": "rejected",
                "session_date": "2026-09-17",
                "decision": "NO_TRADE",
            },
        ]
    )
    report = summarize_aem(events, ["2026-09-17", "2026-09-18"])
    assert report["overall"]["strict_success_rate"] == 1.0
    assert report["overall"]["unresolved_decisions"] == 1
    assert report["overall"]["statuses"][status or "missing_status"] == 1
    assert report["overall"]["statuses"]["not_triggered"] == 1
    assert report["session_coverage"]["fully_resolved_active_sessions"] == 0
    assert report["session_coverage"]["active_sessions_at_least_80pct"] is None
    assert report["by_session"][1]["at_least_70pct"] is None
    json.dumps(report, allow_nan=False)


def test_empty_report_keeps_calendar_and_has_no_fabricated_rates():
    report = summarize_aem(pd.DataFrame(), ["2026-09-17", "2026-09-18"])
    assert len(report["by_session"]) == 2
    assert report["overall"]["strict_success_rate"] is None
    assert report["overall"]["mean_net_r"] is None
    assert report["overall"]["strict_success_wilson95"]["lower"] is None
    assert report["session_coverage"]["zero_trade_decision_sessions"] == 2
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("net_r", np.nan),
        ("net_pnl", np.inf),
        ("strict_success", None),
        ("strict_success", "True"),
        ("target_hit", False),
        ("net_r", -1.0),
        ("label", 0),
    ],
)
def test_resolved_outcomes_must_be_complete_finite_and_consistent(column, value):
    row = _resolved("2026-09-18", "broken")
    row[column] = value
    with pytest.raises(ValueError):
        summarize_aem(pd.DataFrame([row]), ["2026-09-18"])


def test_duplicate_events_and_out_of_calendar_events_are_rejected():
    row = _resolved("2026-09-18", "duplicate")
    with pytest.raises(ValueError, match="unique"):
        summarize_aem(pd.DataFrame([row, row]), ["2026-09-18"])
    with pytest.raises(ValueError, match="outside"):
        summarize_aem(pd.DataFrame([row]), ["2026-09-17"])
    with pytest.raises(ValueError, match="distinct"):
        summarize_aem(pd.DataFrame(), ["2026-09-18", "2026-09-18"])
