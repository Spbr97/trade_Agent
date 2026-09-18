from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
from tradedesk_lab.aem_contract import AemContract
from tradedesk_lab.aem_detector import (
    AemCandidate,
    AemDecision,
    detect_daily_candidate,
    evaluate_trigger,
)
from tradedesk_lab.aem_features import daily_snapshot
from tradedesk_lab.aem_labels import label_trade

from tradedesk.config.models import ChargeSchedule, RiskConfig
from tradedesk.markets.costs import EquityCostModel


def daily_frame() -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=120, tz="Asia/Kolkata")
    close = np.linspace(80, 100, len(idx))
    close[-10:] = np.linspace(98.5, 100.0, 10)
    return pd.DataFrame(
        {
            "open": close - 0.2,
            "high": close + 1.5,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000,
        },
        index=idx,
    )


def minute_history() -> pd.DataFrame:
    sessions = []
    for day in pd.bdate_range("2025-06-09", periods=7):
        idx = pd.date_range(f"{day.date()} 09:15", periods=375, freq="1min", tz="Asia/Kolkata")
        frame = pd.DataFrame(
            {
                "open": 99.8,
                "high": 100.1,
                "low": 99.7,
                "close": 99.9,
                "volume": 1_000,
            },
            index=idx,
        )
        sessions.append(frame)
    current = sessions[-1]
    current.iloc[5] = [99.9, 100.7, 99.8, 100.6, 3_000]
    return pd.concat(sessions)


def risk_and_costs():
    return (
        RiskConfig(trading_capital=Decimal("100000")),
        EquityCostModel(ChargeSchedule()),
    )


def test_contract_hash_tracks_quick_profit_rules():
    assert AemContract().sha256 == AemContract().sha256
    assert AemContract().sha256 != AemContract(target_pct=0.01).sha256


def test_daily_candidate_is_causal_and_does_not_require_compression():
    frame = daily_frame()
    contract = AemContract()
    on = frame.index[-1]
    original = daily_snapshot(frame, on, contract)
    future = frame.copy()
    future.loc[on + pd.Timedelta(days=1)] = [1, 500, 0.5, 400, 99_000_000]
    assert daily_snapshot(future, on, contract) == original
    assert original["eligible"]
    assert detect_daily_candidate("NSE_1", "TEST", frame, on, contract) is not None


def test_trigger_is_anticipatory_and_quick_target_uses_next_minute():
    contract = AemContract()
    candidate = AemCandidate(
        identifier="AEM_v1:NSE_1:2025-06-16",
        scrip_code="NSE_1",
        symbol="TEST",
        armed_on=date(2025, 6, 16),
        resistance=102.0,
        daily_atr=3.0,
        features={},
        contract_sha256=contract.sha256,
    )
    decision = evaluate_trigger(
        candidate,
        minute_history(),
        pd.Timestamp("2025-06-17 09:21", tz="Asia/Kolkata"),
        contract,
    )
    assert decision.decision == "TRADE"
    assert decision.signal_price < candidate.resistance
    idx = pd.date_range("2025-06-17 09:21", periods=15, freq="1min", tz="Asia/Kolkata")
    execution = pd.DataFrame(
        {
            "open": 100.6,
            "high": np.linspace(100.7, 101.8, len(idx)),
            "low": 100.5,
            "close": np.linspace(100.65, 101.7, len(idx)),
            "volume": 500,
        },
        index=idx,
    )
    risk, costs = risk_and_costs()
    result = label_trade(candidate, decision, execution, risk, costs, contract)
    assert result["outcome"] == "target" and result["strict_success"] is True
    assert result["entry_at"].endswith("09:21:00+05:30")
    assert result["execution_interval_minutes"] == 1
    assert 0.007 < result["target"] / result["entry"] - 1 < 0.009
    assert result["minutes_held"] < contract.max_hold_minutes


def test_far_resistance_waits_for_impulse_pullback():
    contract = AemContract()
    candidate = AemCandidate(
        identifier="AEM_v1:NSE_1:2025-06-16",
        scrip_code="NSE_1",
        symbol="TEST",
        armed_on=date(2025, 6, 16),
        resistance=104.0,
        daily_atr=3.0,
        features={},
        contract_sha256=contract.sha256,
    )
    frame = minute_history()
    current = frame.index.date == frame.index[-1].date()
    positions = np.flatnonzero(current)
    frame.iloc[positions[4]] = [99.8, 101.1, 99.7, 101.0, 3_000]
    frame.iloc[positions[5]] = [100.8, 100.9, 100.3, 100.5, 1_000]
    decision = evaluate_trigger(
        candidate,
        frame,
        pd.Timestamp("2025-06-17 09:21", tz="Asia/Kolkata"),
        contract,
    )
    assert decision.decision == "TRADE"
    assert decision.features["entry_pattern"] == "impulse_pullback"


def test_one_minute_stop_wins_ambiguous_bar():
    contract = AemContract()
    candidate = AemCandidate(
        identifier="AEM_v1:NSE_1:2025-06-16",
        scrip_code="NSE_1",
        symbol="TEST",
        armed_on=date(2025, 6, 16),
        resistance=102,
        daily_atr=3,
        features={},
        contract_sha256=contract.sha256,
    )
    decision = AemDecision(
        candidate_id=candidate.identifier,
        decision="TRADE",
        available_at="2025-06-17T09:25:00+05:30",
        signal_price=100,
        reasons=("test",),
        features={"entry_pattern": "momentum_ignition"},
        contract_sha256=contract.sha256,
    )
    idx = pd.date_range("2025-06-17 09:25", periods=2, freq="1min", tz="Asia/Kolkata")
    execution = pd.DataFrame(
        {
            "open": [100, 100],
            "high": [101, 101],
            "low": [99, 99],
            "close": [100, 100],
            "volume": 1_000,
        },
        index=idx,
    )
    risk, costs = risk_and_costs()
    assert label_trade(candidate, decision, execution, risk, costs, contract)["outcome"] == "stop"
