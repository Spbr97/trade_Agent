from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import tradedesk.pre_takeoff_watch as pre_takeoff_watch
from tradedesk.broker.indstocks.models import IST
from tradedesk.pre_takeoff_watch import (
    _append_plan_event,
    _candidate_state,
    _evaluate_plan,
    _intraday_features,
    _load_plan_events,
    _update_watch_plans,
    build_pre_takeoff_watch,
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

    async def candles_history(self, interval, codes, start, end):
        self.requests.append((interval, list(codes), start, end))
        return {code: self.frame.copy() for code in codes}


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
