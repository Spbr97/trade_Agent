from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import tradedesk.pre_takeoff_watch as pre_takeoff_watch
from tradedesk.broker.indstocks.models import IST
from tradedesk.broker.indstocks.rest import TransportStats
from tradedesk.pre_takeoff_watch import (
    _append_plan_event,
    _candidate_state,
    _evaluate_plan,
    _intraday_features,
    _load_plan_events,
    _update_watch_plans,
    build_pre_takeoff_watch,
    collect_crypto_pre_takeoff_watch,
    collect_equity_pre_takeoff_watch,
)


def _compression_frame(*, broken_out: bool = False) -> pd.DataFrame:
    index = pd.date_range("2026-10-10 00:00", periods=40, freq="15min", tz=IST)
    close = np.linspace(100.0, 102.0, 40)
    close[-4:] = [102.0, 102.4, 102.8, 103.2]
    high = close + 1.0
    low = close - 1.0
    high[-10] = 104.0
    for offset in range(4, 0, -1):
        position = -offset
        high[position] = close[position] + 0.1
        low[position] = close[position] - 0.4
    volume = np.full(40, 100.0)
    volume[-1] = 150.0
    if broken_out:
        close[-1] = 105.0
        high[-1] = 105.2
        low[-1] = 104.5
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def _shortlist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "scrip_code": "CDX_TESTINR",
                "symbol": "TESTINR",
                "opportunity_rank": 1,
                "opportunity_probability": 0.8,
            }
        ]
    )


def test_candidate_is_arming_only_before_breakout() -> None:
    compressed = _intraday_features(_compression_frame())
    assert compressed is not None
    state, blockers = _candidate_state(compressed, "crypto")
    assert state == "arming"
    assert blockers == []

    broken = _intraday_features(_compression_frame(broken_out=True))
    assert broken is not None
    state, blockers = _candidate_state(broken, "crypto")
    assert state == "already_moving"
    assert "already_above_prior_high" in blockers


def test_undefined_liquidity_feature_is_excluded_not_passed() -> None:
    frame = _compression_frame()
    frame["volume"] = 0.0
    assert _intraday_features(frame) is None


def test_watch_report_never_presents_an_untriggered_watch_as_a_trade() -> None:
    frame = _compression_frame()
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    report = build_pre_takeoff_watch(
        "crypto", _shortlist(), {"CDX_TESTINR": frame}, observed_at=observed_at
    )
    assert report["status"] == "watch_candidates_found"
    assert report["watch_candidates"] == 1
    assert report["tradeable_calls"] == 0
    candidate = report["candidates"][0]
    assert candidate["state"] == "arming"
    assert candidate["tradeable_now"] is False
    assert candidate["trigger"] > candidate["features"]["last_close"]


def test_watch_can_scan_an_active_pair_without_daily_rank_evidence() -> None:
    frame = _compression_frame()
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    shortlist = _shortlist()
    shortlist.loc[0, "opportunity_probability"] = None
    report = build_pre_takeoff_watch(
        "crypto", shortlist, {"CDX_TESTINR": frame}, observed_at=observed_at
    )
    candidate = report["candidates"][0]
    assert candidate["daily_rank_available"] is False
    assert candidate["opportunity_probability"] is None
    assert candidate["tradeable_now"] is False


def test_mixed_rank_availability_never_serializes_nan() -> None:
    frame = _compression_frame()
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    shortlist = pd.DataFrame(
        [
            {
                "scrip_code": "CDX_RANKEDINR",
                "symbol": "RANKEDINR",
                "opportunity_rank": 1,
                "opportunity_probability": 0.8,
            },
            {
                "scrip_code": "CDX_UNRANKEDINR",
                "symbol": "UNRANKEDINR",
                "opportunity_rank": 2,
                "opportunity_probability": None,
            },
        ]
    )
    report = build_pre_takeoff_watch(
        "crypto",
        shortlist,
        {"CDX_RANKEDINR": frame, "CDX_UNRANKEDINR": frame},
        observed_at=observed_at,
    )
    by_code = {candidate["scrip_code"]: candidate for candidate in report["candidates"]}
    assert by_code["CDX_RANKEDINR"]["opportunity_probability"] == 0.8
    assert by_code["CDX_UNRANKEDINR"]["opportunity_probability"] is None


