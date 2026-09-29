import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab import osr_features
from tradedesk_lab.artifacts import digest
from tradedesk_lab.osr_contract import DEFAULT_OSR_CONTRACT
from tradedesk_lab.osr_events import DEFAULT_OSR_EVENT_CONTRACT, OsrOpportunity
from tradedesk_lab.osr_features import MINIMUM_PEERS, compute_features

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel

SESSION = "2026-09-25"
BARS = 30


def _session_bars(
    session_date: str,
    closes: list[float],
    *,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    idx = pd.date_range(
        f"{session_date} 09:15", periods=len(closes), freq="1min", tz="Asia/Kolkata"
    )
    opens = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) + 0.05 for o, c in zip(opens, closes, strict=True)],
            "low": [min(o, c) - 0.05 for o, c in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": volumes or [1_000.0] * len(closes),
        },
        index=idx,
    )


def _trend(start: float, n: int, *, step: float = 0.05) -> list[float]:
    return [start + step * i + 0.01 * (i % 3) for i in range(n)]


def self_frame() -> pd.DataFrame:
    closes = _trend(100.0, BARS)
    volumes = [1_000.0 + 20.0 * i for i in range(BARS)]
    return _session_bars(SESSION, closes, volumes=volumes)


def universe_frames(*, include_self: bool = False) -> dict[str, pd.DataFrame]:
    starts = {
        "NSE_P1": 50.0,
        "NSE_P2": 75.0,
        "NSE_P3": 120.0,
        "NSE_P4": 90.0,
        "NSE_P5": 60.0,
    }
    frames = {
        code: _session_bars(SESSION, _trend(start, BARS, step=0.03))
        for code, start in starts.items()
    }
    if include_self:
        frames["NSE_1"] = self_frame()
    return frames


