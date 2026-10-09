from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.intraday_contract_race import (
    _aggregate_frame,
    _attach_windows,
    _contract_metrics,
    _frame_payload,
    load_intraday_contract_status,
    replay_contract,
    validate_intraday_path,
)


def _minute_frame(start: pd.Timestamp, count: int, *, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for index in range(count):
        open_price = base + index * 0.01
        rows.append(
            {
                "ts": start + pd.Timedelta(minutes=index),
                "open": open_price,
                "high": open_price + 0.10,
                "low": open_price - 0.10,
                "close": open_price + 0.02,
                "volume": 100 + index,
            }
        )
    return pd.DataFrame(rows).set_index("ts")


def test_intraday_path_requires_exact_grid_and_cross_interval_agreement() -> None:
    start = pd.Timestamp("2026-10-09 09:15", tz=IST)
    end = start + pd.Timedelta(minutes=60)
    m1 = _minute_frame(start, 60)
    m5 = _aggregate_frame(m1, 5, start=start.to_pydatetime())
    m15 = _aggregate_frame(m1, 15, start=start.to_pydatetime())

    assert validate_intraday_path(
        m1, m5, m15, start=start.to_pydatetime(), end=end.to_pydatetime()
    ) == (True, "complete_and_consistent")

    broken = m5.copy()
    broken.iloc[0, broken.columns.get_loc("high")] += 1.0
    valid, detail = validate_intraday_path(
        m1, broken, m15, start=start.to_pydatetime(), end=end.to_pydatetime()
    )
    assert valid is False
    assert detail == "m5_high_aggregate_mismatch"


def test_manifest_window_binding_normalises_duckdb_timestamp_to_date(tmp_path: Path) -> None:
    db = tmp_path / "market.duckdb"
    candles = [
        Candle(
            scrip_code="NSE_1",
            interval=Interval.D1,
            ts=datetime(2026, 10, day, tzinfo=IST),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000,
        )
        for day in (8, 9)
    ]
    with CandleStore(db) as store:
        store.upsert_candles(candles)
    row = {
        "session": "2026-10-08",
        "scrip_code": "NSE_1",
        "symbol": "ONE",
    }
    attached = _attach_windows(db, "nse", [row])
    assert attached[0]["window_status"] == "sealed"
    assert attached[0]["window_start"].startswith("2026-10-09T09:15:00")
    assert attached[0]["window_end"].startswith("2026-10-09T15:30:00")


class _ZeroCost:
    slippage_pct = Decimal("0")

    def net_r_multiple(
        self,
        *,
        trade_type: object,
        qty: float,
        entry: Decimal,
        stop: Decimal,
        exit_price: Decimal,
    ) -> Decimal:
        return (exit_price - entry) / (entry - stop)


def test_replay_uses_conservative_stop_first_order(monkeypatch) -> None:
    start = pd.Timestamp("2026-10-09 09:15", tz=IST)
    m1 = _minute_frame(start, 30)
    m1.iloc[0, m1.columns.get_loc("open")] = 100.0
    m1.iloc[0, m1.columns.get_loc("high")] = 101.0
    m1.iloc[0, m1.columns.get_loc("low")] = 98.0
    monkeypatch.setattr(
        "tradedesk.intraday_contract_race._market_bundle",
        lambda market: SimpleNamespace(
            costs=_ZeroCost(), qty_step=1.0, min_notional_inr=0.0
        ),
    )
    path = {
        "path_status": "valid",
        "market": "nse",
        "decision_close": 100.0,
        "atr_14_pct": 0.02,
        "m1": _frame_payload(m1),
    }

    replay = replay_contract(path, entry_rule="next_open", target_r=0.50)

    assert replay["status"] == "resolved"
    assert replay["event"] == "stop"
    assert replay["strict_success"] is False
    assert replay["net_r"] == -1.0


def test_pullback_without_positive_gap_is_an_explicit_no_call(monkeypatch) -> None:
    start = pd.Timestamp("2026-10-09 09:15", tz=IST)
    m1 = _minute_frame(start, 30, base=99.0)
    monkeypatch.setattr(
        "tradedesk.intraday_contract_race._market_bundle",
        lambda market: SimpleNamespace(
            costs=_ZeroCost(), qty_step=1.0, min_notional_inr=0.0
        ),
    )
    replay = replay_contract(
        {
            "path_status": "valid",
            "market": "nse",
            "decision_close": 100.0,
            "atr_14_pct": 0.02,
            "m1": _frame_payload(m1),
        },
        entry_rule="prior_close_pullback",
        target_r=0.50,
    )
    assert replay["status"] == "never_triggered"
    assert replay["strict_success"] is None
    assert replay["net_r"] is None


def test_invalid_paths_do_not_enter_performance_denominator() -> None:
    rows = [
        {
            "session": "2026-10-01",
            "scrip_code": "NSE_1",
            "path_status": "valid",
            "replay": {
                "status": "resolved",
                "strict_success": True,
                "net_r": 0.3,
                "event": "target",
            },
        },
        {
            "session": "2026-10-02",
            "scrip_code": "NSE_2",
            "path_status": "invalid_or_unavailable",
            "replay": {
                "status": "invalid_or_unavailable",
                "strict_success": None,
                "net_r": None,
                "event": "missing",
            },
        },
    ]
    metrics = _contract_metrics(rows, total_sessions=2)
    assert metrics["valid_paths"] == 1
    assert metrics["invalid_paths"] == 1
    assert metrics["filled"] == 1
    assert metrics["strict_accuracy"] == 1.0


def test_status_fails_closed_without_manifest(tmp_path: Path) -> None:
    status = load_intraday_contract_status("nse", output_root=tmp_path)
    assert status["status"] == "not_started"
    assert status["prospective"]["status"] == "not_registered"
    assert status["eligible_for_live"] is False
    assert status["baseline_accuracy_improved"] is False