def test_stablecoin_is_never_armed_as_a_directional_crypto_watch() -> None:
    frame = _compression_frame()
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    shortlist = _shortlist().assign(scrip_code="CDX_USDTINR", symbol="USDTINR")
    report = build_pre_takeoff_watch(
        "crypto", shortlist, {"CDX_USDTINR": frame}, observed_at=observed_at
    )
    candidate = report["candidates"][0]
    assert candidate["state"] == "observe"
    assert "non_directional_stablecoin" in candidate["blockers"]
    assert report["watch_candidates"] == 0


def test_existing_pending_stablecoin_plan_is_invalidated_before_trigger(
    tmp_path,
) -> None:
    frame = _compression_frame()
    plan = _plan(frame)
    plan["plan_id"] = "stablecoin-plan"
    plan["scrip_code"] = "CDX_USDTINR"
    plan["symbol"] = "USDTINR"
    _append_plan_event(tmp_path / "crypto" / "plans.jsonl", plan)
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    report = build_pre_takeoff_watch(
        "crypto",
        _shortlist().assign(scrip_code="CDX_USDTINR", symbol="USDTINR"),
        {"CDX_USDTINR": frame},
        observed_at=observed_at,
    )
    summary = _update_watch_plans(
        report,
        {"CDX_USDTINR": frame},
        observed_at=observed_at,
        output_root=tmp_path,
    )
    assert summary["states"] == {"invalidated": 1}
    events = _load_plan_events(tmp_path / "crypto" / "plans.jsonl")
    assert events[-1]["resolution_reason"] == (
        "non_directional_asset_excluded_before_trigger"
    )


def test_feature_snapshot_excludes_unclosed_bar() -> None:
    frame = _compression_frame()
    observed_at = datetime(2026, 10, 10, 9, 45, tzinfo=IST)
    report = build_pre_takeoff_watch(
        "crypto",
        _shortlist(),
        {
            "CDX_TESTINR": pd.concat(
                [
                    frame,
                    pd.DataFrame(
                        [{"open": 200, "high": 220, "low": 190, "close": 210, "volume": 9999}],
                        index=[pd.Timestamp("2026-10-10 09:45", tz=IST)],
                    ),
                ]
            )
        },
        observed_at=observed_at,
    )
    # The 09:45 bar closes at 10:00 and must not enter a 09:45 decision.
    latest = report["candidates"][0]["features"]
    assert latest["last_close"] != 210


def _plan(frame: pd.DataFrame) -> dict[str, object]:
    decision_bar = frame.index[-1].isoformat()
    return {
        "version": "pre-takeoff-watch-plan-v1",
        "event": "plan_armed",
        "plan_id": "test-plan",
        "market": "crypto",
        "scrip_code": "CDX_TESTINR",
        "symbol": "TESTINR",
        "armed_at": decision_bar,
        "decision_bar": decision_bar,
        "last_evaluated_bar": decision_bar,
        "state": "pending_trigger",
        "bars_seen": 0,
        "trigger_bars_seen": 0,
        "trigger": 104.0,
        "stop": 102.0,
        "quick_target_075r": 105.5,
        "pre_takeoff_score": 0.7,
        "opportunity_probability": 0.8,
        "source_snapshot_sha256": "snapshot",
        "observed_at": decision_bar,
        "authority": "research_watch_only",
    }


