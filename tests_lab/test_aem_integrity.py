from dataclasses import replace
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_detector import AemCandidate, AemDecision
from tradedesk_lab.aem_features import daily_snapshot, intraday_snapshot
from tradedesk_lab.aem_labels import label_trade

from tradedesk.config.models import ChargeSchedule, RiskConfig
from tradedesk.markets.costs import EquityCostModel


def execution(rows, *, start="2025-06-17 09:25"):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.date_range(start, periods=len(rows), freq="1min", tz="Asia/Kolkata"),
    )


def label(frame, *, limit=False, contract=None, available="2025-06-17T09:25:00+05:30"):
    contract = contract or AemContract(max_hold_minutes=3)
    candidate = AemCandidate(
        identifier="test",
        scrip_code="NSE_1",
        symbol="TEST",
        armed_on=date(2025, 6, 16),
        resistance=104,
        daily_atr=3,
        features={},
        contract_sha256=contract.sha256,
    )
    decision = AemDecision(
        candidate_id=candidate.identifier,
        decision="TRADE",
        available_at=available,
        signal_price=100.5 if limit else 100,
        reasons=(),
        features={
            "entry_pattern": "impulse_pullback" if limit else "momentum_ignition",
            "vwap": 100,
        },
        contract_sha256=contract.sha256,
    )
    return label_trade(
        candidate,
        decision,
        frame,
        RiskConfig(trading_capital=Decimal("100000")),
        EquityCostModel(ChargeSchedule(slippage_pct=Decimal("0.001"))),
        contract,
    )


def test_touched_limit_never_pays_above_limit_or_credits_prefill_high():
    frame = execution(
        [[100.4, 101, 99.9, 100.1], [100, 100.2, 99.8, 100.1], [99, 99.5, 98.8, 99.1]]
    )
    result = label(frame, limit=True)
    assert result["entry"] == 100
    assert result["outcome"] == "gap_stop"
    assert result["entry_time_precision"] == "within_bar"
    assert result["entry_observed_at"].endswith("09:26:00+05:30")
    assert result["exit_at"].endswith("09:27:00+05:30")
    assert result["strict_success"] is False


def test_limit_fill_close_proves_postfill_target_and_stop_still_has_priority():
    result = label(execution([[100.4, 101, 99.9, 100.9]]), limit=True)
    assert result["outcome"] == "target"
    assert result["exit_at"].endswith("09:26:00+05:30")
    assert result["minutes_held"] == 1
    stopped = label(execution([[100.4, 101, 99, 100.9]]), limit=True)
    assert stopped["outcome"] == "stop"


@pytest.mark.parametrize("opening,expected_entry", [(99.99, 100), (99, 99.099)])
def test_marketable_limit_caps_slippage_and_keeps_gap_improvement(opening, expected_entry):
    result = label(execution([[opening, 101, opening - 0.01, 100.9]]), limit=True)
    assert result["entry"] == pytest.approx(expected_entry)
    assert result["entry"] <= 100
    assert result["entry_time_precision"] == "bar_open"


def test_gap_target_precedes_later_low_and_fills_at_open():
    result = label(execution([[100, 100.2, 99.9, 100.1], [102, 103, 98, 100]]))
    assert result["outcome"] == "target"
    assert result["exit"] == pytest.approx(102 * 0.999)
    assert result["exit_at"].endswith("09:26:00+05:30")
    assert result["exit_time_precision"] == "bar_open"


def test_truncated_execution_is_not_a_shortened_time_exit():
    result = label(execution([[100, 100.2, 99.9, 100.1]]))
    assert result == {
        "status": "insufficient_execution_horizon",
        "strict_success": None,
        "label": None,
    }


def test_full_horizon_uses_last_bar_close_timestamp():
    result = label(execution([[100, 100.2, 99.9, 100.1]] * 3))
    assert result["outcome"] == "time_exit"
    assert result["exit_at"].endswith("09:28:00+05:30")
    assert result["minutes_held"] == 3


def test_missing_minute_cannot_skip_a_possible_stop():
    frame = execution([[100, 100.2, 99.9, 100.1], [100, 100.2, 99.9, 100.1], [100, 102, 99.9, 101]])
    result = label(frame.drop(frame.index[1]))
    assert result["status"] == "incomplete_execution_session"
    assert result["label"] is None
    assert label(frame.iloc[1:])["status"] == "incomplete_execution_session"


