from __future__ import annotations

from tradedesk.failure_attribution import attribute_failure, failure_attribution_summary


def _failed(**updates):  # type: ignore[no-untyped-def]
    row = {
        "signal_id": "one",
        "market": "nse",
        "setup": "base_breakout",
        "armed_on": "2026-01-01",
        "outcome_state": "resolved_call",
        "outcome": "stop",
        "label": 0,
        "probability": 0.8,
        "gross_r": -1.0,
        "net_r": -1.2,
        "mfe_r": 0.2,
        "mae_r": -1.1,
        "first_event": "stop",
        "entry": 100.0,
        "actual_entry_price": 101.5,
        "prediction_payload": {
            "context": {
                "atr_pct": 0.06,
                "average_turnover": 20_000_000.0,
                "sector_percentile": 30.0,
                "market_regime": "neutral",
            },
            "source_snapshot": {
                "source_feature_row": {"atr": 2.0, "volume_ratio": 0.8}
            },
        },
    }
    row.update(updates)
    return row


def test_failure_attribution_separates_verified_facts_from_associations() -> None:
    items = attribute_failure(_failed())
    by_code = {item["code"]: item for item in items}
    assert by_code["false_breakout"]["evidence"] == "verified_path"
    assert by_code["late_or_overextended_entry"]["evidence"] == "verified_path"
    assert by_code["model_overconfidence"]["evidence"] == "verified_calibration_miss"
    assert by_code["sector_weakness"]["evidence"] == "diagnostic_association"
    assert by_code["insufficient_volume_confirmation"]["evidence"] == "diagnostic_association"
    assert by_code["excessive_volatility"]["evidence"] == "diagnostic_association"
    assert by_code["poor_liquidity_or_slippage"]


def test_invalid_is_data_quality_only_and_never_a_performance_failure() -> None:
    invalid = _failed(
        outcome_state="invalid_call", outcome="unavailable", label=None, first_event="missing_ohlc"
    )
    assert [item["code"] for item in attribute_failure(invalid)] == ["data_quality_failure"]
    report = failure_attribution_summary([invalid])
    assert report["resolved_calls"] == 0
    assert report["failed_calls"] == 0
    assert report["invalid_calls_excluded"] == 1
    assert report["categories"] == []


def test_summary_reports_recurrence_breakdowns_and_r_values() -> None:
    first = _failed(armed_on="2026-01-01")
    second = _failed(signal_id="two", armed_on="2026-01-02", probability=0.4)
    win = _failed(
        signal_id="win", armed_on="2026-01-03", outcome="target", label=1, gross_r=0.75,
        net_r=0.6,
    )
    report = failure_attribution_summary([first, second, win])
    assert report["resolved_calls"] == 3 and report["failed_calls"] == 2
    false_breakout = next(c for c in report["categories"] if c["code"] == "false_breakout")
    assert false_breakout["failures"] == 2
    assert false_breakout["sessions"] == 2 and false_breakout["recurring"]
    assert false_breakout["by_setup"] == {"base_breakout": 2}
    assert false_breakout["mean_net_r"] == -1.2
    assert false_breakout["accuracy_wilson_95"]["upper"] > 0
    assert false_breakout["recent_share"] == 1.0
    assert false_breakout["by_sector"] == {"unknown": 2}
    assert report["setup_performance"][0]["strict_accuracy"] == 1 / 3
    assert report["unavailable_diagnostics"]


def test_success_and_pending_calls_receive_no_failure_attribution() -> None:
    assert attribute_failure(_failed(outcome="target", label=1)) == []
    assert attribute_failure(_failed(outcome_state="pending_call", outcome=None, label=None)) == []