def _append_bar(
    frame: pd.DataFrame,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> pd.DataFrame:
    timestamp = frame.index[-1] + timedelta(minutes=15)
    return pd.concat(
        [
            frame,
            pd.DataFrame(
                [
                    {
                        "open": open_price,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": 200.0,
                    }
                ],
                index=[timestamp],
            ),
        ]
    )


def test_plan_cannot_trigger_on_the_decision_bar() -> None:
    frame = _compression_frame()
    plan = _plan(frame)
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [
        103.0,
        106.0,
        101.0,
        105.0,
    ]
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    assert _evaluate_plan(plan, frame, observed_at=observed_at) is None


def test_plan_uses_conservative_same_bar_trigger_and_stop_resolution() -> None:
    frame = _compression_frame()
    plan = _plan(frame)
    future = _append_bar(
        frame, open_price=103.5, high=104.5, low=101.5, close=103.0
    )
    observed_at = future.index[-1].to_pydatetime() + timedelta(minutes=15)
    result = _evaluate_plan(plan, future, observed_at=observed_at)
    assert result is not None
    assert result["state"] == "invalidated"
    assert result["resolution_reason"] == "ambiguous_trigger_and_stop_same_bar"
    assert "entry_execution" not in result


def test_plan_resolves_a_future_clean_target_after_costs() -> None:
    frame = _compression_frame()
    plan = _plan(frame)
    triggered = _append_bar(
        frame, open_price=103.5, high=104.3, low=102.5, close=104.1
    )
    future = _append_bar(
        triggered, open_price=104.2, high=105.7, low=103.8, close=105.6
    )
    observed_at = future.index[-1].to_pydatetime() + timedelta(minutes=15)
    result = _evaluate_plan(plan, future, observed_at=observed_at)
    assert result is not None
    assert result["state"] == "target"
    assert result["net_r"] is not None
    assert result["strict_success"] is (float(result["net_r"]) > 0)


def test_update_watch_plans_persists_a_hash_chained_immutable_plan(tmp_path) -> None:
    frame = _compression_frame()
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    report = build_pre_takeoff_watch(
        "crypto", _shortlist(), {"CDX_TESTINR": frame}, observed_at=observed_at
    )
    summary = _update_watch_plans(
        report,
        {"CDX_TESTINR": frame},
        observed_at=observed_at,
        output_root=tmp_path,
    )
    assert summary["states"] == {"pending_trigger": 1}
    assert summary["active_plans"] == 1
    assert summary["resolved_trades"] == 0
    events = _load_plan_events(tmp_path / "crypto" / "plans.jsonl")
    assert len(events) == 1
    assert events[0]["trigger"] == report["candidates"][0]["trigger"]
    assert events[0]["stop"] == report["candidates"][0]["stop"]


def _equity_shortlist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "scrip_code": "NSE_1",
                "symbol": "ONE",
                "opportunity_rank": 1,
                "opportunity_probability": 0.8,
            },
            {
                "scrip_code": "NSE_2",
                "symbol": "TWO",
                "opportunity_rank": 2,
                "opportunity_probability": 0.7,
            },
        ]
    )


class _FakeEquityClient:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.requests: list[tuple[object, list[str], datetime, datetime]] = []

    async def candles_history(self, interval, codes, start, end, **_kwargs):
        self.requests.append((interval, list(codes), start, end))
        return {code: self.frame.copy() for code in codes}


class _FakeCoinDcxClient:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.registered: dict[str, str] = {}
        self.candles_history_calls = 0
        self.candle_rows_history_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def instruments(self):
        return [
            SimpleNamespace(
                scrip_code="CDX_TESTINR",
                custom_symbol="TEST_INR",
                trading_symbol="TESTINR",
                symbol_name="TEST",
            )
        ]

    def register_pairs(self, pairs: dict[str, str]) -> None:
        self.registered = dict(pairs)

    async def candles_history(self, interval, codes, start, end):
        self.candles_history_calls += 1
        return {code: self.frame.copy() for code in codes}

    async def candle_rows_history(self, interval, codes, start, end):
        self.candle_rows_history_calls += 1
        rows = [
            {
                "time": int(timestamp.timestamp() * 1000),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
            }
            for timestamp, bar in self.frame.iterrows()
        ]
        return {code: list(rows) for code in codes}