@pytest.mark.parametrize("kind", ["duplicate", "unsorted", "nat", "unaligned"])
def test_invalid_execution_timestamps_fail_closed(kind):
    frame = execution([[100, 100.2, 99.9, 100.1]] * 3)
    if kind == "duplicate":
        frame.index = [frame.index[0], frame.index[0], frame.index[2]]
    elif kind == "unsorted":
        frame = frame.iloc[::-1]
    elif kind == "nat":
        frame.index = pd.DatetimeIndex([frame.index[0], pd.NaT, frame.index[2]])
    else:
        frame.index += pd.Timedelta(seconds=30)
    assert label(frame)["status"] == "invalid_execution_timestamps"


def test_next_day_bars_cannot_fill_todays_decision():
    frame = execution([[100, 102, 99.9, 101]], start="2025-06-18 09:25")
    assert label(frame)["status"] == "no_executable_bar"


def test_naive_and_utc_execution_timestamps_have_same_exchange_result():
    frame = execution([[100, 102, 99.9, 101]])
    expected = label(frame)
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    utc = frame.copy()
    utc.index = utc.index.tz_convert("UTC")
    assert label(naive, available="2025-06-17 09:25") == expected
    assert label(utc, available="2025-06-17T03:55:00Z") == expected


def test_limit_unfilled_needs_complete_order_window():
    frame = execution([[100.5, 100.6, 100.2, 100.4]] * 15)
    assert label(frame, limit=True)["status"] == "no_pullback_fill"
    assert label(frame.iloc[:3], limit=True)["status"] == "insufficient_execution_horizon"


def test_session_close_is_valid_horizon_and_1530_marker_cannot_trade():
    frame = execution([[100, 100.2, 99.9, 100.1], [100, 200, 1, 100]], start="2025-06-17 15:29")
    result = label(frame, available="2025-06-17T15:29:00+05:30")
    assert result["outcome"] == "time_exit"
    assert result["exit_at"].endswith("15:30:00+05:30")
    assert result["minutes_held"] == 1


def test_invalid_ohlc_cannot_make_a_profitable_label():
    frame = execution([[100, 102, np.nan, 101]])
    assert label(frame)["status"] == "invalid_execution_bar"
    frame = execution([[100, 102, "bad", 101]])
    assert label(frame)["status"] == "invalid_execution_bar"


def feature_frame():
    frame = execution([[100, 100.6, 99.9, 100.5]] * 6, start="2025-06-17 09:15")
    frame["volume"] = 1000
    frame["vwap"] = np.linspace(100, 100.1, len(frame))
    frame["atr14"] = 1
    frame["session_bar"] = np.arange(len(frame))
    frame["tod_rvol"] = 2
    return frame


def snapshot(frame, at="2025-06-17 09:21"):
    return intraday_snapshot(frame, at=pd.Timestamp(at), resistance=102, contract=AemContract())


def test_future_bar_cadence_cannot_change_signal_availability():
    frame = feature_frame()
    expected = snapshot(frame)
    tail = pd.concat([frame.iloc[-1:]] * 20)
    tail.index = pd.date_range("2025-06-17 09:21", periods=20, freq="5min", tz="Asia/Kolkata")
    tail.loc[:, ["high", "close"]] = 999
    assert snapshot(pd.concat([frame, tail])) == expected


def test_stale_and_missing_signal_bars_fail_closed():
    frame = feature_frame()
    with pytest.raises(ValueError, match="stale_signal_bar"):
        snapshot(frame, at="2025-06-17 09:22")
    with pytest.raises(ValueError, match="incomplete_signal_session"):
        snapshot(frame.drop(frame.index[2]))


def test_invalid_signal_ohlc_fails_closed_before_using_precomputed_features():
    frame = feature_frame()
    frame.loc[frame.index[-1], "high"] = np.nan
    with pytest.raises(ValueError, match="invalid_signal_bar"):
        snapshot(frame)


def test_daily_snapshot_uses_exchange_date_for_utc_index():
    idx = pd.bdate_range("2025-01-01", periods=70, tz="Asia/Kolkata")
    frame = pd.DataFrame(
        {
            "open": 99,
            "high": 101,
            "low": 98,
            "close": 100,
            "volume": 1_000_000,
            "ema20": 99,
            "ema50": 98,
            "ema20_slope": 1,
            "ema50_slope": 1,
            "atr14": 2,
        },
        index=idx,
    )
    expected = daily_snapshot(frame, idx[-1].date(), AemContract())
    frame.index = frame.index.tz_convert("UTC")
    assert daily_snapshot(frame, idx[-1].date(), AemContract()) == expected


def test_contract_versions_execution_and_refuses_silent_m5_fallback():
    contract = AemContract()
    assert contract.feature_version.endswith("v2")
    assert contract.label_version.endswith("v2")
    with pytest.raises(ValueError, match="one-minute"):
        replace(contract, execution_interval_minutes=5)
