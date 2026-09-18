from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
from tradedesk_lab.mcb_contract import McbContract
from tradedesk_lab.mcb_dataset import build_events
from tradedesk_lab.mcb_detector import (
    McbCandidate,
    McbDecision,
    detect_daily_candidate,
    evaluate_trigger,
)
from tradedesk_lab.mcb_features import daily_snapshot, time_of_day_rvol
from tradedesk_lab.mcb_labels import label_trade

from tradedesk.config.models import ChargeSchedule, RiskConfig
from tradedesk.markets.costs import EquityCostModel


def daily_frame() -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=120, tz="Asia/Kolkata")
    close = np.linspace(90, 130, len(idx))
    close[-5:] = [129.4, 129.6, 129.7, 129.8, 130.0]
    volume = np.full(len(idx), 1_000_000.0)
    volume[-5:] = 500_000
    return pd.DataFrame(
        {
            "open": close - 0.2,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def m5_history(*, breakout: bool = True) -> pd.DataFrame:
    sessions = []
    for day in pd.bdate_range("2025-06-09", periods=7):
        idx = pd.date_range(f"{day.date()} 09:15", periods=75, freq="5min", tz="Asia/Kolkata")
        base = np.linspace(129.8, 130.2, 75)
        frame = pd.DataFrame(
            {
                "open": base - 0.05,
                "high": base + 0.25,
                "low": base - 0.25,
                "close": base,
                "volume": 1_000,
            },
            index=idx,
        )
        sessions.append(frame)
    current = sessions[-1]
    if breakout:
        # The 09:30 bar closes at 09:35 above both the daily level and opening range.
        current.iloc[3] = [130.6, 132.3, 130.5, 132.1, 3_000]
        current.iloc[4] = [132.0, 136.5, 131.8, 136.0, 2_000]
    return pd.concat(sessions)


def risk_and_costs():
    return (
        RiskConfig(trading_capital=Decimal("100000")),
        EquityCostModel(ChargeSchedule()),
    )


def relaxed_contract() -> McbContract:
    return McbContract(
        daily_prior_momentum_min=0.04,
        daily_volume_contraction_max=0.9,
        rvol_min_sessions=5,
    )


def test_contract_hash_is_stable_and_changes_with_rules():
    first = McbContract()
    assert first.sha256 == McbContract().sha256
    assert first.sha256 != McbContract(rvol_min=1.4).sha256


def test_daily_candidate_is_causal_and_has_required_structure():
    frame = daily_frame()
    contract = relaxed_contract()
    on = frame.index[-1]
    original = daily_snapshot(frame, on, contract)
    future = frame.copy()
    future.loc[on + pd.Timedelta(days=1)] = [1, 1_000, 0.5, 900, 99_000_000]
    assert daily_snapshot(future, on, contract) == original
    assert original["eligible"]
    candidate = detect_daily_candidate("NSE_1", "TEST", frame, on, contract)
    assert candidate is not None and candidate.breakout_level > original["close"]


def test_daily_candidate_rejects_below_tradability_floor():
    frame = daily_frame()
    frame["volume"] = 1_000
    snapshot = daily_snapshot(frame, frame.index[-1], relaxed_contract())
    assert snapshot["median_turnover_inr"] < 50_000_000
    assert snapshot["checks"]["liquidity"] is False
    assert not snapshot["eligible"]


def test_time_of_day_rvol_uses_only_prior_same_slot_volume():
    frame = m5_history()
    rvol = time_of_day_rvol(frame, lookback_sessions=20, min_sessions=5)
    stamp = frame.index[-75 + 3]
    assert rvol.loc[stamp] == 3.0
    changed = frame.copy()
    changed.loc[changed.index > stamp, "volume"] = 1_000_000
    pd.testing.assert_series_equal(
        rvol.loc[:stamp],
        time_of_day_rvol(changed, lookback_sessions=20, min_sessions=5).loc[:stamp],
    )


def test_trigger_waits_for_closed_bar_and_next_bar_is_entry():
    daily = daily_frame()
    contract = relaxed_contract()
    candidate = detect_daily_candidate("NSE_1", "TEST", daily, daily.index[-1], contract)
    assert candidate is not None
    intraday = m5_history()
    before_close = evaluate_trigger(
        candidate,
        intraday,
        pd.Timestamp("2025-06-17 09:34:59", tz="Asia/Kolkata"),
        contract,
    )
    assert before_close.decision != "TRADE"
    decision = evaluate_trigger(
        candidate,
        intraday,
        pd.Timestamp("2025-06-17 09:35", tz="Asia/Kolkata"),
        contract,
    )
    assert decision.decision == "TRADE"
    risk, costs = risk_and_costs()
    session = intraday.loc["2025-06-17"]
    outcome = label_trade(candidate, decision, session, risk, costs, contract)
    assert outcome["status"] == "resolved" and outcome["strict_success"] is True
    assert pd.Timestamp(outcome["entry_at"]).strftime("%H:%M") == "09:35"
    assert outcome["net_r"] < outcome["gross_r"]


def test_stop_wins_ambiguous_bar_and_target_can_lose_after_costs():
    contract = McbContract(target_r=0.01)
    candidate = McbCandidate(
        identifier="MCB_v1:NSE_1:2025-06-16",
        scrip_code="NSE_1",
        symbol="TEST",
        armed_on=date(2025, 6, 16),
        breakout_level=100,
        compression_low=99,
        daily_atr=2,
        features={},
        contract_sha256=contract.sha256,
    )
    decision = McbDecision(
        candidate_id=candidate.identifier,
        decision="TRADE",
        available_at="2025-06-17T09:35:00+05:30",
        entry_trigger=100,
        stop=99.9,
        provisional_target=100.001,
        reasons=("test",),
        features={},
        contract_sha256=contract.sha256,
    )
    idx = pd.date_range("2025-06-17 09:35", periods=2, freq="5min", tz="Asia/Kolkata")
    tie = pd.DataFrame(
        {
            "open": [100, 100],
            "high": [101, 101],
            "low": [99, 99],
            "close": [100, 100],
            "volume": 1000,
        },
        index=idx,
    )
    risk, costs = risk_and_costs()
    assert label_trade(candidate, decision, tie, risk, costs, contract)["outcome"] == "stop"
    target_only = tie.copy()
    target_only.low = 99.95
    outcome = label_trade(candidate, decision, target_only, risk, costs, contract)
    assert outcome["target_hit"] is True
    assert outcome["strict_success"] is False and outcome["label"] == 0


def test_event_builder_keeps_trade_and_no_trade_candidates():
    contract = relaxed_contract()
    daily = daily_frame()
    intraday = m5_history()
    # Keep one session as a daily-qualified but untriggered counterexample.
    last_session = intraday.index.date == intraday.index[-1].date()
    quiet = intraday.copy()
    quiet.loc[last_session, ["open", "high", "low", "close", "volume"]] = [
        129.9,
        130.2,
        129.6,
        130.0,
        1_000,
    ]
    # First six sessions supply slot history; append a second evaluation session with breakout.
    extra = m5_history().loc["2025-06-17"].copy()
    extra.index = extra.index + pd.Timedelta(days=1)
    combined = pd.concat([quiet, extra])
    risk, costs = risk_and_costs()
    events, audit = build_events(
        {"NSE_1": daily},
        {"NSE_1": combined},
        {"NSE_1": "TEST"},
        risk=risk,
        costs=costs,
        contract=contract,
    )
    assert {"TRADE", "NO_TRADE"}.issubset(set(events.decision))
    assert audit["daily_candidates"] >= 2
    assert events.event_id.is_unique


def test_no_trade_only_event_frame_has_no_resolved_status_column():
    events = pd.DataFrame([{"decision": "NO_TRADE", "label": None}])
    resolved = events.loc[events.status == "resolved"] if "status" in events else events.iloc[:0]
    assert resolved.empty