def test_crypto_collector_updates_m20_and_isolated_m21_from_one_fetch(
    tmp_path, monkeypatch
) -> None:
    from tradedesk.broker import coindcx
    from tradedesk.markets import crypto_universe

    frame = _compression_frame()
    frame["volume"] += 0.4
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    client = _FakeCoinDcxClient(frame)
    monkeypatch.setattr(coindcx, "CoinDcxClient", lambda: client)
    monkeypatch.setattr(
        crypto_universe,
        "active_crypto_codes",
        lambda instruments: {"CDX_TESTINR"},
    )
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (
            _shortlist(),
            {"full_universe_rows": 1, "shortlist_rows": 1, "ranker_sha256": "test"},
        ),
    )
    m20_root = tmp_path / "m20"
    m21_root = tmp_path / "m21"
    m22_root = tmp_path / "m22"

    report = asyncio.run(
        collect_crypto_pre_takeoff_watch(
            output_root=m20_root,
            momentum_output_root=m21_root,
            momentum_m22_output_root=m22_root,
            observed_at=observed_at,
        )
    )

    assert report["version"] == "pre-takeoff-watch-v1"
    assert report["requested_pairs"] == 1
    assert client.registered == {"CDX_TESTINR": "TEST_INR"}
    assert client.candle_rows_history_calls == 1
    assert client.candles_history_calls == 0
    m20_latest = json.loads((m20_root / "crypto" / "latest.json").read_text())
    m21_latest = json.loads((m21_root / "crypto" / "latest.json").read_text())
    m22_latest = json.loads((m22_root / "crypto" / "latest.json").read_text())
    assert m20_latest["snapshot_sha256"] == report["snapshot_sha256"]
    assert m20_latest["candidates"][0]["features"]["average_notional_20"] == pytest.approx(
        np.mean(frame["close"].iloc[-20:] * np.round(frame["volume"].iloc[-20:]))
    )
    assert m21_latest["version"] == "momentum-ignition-watch-v1"
    assert m21_latest["source"]["shared_m15_fetch_with_m20"] is True
    assert (
        m21_latest["source"]["m15_volume_contract"]
        == "coindcx_raw_fractional_base_asset_v1"
    )
    assert m21_latest["candidates"][0]["features"]["prior_volume_20"] == pytest.approx(100.4)
    assert m22_latest["version"] == "momentum-ignition-watch-m22-v1"
    assert m22_latest["source"]["shared_m15_fetch_with_m20"] is True
    assert m22_latest["source"]["m15_volume_contract"] == (
        "coindcx_raw_fractional_base_asset_v1"
    )
    assert m22_latest["coverage_gate_passed"] is False
    assert (m22_root / "crypto" / "activation.json").exists()
    assert (m20_root / "crypto" / "plans.jsonl").exists()
    assert not (m20_root / "momentum_ignition_watch" / "crypto" / "latest.json").exists()


def test_equity_collector_scans_full_ranked_universe_and_arms_in_session(
    tmp_path, monkeypatch
) -> None:
    frame = _compression_frame()
    frame.index = pd.date_range("2026-10-09 00:00", periods=40, freq="15min", tz=IST)
    observed_at = datetime(2026, 10, 9, 10, 1, tzinfo=IST)
    shortlist = _equity_shortlist()
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (
            shortlist,
            {"full_universe_rows": len(shortlist), "shortlist_rows": len(shortlist)},
        ),
    )
    client = _FakeEquityClient(frame)

    report = asyncio.run(
        collect_equity_pre_takeoff_watch(
            "nse", client=client, output_root=tmp_path, observed_at=observed_at
        )
    )

    assert report["universe_scope"] == "full_daily_liquid_eligible"
    assert report["requested_pairs"] == 2
    assert report["intraday_valid_rows"] == 2
    assert report["new_plan_registration_open"] is True
    assert report["watch_plans"]["new_plans_registered"] == 2
    assert report["tradeable_calls"] == 0
    assert client.requests[0][1] == ["NSE_1", "NSE_2"]
    assert client.requests[0][3] - client.requests[0][2] == timedelta(days=7)
    assert (
        tmp_path
        / "equity_impulse_continuation_m23"
        / "nse"
        / "latest.json"
    ).exists()


def test_equity_collector_never_arms_retrospectively_when_market_is_closed(
    tmp_path, monkeypatch
) -> None:
    frame = _compression_frame()
    frame.index = pd.date_range("2026-10-10 00:00", periods=40, freq="15min", tz=IST)
    observed_at = datetime(2026, 10, 10, 10, 1, tzinfo=IST)
    shortlist = _equity_shortlist()
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (
            shortlist,
            {"full_universe_rows": len(shortlist), "shortlist_rows": len(shortlist)},
        ),
    )

    report = asyncio.run(
        collect_equity_pre_takeoff_watch(
            "nse",
            client=_FakeEquityClient(frame),
            output_root=tmp_path,
            observed_at=observed_at,
        )
    )

    assert report["new_plan_registration_open"] is False
    assert report["watch_plans"]["new_plans_registered"] == 0
    assert report["watch_plans"]["total_plans"] == 0
    assert not (tmp_path / "nse" / "plans.jsonl").exists()


