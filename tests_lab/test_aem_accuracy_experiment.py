import pandas as pd
import pytest
from tradedesk_lab.aem_accuracy_experiment import SPECS, evaluate_accuracy_experiment


def _events():
    rows = []
    for day in pd.bdate_range("2026-01-01", periods=120):
        on = day.date().isoformat()
        for item in range(4):
            success = item < 2
            rows.append(
                {
                    "event_id": f"{on}:{item}",
                    "session_date": on,
                    "decision": "TRADE",
                    "status": "resolved",
                    "strict_success": success,
                    "target_hit": success,
                    "net_pnl": 10.0 if success else -10.0,
                    "net_r": 0.5 if success else -1.0,
                    "gross_r": 0.6 if success else -0.9,
                    "daily_extension_atr": 0.5,
                    "daily_median_turnover_inr": 1_000_000.0,
                    "intraday_resistance_distance": 0.02 if success else 0.005,
                    "intraday_tod_rvol": 1.5 if success else 0.5,
                    "intraday_vwap": 100.0,
                    "intraday_vwap_slope": 0.1 if success else -0.1,
                    "intraday_signal_price": 101.0 if success else 99.0,
                    "intraday_body_ratio": 0.5 if success else -0.5,
                }
            )
    return pd.DataFrame(rows)


def test_information_quality_selects_on_development_then_scores_holdout_once():
    events = _events()
    dates = sorted(events.session_date.unique())
    report = evaluate_accuracy_experiment(events, dates)
    assert len(SPECS) == 11
    assert report["split"]["development_sessions"] == 80
    assert report["split"]["holdout_sessions"] == 40
    assert report["split"]["holdout_opened_once"] is True
    assert report["selected_specification"]["id"] == "room_1p2"
    assert report["holdout_baseline"]["strict_success_rate"] == 0.5
    assert report["holdout_challenger"]["strict_success_rate"] == 1.0
    assert report["holdout_challenger"]["resolved_trades"] == 80
    assert report["promotion_checks"]["minimum_holdout_resolved"] is False
    assert report["passed"] is False


def test_information_quality_rejects_wrong_calendar_and_missing_causal_fields():
    events = _events()
    dates = sorted(events.session_date.unique())
    with pytest.raises(ValueError, match="120-session"):
        evaluate_accuracy_experiment(events, dates[:-1])
    with pytest.raises(ValueError, match="missing"):
        evaluate_accuracy_experiment(events.drop(columns="intraday_tod_rvol"), dates)