def daily_frame(n: int = 80, *, end: str = "2026-09-24") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=n, tz="Asia/Kolkata")
    rng = np.random.default_rng(17)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    close[-1] = 100.0
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + rng.uniform(0.2, 0.6, n)
    low = np.minimum(open_, close) - rng.uniform(0.2, 0.6, n)
    low[-1] = 99.0
    volume = rng.uniform(50_000, 150_000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def intraday_history(sessions: int = 5) -> pd.DataFrame:
    base = date.fromisoformat(SESSION)
    frames = []
    for offset in range(sessions, 0, -1):
        day = (base - timedelta(days=offset)).isoformat()
        frames.append(_session_bars(day, _trend(100.0, BARS, step=0.02)))
    return pd.concat(frames)


def opportunity(*, decision_at: str = "2026-09-25T09:35:00+05:30") -> OsrOpportunity:
    return OsrOpportunity(
        identifier="OSR_v1:NSE_1:2026-09-25:gap_down_reclaim:0935",
        scrip_code="NSE_1",
        symbol="TEST",
        session=SESSION,
        mode="gap_down_reclaim",
        signal_bar_open="2026-09-25T09:34:00+05:30",
        decision_at=decision_at,
        state_started_at="2026-09-25T09:20:00+05:30",
        intended_entry=101.0,
        entry_limit=101.1,
        causal_reference=100.5,
        decision_values={"prior_close": 100.0, "prior_low": 99.0},
        strategy_contract_sha256=DEFAULT_OSR_CONTRACT.sha256,
        event_contract_sha256=DEFAULT_OSR_EVENT_CONTRACT.sha256,
    )


def costs() -> EquityCostModel:
    return EquityCostModel(ChargeSchedule())


def _full_kwargs(**overrides) -> dict:
    kwargs = dict(
        opportunity=opportunity(),
        frame=self_frame(),
        daily_frame=daily_frame(),
        universe_frames=universe_frames(),
        intraday_history=intraday_history(),
        costs=costs(),
        quantity=10,
    )
    kwargs.update(overrides)
    return kwargs


def test_computed_features_match_registry_and_are_finite():
    result = compute_features(**_full_kwargs())
    names = {feature.name for feature in DEFAULT_OSR_CONTRACT.features}
    assert set(result["values"]) == names
    assert len(result["values"]) == 30
    assert all(np.isfinite(value) for value in result["values"].values())
    assert result["peer_count"] == 5
    assert result["available_at"] == "2026-09-25T09:35:00+05:30"
    assert 0 <= result["values"]["cross_sectional_return_rank"] <= 1
    assert 0 <= result["values"]["cross_sectional_recovery_rank"] <= 1


def test_future_self_and_peer_bars_cannot_change_features():
    original = compute_features(**_full_kwargs())
    at = pd.Timestamp("2026-09-25 09:35", tz="Asia/Kolkata")

    corrupted_self = self_frame()
    corrupted_self.loc[
        corrupted_self.index >= at, ["open", "high", "low", "close", "volume"]
    ] = [1.0, 9_999.0, 0.5, 8_000.0, 999_999_999.0]
    assert compute_features(**_full_kwargs(frame=corrupted_self)) == original

    corrupted_peers = universe_frames()
    for frame in corrupted_peers.values():
        frame.loc[frame.index >= at, ["open", "high", "low", "close", "volume"]] = [
            1.0,
            9_999.0,
            0.5,
            8_000.0,
            999_999_999.0,
        ]
    assert compute_features(**_full_kwargs(universe_frames=corrupted_peers)) == original


def test_candidate_is_excluded_from_cross_sectional_peers():
    without_self = compute_features(**_full_kwargs(universe_frames=universe_frames()))
    with_self = compute_features(
        **_full_kwargs(universe_frames=universe_frames(include_self=True))
    )
    assert with_self == without_self


def test_undersized_peer_universe_fails_closed():
    thin = dict(list(universe_frames().items())[: MINIMUM_PEERS - 1])
    with pytest.raises(ValueError, match="insufficient_peer_universe"):
        compute_features(**_full_kwargs(universe_frames=thin))


def test_noncausal_daily_or_intraday_history_fails_closed():
    with pytest.raises(ValueError, match="daily_frame_not_causal"):
        compute_features(**_full_kwargs(daily_frame=daily_frame(end="2026-09-25")))

    overlapping = pd.concat([intraday_history(), self_frame().iloc[:5]])
    with pytest.raises(ValueError, match="intraday_history_overlaps_current_session"):
        compute_features(**_full_kwargs(intraday_history=overlapping))


def test_opportunity_daily_context_mismatch_fails_closed():
    bad = replace(
        opportunity(),
        decision_values={"prior_close": 101.0, "prior_low": 99.0},
    )
    with pytest.raises(ValueError, match="opportunity_prior_close_mismatch"):
        compute_features(**_full_kwargs(opportunity=bad))


def test_contract_fingerprint_and_mode_mismatch_are_rejected():
    mismatched = replace(opportunity(), strategy_contract_sha256="0" * 64)
    with pytest.raises(ValueError, match="strategy contract differs"):
        compute_features(**_full_kwargs(opportunity=mismatched))

    wrong_mode = replace(opportunity(), mode="breakout_retest")
    with pytest.raises(ValueError, match="mode is not"):
        compute_features(**_full_kwargs(opportunity=wrong_mode))


def test_tracked_osr_feature_evidence_matches_sources():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/osr-features.json").read_text(encoding="utf-8"))

    assert evidence["status"] == "feature_and_dataset_assembly_implemented_real_run_pending"
    assert evidence["milestone"] == 2
    assert evidence["feature_registry_implemented"] is True
    assert evidence["real_run_completed"] is False
    assert evidence["algorithm_evaluated"] is False
    assert evidence["baseline_improved"] is False
    assert evidence["eligible_for_live"] is False
    assert evidence["strategy_contract_sha256"] == DEFAULT_OSR_CONTRACT.sha256
    assert evidence["feature_implementation_sha256"] == digest(Path(osr_features.__file__))
    assert evidence["feature_tests_sha256"] == digest(root / "tests_lab/test_osr_features.py")
    assert evidence["decision"]["change_live_behavior"] is False