def test_invalid_detail_explains_exclusions_without_touching_the_report() -> None:
    observed_at = datetime(2026, 10, 10, 12, 0, tzinfo=IST)
    frame = _compression_frame()
    frames = {
        "NSE_1": frame,
        "NSE_2": frame.tail(10),  # too few completed bars
        "NSE_3": [],  # what candles_history returns for a code with no data
    }
    shortlist = pd.concat(
        [
            _equity_shortlist(),
            pd.DataFrame(
                [
                    {
                        "scrip_code": "NSE_3",
                        "symbol": "THREE",
                        "opportunity_rank": 3,
                        "opportunity_probability": 0.6,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    detail: dict[str, dict[str, object]] = {}
    with_detail = build_pre_takeoff_watch(
        "nse", shortlist, frames, observed_at=observed_at, invalid_detail=detail
    )
    without_detail = build_pre_takeoff_watch("nse", shortlist, frames, observed_at=observed_at)

    assert with_detail == without_detail  # diagnostics never leak into the evidence report
    assert with_detail["intraday_invalid_rows"] == 2
    assert detail["NSE_2"]["reason"] == "too_few_completed_bars"
    assert detail["NSE_2"]["fetched_bars"] == 10
    assert detail["NSE_3"]["reason"] == "no_candles_returned"
    assert "NSE_1" not in detail


def test_equity_collector_writes_timing_and_exclusion_diagnostics(
    tmp_path, monkeypatch
) -> None:
    frame = _compression_frame()
    frame.index = pd.date_range("2026-10-09 00:00", periods=40, freq="15min", tz=IST)
    observed_at = datetime(2026, 10, 9, 10, 1, tzinfo=IST)
    shortlist = _equity_shortlist()
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (
            shortlist,
            {"full_universe_rows": len(shortlist), "shortlist_rows": len(shortlist)},
        ),
    )

    class _CountingClient(_FakeEquityClient):
        def __init__(self, frame: pd.DataFrame) -> None:
            super().__init__(frame)
            self.stats = TransportStats()

        async def candles_history(self, interval, codes, start, end, **kwargs):
            self.stats.requests += 3
            self.stats.api_errors += 1
            result = await super().candles_history(interval, codes, start, end, **kwargs)
            result["NSE_2"] = []  # one shortlisted code returns nothing
            return result

    report = asyncio.run(
        collect_equity_pre_takeoff_watch(
            "nse", client=_CountingClient(frame), output_root=tmp_path, observed_at=observed_at
        )
    )

    lines = (tmp_path / "nse" / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary["market"] == "nse"
    assert summary["requested_codes"] == 2 and summary["codes_with_candles"] == 1
    assert summary["transport"]["requests"] == 3 and summary["transport"]["api_errors"] == 1
    assert summary["invalid_rows"] == 1
    assert summary["invalid_by_reason"] == {"no_candles_returned": 1}
    assert set(summary["seconds"]) == {"shortlist", "fetch", "plans", "m23", "total"}
    latest = json.loads((tmp_path / "nse" / "diagnostics_latest.json").read_text("utf-8"))
    assert latest["invalid_codes"]["NSE_2"]["reason"] == "no_candles_returned"
    assert "invalid_codes" not in summary
    # The evidence report itself carries none of it.
    assert "seconds" not in report and "transport" not in report


def test_equity_collector_diagnostics_failure_never_fails_the_slot(
    tmp_path, monkeypatch
) -> None:
    frame = _compression_frame()
    frame.index = pd.date_range("2026-10-09 00:00", periods=40, freq="15min", tz=IST)
    shortlist = _equity_shortlist()
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (shortlist, {"full_universe_rows": 2, "shortlist_rows": 2}),
    )
    real_atomic = pre_takeoff_watch._atomic_json

    def selective(path, value):
        if path.name == "diagnostics_latest.json":
            raise OSError("disk full")
        real_atomic(path, value)

    monkeypatch.setattr(pre_takeoff_watch, "_atomic_json", selective)

    report = asyncio.run(
        collect_equity_pre_takeoff_watch(
            "nse",
            client=_FakeEquityClient(frame),
            output_root=tmp_path,
            observed_at=datetime(2026, 10, 9, 10, 1, tzinfo=IST),
        )
    )

    assert report["watch_plans"]["new_plans_registered"] == 2
    assert (tmp_path / "nse" / "latest.json").exists()


def test_equity_collector_keeps_session_bars_for_my_picks_and_survives_failure(
    tmp_path, monkeypatch
) -> None:
    import tradedesk.my_picks as my_picks

    frame = _compression_frame()
    frame.index = pd.date_range("2026-10-09 00:00", periods=40, freq="15min", tz=IST)
    shortlist = _equity_shortlist()
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (shortlist, {"full_universe_rows": 2, "shortlist_rows": 2}),
    )
    calls = []
    monkeypatch.setattr(
        my_picks,
        "write_intraday_bars",
        lambda market, candles, **kw: calls.append((market, sorted(candles), kw)),
    )
    observed_at = datetime(2026, 10, 9, 10, 1, tzinfo=IST)
    asyncio.run(
        collect_equity_pre_takeoff_watch(
            "nse", client=_FakeEquityClient(frame), output_root=tmp_path, observed_at=observed_at
        )
    )
    assert calls == [
        ("nse", ["NSE_1", "NSE_2"], {"observed_at": observed_at, "output_root": tmp_path})
    ]

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(my_picks, "write_intraday_bars", boom)
    report = asyncio.run(
        collect_equity_pre_takeoff_watch(
            "nse",
            client=_FakeEquityClient(frame),
            output_root=tmp_path / "again",
            observed_at=observed_at,
        )
    )
    assert report["tradeable_calls"] == 0


def test_production_collector_offers_bars_to_the_intraday_study_and_survives_failure(
    tmp_path, monkeypatch
) -> None:
    import tradedesk_lab.intraday_mover_study as ims

    frame = _compression_frame()
    frame.index = pd.date_range("2026-10-09 00:00", periods=40, freq="15min", tz=IST)
    shortlist = _equity_shortlist()
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *args, **kwargs: (shortlist, {"full_universe_rows": 2, "shortlist_rows": 2}),
    )
    monkeypatch.setattr(pre_takeoff_watch, "DEFAULT_OUTPUT_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(ims, "register_live", lambda market, candles, **kw: calls.append(market))
    observed_at = datetime(2026, 10, 9, 10, 16, tzinfo=IST)

    def collect(root):
        return asyncio.run(
            collect_equity_pre_takeoff_watch(
                "nse", client=_FakeEquityClient(frame), output_root=root, observed_at=observed_at
            )
        )

    collect(tmp_path)
    collect(tmp_path / "isolated")  # a non-production output root never touches the study
    assert calls == ["nse"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("study broke")

    monkeypatch.setattr(ims, "register_live", boom)
    assert collect(tmp_path)["tradeable_calls"] == 0


def test_production_crypto_collector_offers_exact_volume_bars_to_the_intraday_study(
    tmp_path, monkeypatch
) -> None:
    import tradedesk_lab.intraday_mover_study as ims
    from tradedesk_lab.intraday_history import Bar

    from tradedesk.broker import coindcx
    from tradedesk.markets import crypto_universe

    frame = _compression_frame()
    frame["volume"] += 0.4  # fractional, as CoinDCX reports base-asset volume
    observed_at = frame.index[-1].to_pydatetime() + timedelta(minutes=15)
    monkeypatch.setattr(coindcx, "CoinDcxClient", lambda: _FakeCoinDcxClient(frame))
    monkeypatch.setattr(crypto_universe, "active_crypto_codes", lambda i: {"CDX_TESTINR"})
    monkeypatch.setattr(
        pre_takeoff_watch,
        "_load_opportunity_shortlist",
        lambda *a, **k: (_shortlist(), {"full_universe_rows": 1, "shortlist_rows": 1}),
    )
    monkeypatch.setattr(pre_takeoff_watch, "DEFAULT_OUTPUT_ROOT", tmp_path)
    seen = {}

    def capture(market, candles, **kw):
        seen["market"], seen["candles"] = market, candles

    monkeypatch.setattr(ims, "register_live", capture)
    asyncio.run(
        collect_crypto_pre_takeoff_watch(
            output_root=tmp_path,
            momentum_output_root=tmp_path / "m21",
            momentum_m22_output_root=tmp_path / "m22",
            observed_at=observed_at,
        )
    )
    assert seen["market"] == "crypto"
    bars = seen["candles"]["CDX_TESTINR"]
    assert bars and all(isinstance(b, Bar) for b in bars)
    assert any(b.volume != int(b.volume) for b in bars)  # exact volume, not rounded


def test_standalone_collector_script_can_import_shared_pipeline_lock(tmp_path) -> None:
    script = Path(__file__).parents[2] / "scripts" / "pre_takeoff_watch.py"
    completed = subprocess.run(
        [sys.executable, str(script), "status", "--market", "equity"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert set(result) == {"nse", "bse"}
    assert result["nse"]["status"] == "not_collected"
