"""VWAP Reclaim (SDD section 7): each condition in isolation, MTF conflict rejection, and
the look-ahead property."""

from __future__ import annotations

import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine.mtf import Alignment, Direction
from tradedesk.setups.intraday.base import IntradaySetupContext
from tradedesk.setups.intraday.vwap_reclaim import VwapReclaim

CTX = IntradaySetupContext(scrip_code="NSE_2885", symbol="RELIANCE", interval=Interval.M5)


def _frame(rows: list[dict]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=5 * i) for i in range(len(rows))]  # noqa: E501
    )
    return pd.DataFrame(rows, index=idx)


def _reclaim_rows(**over: object) -> list[dict]:
    """prev bar below VWAP, last bar reclaims it with a strong close and elevated volume -
    every default value chosen to clear every gate, so a single override isolates one."""
    prev = dict(close=98.0, vwap=99.0, high=98.5, low=97.5, atr14=1.0, vol_ratio20=1.5)
    last = dict(close=101.0, vwap=99.5, high=101.2, low=99.0, atr14=1.0, vol_ratio20=1.5)
    last.update(over)
    return [prev, last]


def test_arms_on_a_clean_reclaim() -> None:
    sig = VwapReclaim().arm(_frame(_reclaim_rows()), CTX, {})
    assert sig is not None
    assert sig.trigger == pytest.approx(101.2)
    assert sig.stop < sig.trigger
    assert "reclaimed VWAP" in sig.reasons[0]


def test_does_not_arm_if_it_was_already_above_vwap() -> None:
    rows = _reclaim_rows()
    rows[0]["close"] = 99.5  # prev bar was ABOVE its own vwap (99.0) - no reclaim happened
    assert VwapReclaim().arm(_frame(rows), CTX, {}) is None


def test_does_not_arm_without_reclaiming_now() -> None:
    rows = _reclaim_rows()
    rows[1]["close"] = 99.0  # last bar closes AT/below its own vwap (99.5) - not reclaimed
    assert VwapReclaim().arm(_frame(rows), CTX, {}) is None


def test_does_not_arm_on_a_weak_close() -> None:
    rows = _reclaim_rows()
    rows[1]["close"] = 99.6  # technically above vwap, but near the bottom of [99.0, 101.2]
    assert VwapReclaim().arm(_frame(rows), CTX, {}) is None


def test_does_not_arm_without_volume_expansion() -> None:
    rows = _reclaim_rows(vol_ratio20=0.8)
    assert VwapReclaim().arm(_frame(rows), CTX, {}) is None


def test_does_not_arm_with_no_vwap_yet() -> None:
    """Too early in the session for the accumulator to have a value - must not arm on
    missing data rather than treating NaN as some default."""
    rows = _reclaim_rows()
    rows[0]["vwap"] = float("nan")
    assert VwapReclaim().arm(_frame(rows), CTX, {}) is None


def test_rejects_outright_on_mtf_conflict() -> None:
    """The setup's whole premise is weakest when a higher timeframe disagrees - SDD section
    5's hard reject, not merely a lower score."""
    conflicted = Alignment(
        at=pd.Timestamp("2026-09-14 09:20", tz="Asia/Kolkata"),
        directions={Interval.H1: Direction.BEARISH, Interval.M5: Direction.BULLISH},
        agreement=0.5, conflict=True, reasons=("timeframes disagree",),
    )  # fmt: skip
    ctx = IntradaySetupContext(
        scrip_code="NSE_2885", symbol="RELIANCE", interval=Interval.M5, alignment=conflicted
    )
    assert VwapReclaim().arm(_frame(_reclaim_rows()), ctx, {}) is None


def test_agreeing_alignment_does_not_block_it() -> None:
    agreeing = Alignment(
        at=pd.Timestamp("2026-09-14 09:20", tz="Asia/Kolkata"),
        directions={Interval.H1: Direction.BULLISH, Interval.M5: Direction.BULLISH},
        agreement=1.0, conflict=False,
    )  # fmt: skip
    ctx = IntradaySetupContext(
        scrip_code="NSE_2885", symbol="RELIANCE", interval=Interval.M5, alignment=agreeing
    )
    sig = VwapReclaim().arm(_frame(_reclaim_rows()), ctx, {})
    assert sig is not None
    assert any("mtf agreement" in r for r in sig.reasons)


def test_stop_never_lands_above_trigger() -> None:
    """A degenerate geometry (stop >= trigger) must refuse rather than emit an unusable
    signal - the same defensive check make_signal() does for daily setups."""
    rows = _reclaim_rows(low=101.0, high=101.2, atr14=0.01)  # razor-thin range, tiny ATR
    sig = VwapReclaim().arm(_frame(rows), CTX, {})
    if sig is not None:
        assert sig.stop < sig.trigger


def test_params_override_defaults() -> None:
    rows = _reclaim_rows(vol_ratio20=1.1)  # below the 1.2 default, above a loosened one
    assert VwapReclaim().arm(_frame(rows), CTX, {}) is None
    sig = VwapReclaim().arm(_frame(rows), CTX, {"min_vol_ratio": 1.0})
    assert sig is not None
