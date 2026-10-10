from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import IST, Candle, Interval
from tradedesk.data.candle_store import CandleStore
from tradedesk.intraday_contract_race import (
    _aggregate_frame,
    _attach_windows,
    _contract_metrics,
    _expected_index,
    _fetch_with_invalid_code_isolation,
    _frame_payload,
    _load_path_record,
    _matched_random_control,
    _same_timestamp_grid,
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


def test_expected_grid_normalises_fixed_offset_manifest_times_to_ist() -> None:
    start = datetime.fromisoformat("2026-07-24T05:30:00+05:30")
    end = datetime.fromisoformat("2026-07-24T05:35:00+05:30")
    actual = pd.DatetimeIndex(
        list(
            pd.date_range(
                start=pd.Timestamp("2026-07-24 05:30", tz=IST),
                periods=5,
                freq="1min",
            )
        )
    )

    expected = _expected_index(start, end, 1)

    assert expected.tz == IST
    assert actual.freq is None
    assert _same_timestamp_grid(actual, expected)


def test_crypto_path_uses_derived_m15_and_audits_observed_disagreement(
    tmp_path: Path,
) -> None:
    start = pd.Timestamp("2026-07-24 05:30", tz=IST)
    end = start + pd.Timedelta(minutes=60)
    m1 = _minute_frame(start, 60)
    m5 = _aggregate_frame(m1, 5, start=start.to_pydatetime())
    observed_m15 = _aggregate_frame(m1, 15, start=start.to_pydatetime())
    observed_m15.iloc[0, observed_m15.columns.get_loc("open")] += 0.05

    def candles(interval: Interval, frame: pd.DataFrame) -> list[Candle]:
        return [
            Candle(
                scrip_code="CDX_TESTINR",
                interval=interval,
                ts=timestamp.to_pydatetime(),
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
                close=float(bar["close"]),
                volume=int(bar["volume"]),
            )
            for timestamp, bar in frame.iterrows()
        ]

    db = tmp_path / "crypto.duckdb"
    with CandleStore(db) as store:
        store.upsert_candles(candles(Interval.M1, m1))
        store.upsert_candles(candles(Interval.M5, m5))
        store.upsert_candles(candles(Interval.M15, observed_m15))
        record = _load_path_record(
            store,
            {
                "market": "crypto",
                "block": "validation",
                "session": "2026-07-23",
                "scrip_code": "CDX_TESTINR",
                "symbol": "TESTINR",
                "roles": ["ranker_top_1"],
                "decision_close": 100.0,
                "atr_14_pct": 0.02,
                "ranker_score": 0.75,
                "return_20_rank": 1.0,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "window_status": "sealed",
            },
        )

    assert record["path_status"] == "valid"
    assert record["m15_source"] == "derived_from_observed_m1"
    assert record["observed_m15_audit"]["status"] == "feed_disagreement"
    assert record["observed_m15_audit"]["mismatch_bars_by_field"]["open"] == 1
    assert record["m15"][0][1] == pytest.approx(float(m1.iloc[0]["open"]))


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


@pytest.mark.asyncio
async def test_invalid_scrip_is_isolated_without_losing_healthy_neighbours() -> None:
    start = datetime(2026, 10, 9, 9, 15, tzinfo=IST)
    end = datetime(2026, 10, 9, 15, 30, tzinfo=IST)

    class Client:
        async def candles_history(self, interval, codes, start, end):  # noqa: ANN001
            if "NSE_BAD" in codes:
                raise RuntimeError("HTTP 400: Invalid scrip codes")
            return {code: [] for code in codes}

    fetched, errors = await asyncio.wait_for(
        _fetch_with_invalid_code_isolation(
            Client(), Interval.M1, ["NSE_GOOD", "NSE_BAD", "NSE_OK"], start, end
        ),
        timeout=2.0,
    )
    assert set(fetched) == {"NSE_GOOD", "NSE_BAD", "NSE_OK"}
    assert [error["scrip_code"] for error in errors] == ["NSE_BAD"]


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


def test_matched_random_uses_exact_smaller_sealed_session_sets() -> None:
    m1 = _frame_payload(
        _minute_frame(pd.Timestamp("2026-10-09 09:15", tz=IST), 2)
    )
    paths = []
    for session in ("2026-10-01", "2026-10-02"):
        paths.append(
            {
                "session": session,
                "scrip_code": f"{session}-primary",
                "roles": ["ranker_top_1"],
                "path_status": "valid",
                "market": "nse",
                "decision_close": 100.0,
                "atr_14_pct": 0.02,
                "m1": m1,
            }
        )
        for index in range(3):
            paths.append(
                {
                    "session": session,
                    "scrip_code": f"{session}-random-{index}",
                    "roles": [f"random_{index + 1:02d}"],
                    "path_status": "valid",
                    "market": "nse",
                    "decision_close": 100.0,
                    "atr_14_pct": 0.02,
                    "m1": m1,
                }
            )

    result = _matched_random_control(
        paths,
        entry_rule="next_open",
        target_r=0.50,
        observed={"strict_accuracy": 0.0, "mean_net_r": 0.0},
        seed=1701,
    )

    assert result["status"] == "available"
    assert result["sessions"] == 2


def test_status_fails_closed_without_manifest(tmp_path: Path) -> None:
    status = load_intraday_contract_status("nse", output_root=tmp_path)
    assert status["status"] == "not_started"
    assert status["prospective"]["status"] == "not_registered"
    assert status["eligible_for_live"] is False
    assert status["baseline_accuracy_improved"] is False

