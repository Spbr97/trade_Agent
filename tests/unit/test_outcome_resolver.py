from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradedesk.config.models import ChargeSchedule, CryptoChargeSchedule
from tradedesk.evidence import CONTRACTS
from tradedesk.outcome_resolver import resolve_versioned_call
from tradedesk.prediction_ledger import (
    LEDGER_SCHEMA_VERSION,
    STRATEGY_VERSION,
    build_prediction_payload,
    seal_prediction,
)
from tradedesk.signal_tracker import TrackedSignal, resolve_outcomes


def _row(*, version: str = "quick-profit-v1", market: str = "nse") -> TrackedSignal:
    contract = CONTRACTS[version]
    signal = SimpleNamespace(
        id=f"{market}-one",
        scrip_code=("CDX_ONE" if market == "crypto" else "NSE_ONE"),
        symbol="ONE",
        setup=SimpleNamespace(value="base_breakout"),
        armed_on=date(2026, 1, 1),
        trigger=100.0,
        stop=95.0,
        t1=105.0,
        t2=110.0,
        valid_sessions=3,
        chased_atr_mult=1.0,
        exit_plan=SimpleNamespace(max_hold_sessions=10),
        geometry={"pivot": 100.0},
        rs_percentile=80.0,
        regime="risk_on",
    )
    if market == "crypto":
        schedule = CryptoChargeSchedule().model_dump(mode="json")
        cost_model = "CryptoCostModel"
    else:
        schedule = ChargeSchedule().model_dump(mode="json")
        cost_model = "EquityCostModel"
    entry = SimpleNamespace(
        signal=signal,
        grade=SimpleNamespace(value="A"),
        score=90,
        probability=0.8,
        alertable=True,
        rejected_for=[],
        score_components={"trend": 10.0},
        score_notes=[],
        sector=None,
        sector_percentile=None,
        source_bar={"atr": 2.0, "close": 99.0, "volume": 1000.0},
        atr_pct=0.02,
        avg_turnover=1_000_000.0,
        results_in_sessions=None,
        qty=10.0,
        risk_amount=50.0,
        risk_pct=0.001,
        position_value=1000.0,
        size_caps=[],
        costs_round_trip=5.0,
        net_rr_t1=0.6,
        net_rr_t2=1.8,
        feature_version="v4",
        model_version="model-v1",
        model_kind="logistic",
    )
    watchlist = SimpleNamespace(
        on=date(2026, 1, 1),
        generated_at=datetime(2026, 1, 1, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        regime=None,
        execution_assumptions={
            "cost_model": cost_model,
            "slippage_pct": "0.0005",
            "fee_tax_schedule": schedule,
        },
    )
    payload = build_prediction_payload(
        watchlist=watchlist,
        entry=entry,
        market=market,
        source="live",
        evidence_class="qualified_call",
        contract_kind=contract.kind.value,
        contract_version=version,
    )
    return TrackedSignal(
        signal_id=signal.id,
        scrip_code=signal.scrip_code,
        symbol="ONE",
        setup="base_breakout",
        grade="A",
        armed_on="2026-01-01",
        entry=100.0,
        stop=95.0,
        t1=105.0,
        t2=110.0,
        net_rr_t1=0.6,
        net_rr_t2=1.8,
        rejected_for=[],
        logged_at="2026-01-01T16:00:00+05:30",
        probability=0.8,
        source="live",
        market=market,
        evidence_class="qualified_call",
        outcome_state="pending_call",
        contract_kind=contract.kind.value,
        contract_version=version,
        ledger_schema_version=LEDGER_SCHEMA_VERSION,
        prediction_payload=payload,
        prediction_sha256=seal_prediction(payload),
        source_snapshot_sha256=payload["source_snapshot_sha256"],
        contract_sha256=payload["contract"]["sha256"],
        strategy_version=STRATEGY_VERSION,
        feature_version="v4",
        model_version="model-v1",
    )


def _bars(values: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-01-02", periods=len(values), freq="D", tz="Asia/Kolkata")
    return pd.DataFrame(values, columns=["open", "high", "low", "close"], index=index)


def test_quick_profit_target_is_deterministic_and_net_of_costs() -> None:
    row = _row()
    bars = _bars([(99.0, 104.0, 98.0, 103.0)])
    first = resolve_versioned_call(row, bars)
    second = resolve_versioned_call(row, bars.copy())
    assert first == second
    assert first is not None
    assert first.outcome == "target" and first.label == 1
    assert first.entry_on == "2026-01-02" and first.holding_sessions == 1
    assert first.gross_r == pytest.approx(0.75)
    assert first.net_r is not None and first.net_r < first.execution_r < first.gross_r
    assert first.first_event == "target"


def test_same_bar_always_resolves_stop_before_target() -> None:
    result = resolve_versioned_call(_row(), _bars([(100.0, 105.0, 94.0, 102.0)]))
    assert result is not None
    assert result.outcome == "stop" and result.label == 0
    assert result.resolution_rule == "stop_before_target"


def test_entry_expiry_chase_and_invalidation_are_never_performance_labels() -> None:
    no_touch = _bars([(98, 99, 97, 98), (98, 99, 97, 98), (98, 99, 97, 98)])
    expired = resolve_versioned_call(_row(), no_touch)
    assert expired is not None
    assert expired.outcome == "never_triggered" and expired.label is None
    chased = resolve_versioned_call(_row(), _bars([(103, 104, 102, 103)]))
    assert chased is not None and chased.outcome == "chased" and chased.label is None
    invalidated = resolve_versioned_call(_row(), _bars([(98, 99, 94, 94)]))
    assert invalidated is not None
    assert invalidated.outcome == "invalidated" and invalidated.label is None


def test_timeout_waits_for_full_contract_window() -> None:
    two = _bars([(99, 101, 98, 100), (100, 101, 98, 100)])
    assert resolve_versioned_call(_row(), two) is None
    three = _bars([(99, 101, 98, 100), (100, 101, 98, 100), (100, 101, 98, 100.5)])
    result = resolve_versioned_call(_row(), three)
    assert result is not None
    assert result.outcome == "timeout" and result.holding_sessions == 3


def test_missing_observed_candle_fails_closed_as_invalid() -> None:
    bars = _bars([(99, 101, 98, 100), (100, float("nan"), 98, 100)])
    result = resolve_versioned_call(_row(), bars)
    assert result is not None
    assert result.outcome == "unavailable"
    assert result.outcome_state == "invalid_call" and result.label is None


def test_swing_contract_uses_its_own_target_and_holding_window() -> None:
    row = _row(version="swing-v1")
    bars = _bars([(99, 101, 98, 100), (100, 110.5, 99, 110)])
    result = resolve_versioned_call(row, bars)
    assert result is not None
    assert result.outcome == "target"
    assert result.gross_r == pytest.approx(2.0)
    assert result.holding_sessions == 2


def test_crypto_has_reporting_only_after_tax_r() -> None:
    result = resolve_versioned_call(
        _row(market="crypto"), _bars([(99, 104, 98, 103)])
    )
    assert result is not None and result.after_tax_r is not None
    assert result.after_tax_r < result.net_r


def test_tracker_routes_versioned_rows_to_deterministic_resolver() -> None:
    row = _row(version="swing-v1")
    bars = _bars([(99, 101, 98, 100), (100, 111, 99, 110)])

    class Store:
        def load(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return bars

    resolved = resolve_outcomes(Store(), {row.signal_id: row}, max_hold=999)  # type: ignore[arg-type]
    assert resolved == [row]
    assert row.outcome == "target" and row.gross_r == pytest.approx(2.0)
    assert row.net_r is not None and row.first_event == "target"
    assert row.resolved_at == "2026-01-03"
