"""scan_bar (CLAUDE.md: signals are created only by scan_day (daily) and scan_bar
(intraday)): the look-ahead safety net, universe filtering, and multi-code wiring."""

from __future__ import annotations

import pandas as pd

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine.intraday_engine import IntradaySnapshot, scan_bar
from tradedesk.engine.intraday_signals import IntradaySetupKind


def _reclaim_frame(n_extra_future_bars: int = 0) -> pd.DataFrame:
    """A clean VWAP-reclaim setup on bar 1 (5-minute), optionally followed by bars that
    would change the outcome if scan_bar ever let a setup see them."""
    idx = [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=5 * i) for i in range(2 + n_extra_future_bars)]  # noqa: E501
    rows = [
        dict(close=98.0, vwap=99.0, high=98.5, low=97.5, atr14=1.0, vol_ratio20=1.5),
        dict(close=101.0, vwap=99.5, high=101.2, low=99.0, atr14=1.0, vol_ratio20=1.5),
    ]
    # Future bars, if any, are wildly different - if scan_bar ever leaked them into the
    # arming bar's df, the setup's geometry (which reads df.iloc[-1]) would change.
    rows += [dict(close=5.0, vwap=500.0, high=5.0, low=5.0, atr14=0.001, vol_ratio20=0.001)] * n_extra_future_bars  # noqa: E501
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _snapshot(frame: pd.DataFrame, at: pd.Timestamp) -> IntradaySnapshot:
    return IntradaySnapshot(
        at=at,
        arming_interval=Interval.M5,
        features={"NSE_2885": frame},
        symbols={"NSE_2885": "RELIANCE"},
    )


def test_scan_bar_arms_a_clean_setup() -> None:
    at = pd.Timestamp("2026-09-14 09:25", tz="Asia/Kolkata")  # bar[1] (09:20) closes at 09:25
    snap = _snapshot(_reclaim_frame(), at)
    sigs = scan_bar(snap, [IntradaySetupKind.VWAP_RECLAIM], {})
    assert len(sigs) == 1
    assert sigs[0].scrip_code == "NSE_2885"
    assert sigs[0].trigger == 101.2


def test_scan_bar_never_lets_a_setup_see_an_unclosed_bar() -> None:
    """The core safety net: even though the frame PHYSICALLY CONTAINS future rows that
    would completely change the geometry, scan_bar must slice to the last bar closed by
    `at` before any setup ever sees the frame."""
    at = pd.Timestamp("2026-09-14 09:25", tz="Asia/Kolkata")
    with_future = _reclaim_frame(n_extra_future_bars=3)
    snap = _snapshot(with_future, at)
    sigs = scan_bar(snap, [IntradaySetupKind.VWAP_RECLAIM], {})
    assert len(sigs) == 1
    assert sigs[0].trigger == 101.2  # identical to the no-future-bars case, not corrupted
    assert sigs[0].armed_at == pd.Timestamp("2026-09-14 09:20", tz="Asia/Kolkata")


def test_scan_bar_returns_nothing_before_the_arming_bar_has_closed() -> None:
    at = pd.Timestamp("2026-09-14 09:16", tz="Asia/Kolkata")  # bar[1] (09:20) not even open yet
    snap = _snapshot(_reclaim_frame(), at)
    assert scan_bar(snap, [IntradaySetupKind.VWAP_RECLAIM], {}) == []


def test_scan_bar_skips_a_code_with_no_frame() -> None:
    at = pd.Timestamp("2026-09-14 09:25", tz="Asia/Kolkata")
    snap = IntradaySnapshot(
        at=at, arming_interval=Interval.M5,
        features={"NSE_2885": _reclaim_frame(), "NSE_9999": pd.DataFrame()},
        symbols={"NSE_2885": "RELIANCE", "NSE_9999": "GHOST"},
    )
    sigs = scan_bar(snap, [IntradaySetupKind.VWAP_RECLAIM], {})
    assert {s.scrip_code for s in sigs} == {"NSE_2885"}


def test_scan_bar_universe_restricts_which_codes_are_scanned() -> None:
    at = pd.Timestamp("2026-09-14 09:25", tz="Asia/Kolkata")
    snap = IntradaySnapshot(
        at=at, arming_interval=Interval.M5,
        features={"NSE_2885": _reclaim_frame(), "NSE_1333": _reclaim_frame()},
        symbols={"NSE_2885": "RELIANCE", "NSE_1333": "HDFCBANK"},
        universe=["NSE_1333"],
    )
    sigs = scan_bar(snap, [IntradaySetupKind.VWAP_RECLAIM], {})
    assert {s.scrip_code for s in sigs} == {"NSE_1333"}


def test_scan_bar_passes_per_setup_params_through() -> None:
    at = pd.Timestamp("2026-09-14 09:25", tz="Asia/Kolkata")
    weak_volume = _reclaim_frame()
    weak_volume.iloc[-1, weak_volume.columns.get_loc("vol_ratio20")] = 1.1
    snap = _snapshot(weak_volume, at)
    assert scan_bar(snap, [IntradaySetupKind.VWAP_RECLAIM], {}) == []
    sigs = scan_bar(
        snap, [IntradaySetupKind.VWAP_RECLAIM], {"vwap_reclaim": {"min_vol_ratio": 1.0}}
    )
    assert len(sigs) == 1
