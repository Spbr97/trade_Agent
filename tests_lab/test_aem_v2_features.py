from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from tradedesk_lab.aem_v2_contract import DEFAULT_AEM_V2_CONTRACT
from tradedesk_lab.aem_v2_events import DEFAULT_EVENT_ENGINE_CONTRACT, AemV2Opportunity
from tradedesk_lab.aem_v2_features import MINIMUM_PEERS, compute_features

from tradedesk.config.models import ChargeSchedule
from tradedesk.markets.costs import EquityCostModel

SESSION = "2026-09-25"
BARS = 30


def _session_bars(
    session_date: str, closes: list[float], *, volumes: list[float] | None = None
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


def universe_frames() -> dict[str, pd.DataFrame]:
    starts = {"NSE_P1": 50.0, "NSE_P2": 75.0, "NSE_P3": 120.0, "NSE_P4": 90.0, "NSE_P5": 60.0}
    return {
        code: _session_bars(SESSION, _trend(start, BARS, step=0.03))
        for code, start in starts.items()
    }


def daily_frame(n: int = 80, *, end: str = "2026-09-24") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=n, tz="Asia/Kolkata")
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + rng.uniform(0.2, 0.6, n)
    low = np.minimum(open_, close) - rng.uniform(0.2, 0.6, n)
    volume = rng.uniform(50_000, 150_000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


def intraday_history(sessions: int = 5) -> pd.DataFrame:
    base = date.fromisoformat(SESSION)
    frames = []
    for offset in range(sessions, 0, -1):
        day = (base - timedelta(days=offset)).isoformat()
        frames.append(_session_bars(day, _trend(100.0, BARS, step=0.02)))
    return pd.concat(frames)


def opportunity(*, decision_at: str = "2026-09-25T09:30:00+05:30") -> AemV2Opportunity:
    return AemV2Opportunity(
        identifier="AEM_v2:NSE_1:2026-09-25:anticipatory_impulse:0930",
        scrip_code="NSE_1",
        symbol="TEST",
        session=SESSION,
        mode="anticipatory_impulse",
        signal_bar_open="2026-09-25T09:29:00+05:30",
        decision_at=decision_at,
        state_started_at="2026-09-25T09:20:00+05:30",
        intended_entry=101.5,
        entry_limit=101.65,
        causal_level=102.0,
        decision_values={},
        strategy_contract_sha256=DEFAULT_AEM_V2_CONTRACT.sha256,
        event_contract_sha256=DEFAULT_EVENT_ENGINE_CONTRACT.sha256,
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


def test_computed_features_match_frozen_registry_and_are_all_finite():
    result = compute_features(**_full_kwargs())
    names = {feature.name for feature in DEFAULT_AEM_V2_CONTRACT.features}
    assert set(result["values"]) == names
    assert all(np.isfinite(v) for v in result["values"].values())
    assert result["peer_count"] == 5
    assert result["available_at"] == "2026-09-25T09:30:00+05:30"


def test_future_bars_and_future_peer_bars_cannot_change_computed_features():
    original = compute_features(**_full_kwargs())

    corrupted_self = self_frame()
    at = pd.Timestamp("2026-09-25 09:30", tz="Asia/Kolkata")
    corrupted_self.loc[corrupted_self.index >= at, ["open", "high", "low", "close", "volume"]] = [
        1.0,
        9_999.0,
        0.5,
        8_000.0,
        999_999_999.0,
    ]
    changed = compute_features(**_full_kwargs(frame=corrupted_self))
    assert changed == original

    corrupted_peers = universe_frames()
    for pframe in corrupted_peers.values():
        pframe.loc[pframe.index >= at, ["open", "high", "low", "close", "volume"]] = [
            1.0,
            9_999.0,
            0.5,
            8_000.0,
            999_999_999.0,
        ]
    changed_peers = compute_features(**_full_kwargs(universe_frames=corrupted_peers))
    assert changed_peers == original


def test_insufficient_signal_history_fails_closed():
    short_frame = self_frame().iloc[:8]
    with pytest.raises(ValueError, match="insufficient_signal_history"):
        compute_features(**_full_kwargs(frame=short_frame, opportunity=opportunity(
            decision_at="2026-09-25T09:23:00+05:30"
        )))


def test_undersized_peer_universe_fails_closed():
    thin_universe = dict(list(universe_frames().items())[: MINIMUM_PEERS - 1])
    with pytest.raises(ValueError, match="insufficient_peer_universe"):
        compute_features(**_full_kwargs(universe_frames=thin_universe))


def test_noncausal_daily_frame_fails_closed():
    same_day_daily = daily_frame(end="2026-09-25")
    with pytest.raises(ValueError, match="daily_frame_not_causal"):
        compute_features(**_full_kwargs(daily_frame=same_day_daily))


def test_overlapping_tod_history_fails_closed():
    overlapping = pd.concat([intraday_history(), self_frame().iloc[:5]])
    with pytest.raises(ValueError, match="intraday_history_overlaps_current_session"):
        compute_features(**_full_kwargs(intraday_history=overlapping))


def test_contract_fingerprint_mismatch_is_rejected():
    from dataclasses import replace

    mismatched = replace(opportunity(), strategy_contract_sha256="0" * 64)
    with pytest.raises(ValueError, match="strategy contract differs"):
        compute_features(**_full_kwargs(opportunity=mismatched))
