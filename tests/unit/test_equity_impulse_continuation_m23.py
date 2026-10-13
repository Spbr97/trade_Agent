from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import IST
from tradedesk.equity_impulse_continuation_m23 import (
    build_equity_impulse_continuation,
    collect_equity_impulse_continuation,
    evaluate_equity_impulse_evidence,
    load_equity_impulse_continuation,
)


def _shortlist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "scrip_code": "NSE_FAST",
                "symbol": "FAST",
                "opportunity_rank": 1,
                "opportunity_probability": 0.6,
            }
        ]
    )


def _impulse_frame(*, include_trigger: bool = False) -> pd.DataFrame:
    prior_index = pd.date_range("2026-10-09 09:15", periods=32, freq="15min", tz=IST)
    prior = pd.DataFrame(
        {
            "open": [99.5] * 32,
            "high": [100.5] * 32,
            "low": [99.0] * 32,
            "close": [100.0] * 32,
            "volume": [100.0] * 32,
        },
        index=prior_index,
    )
    current_index = pd.date_range("2026-10-12 09:15", periods=4, freq="15min", tz=IST)
    current = pd.DataFrame(
        {
            "open": [101.0, 103.0, 103.2, 103.5],
            "high": [103.5, 104.0, 103.9, 104.0],
            "low": [100.5, 101.0, 102.5, 103.0],
            "close": [103.0, 103.5, 103.6, 103.8],
            "volume": [400.0, 180.0, 170.0, 160.0],
        },
        index=current_index,
    )
    if include_trigger:
        trigger = pd.DataFrame(
            {
                "open": [104.2],
                "high": [107.0],
                "low": [103.0],
                "close": [106.5],
                "volume": [300.0],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2026-10-12 10:15", tz=IST)]),
        )
        current = pd.concat([current, trigger])
    return pd.concat([prior, current])


def test_candidate_uses_completed_bars_and_ignores_future_trigger() -> None:
    frame = _impulse_frame(include_trigger=True)
    observed = datetime(2026, 10, 12, 10, 16, tzinfo=IST)
    report = build_equity_impulse_continuation(
        "nse", _shortlist(), {"NSE_FAST": frame}, observed_at=observed
    )

    assert report["valid_candidates"] == 1
    candidate = report["candidates"][0]
    assert candidate["features"]["completed_session_bars"] == 4
    assert candidate["features"]["session_high"] == pytest.approx(104.0)
    assert candidate["features"]["opening_return"] == pytest.approx(0.03)
    assert candidate["features"]["opening_volume_ratio"] == pytest.approx(4.0)
    assert candidate["trigger"] == pytest.approx(104.104)
    assert candidate["tradeable_now"] is False


def test_later_completed_bar_triggers_and_resolves_target(tmp_path) -> None:
    first_observed = datetime(2026, 10, 12, 10, 16, tzinfo=IST)
    first = collect_equity_impulse_continuation(
        "nse",
        _shortlist(),
        {"NSE_FAST": _impulse_frame()},
        observed_at=first_observed,
        output_root=tmp_path,
    )
    assert first["watch_plans"]["new_plans_registered"] == 1
    assert first["watch_plans"]["states"] == {"pending_trigger": 1}

    second = collect_equity_impulse_continuation(
        "nse",
        _shortlist(),
        {"NSE_FAST": _impulse_frame(include_trigger=True)},
        observed_at=datetime(2026, 10, 12, 10, 31, tzinfo=IST),
        output_root=tmp_path,
    )
    assert second["watch_plans"]["states"] == {"target": 1}
    assert second["watch_plans"]["evidence"]["metrics"]["resolved_trades"] == 1
    assert second["watch_plans"]["evidence"]["metrics"]["strict_successes"] == 1
    assert second["watch_plans"]["new_plans_registered"] == 0

    loaded = load_equity_impulse_continuation("nse", output_root=tmp_path)
    assert loaded["status"] == "watch_candidates_found"
    assert loaded["watch_plans"]["evidence"]["metrics"]["resolved_trades"] == 1


def test_same_bar_trigger_and_stop_is_invalid_not_a_loss(tmp_path) -> None:
    collect_equity_impulse_continuation(
        "nse",
        _shortlist(),
        {"NSE_FAST": _impulse_frame()},
        observed_at=datetime(2026, 10, 12, 10, 16, tzinfo=IST),
        output_root=tmp_path,
    )
    frame = _impulse_frame()
    ambiguous = pd.DataFrame(
        {
            "open": [104.2],
            "high": [105.0],
            "low": [100.0],
            "close": [103.0],
            "volume": [300.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2026-10-12 10:15", tz=IST)]),
    )
    second = collect_equity_impulse_continuation(
        "nse",
        _shortlist(),
        {"NSE_FAST": pd.concat([frame, ambiguous])},
        observed_at=datetime(2026, 10, 12, 10, 31, tzinfo=IST),
        output_root=tmp_path,
    )

    assert second["watch_plans"]["states"] == {"invalidated": 1}
    assert second["watch_plans"]["evidence"]["metrics"]["resolved_trades"] == 0


def test_profitability_can_qualify_with_below_fifty_percent_accuracy() -> None:
    latest: dict[str, dict[str, object]] = {}
    outcomes = [2.0, -0.5, -0.5, 2.0, -0.5] * 12
    assert sum(value > 0 for value in outcomes) / len(outcomes) == pytest.approx(0.4)
    for index, net_r in enumerate(outcomes):
        latest[str(index)] = {
            "plan_id": str(index),
            "state": "target" if net_r > 0 else "stop",
            "decision_session": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
            "resolved_at": f"2026-04-{1 + index % 28:02d}T15:15:00+05:30",
            "strict_success": net_r > 0,
            "net_r": net_r,
        }

    evidence = evaluate_equity_impulse_evidence(
        latest, random_timing_margin_r=0.12, integrity_passed=True
    )
    metrics = evidence["metrics"]
    assert metrics["strict_accuracy"] == pytest.approx(0.4)
    assert metrics["mean_net_r"] > 0.05
    assert metrics["expectancy_95_lower_r"] > 0
    assert metrics["profit_factor"] > 1.2
    assert evidence["status"] == "qualified_for_human_review"
    assert "strict_accuracy" not in evidence["checks"]
    assert evidence["eligible_for_live"] is False


def test_crypto_is_explicitly_not_applicable(tmp_path) -> None:
    report = load_equity_impulse_continuation("crypto", output_root=tmp_path)
    assert report["status"] == "not_applicable"
    assert report["eligible_for_live"] is False
