"""Multi-timeframe alignment (SDD section 5): last-closed-bar look-ahead discipline,
direction classification, and conflict/agreement across the hierarchy."""

from __future__ import annotations

import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine import mtf


def _bars(opens: list[str], stacked: str) -> pd.DataFrame:
    """`stacked` in {"up", "down", "flat"} controls whether close/ema9/ema20/ema50 stack
    bullishly, bearishly, or not at all - every bar identical, since only the LAST closed
    one should ever matter to `align()`."""
    idx = pd.DatetimeIndex([pd.Timestamp(o, tz="Asia/Kolkata") for o in opens])
    if stacked == "up":
        row = {"close": 104.0, "ema9": 103.0, "ema20": 102.0, "ema50": 101.0}
    elif stacked == "down":
        row = {"close": 101.0, "ema9": 102.0, "ema20": 103.0, "ema50": 104.0}
    else:
        row = {"close": 100.0, "ema9": 100.0, "ema20": 100.0, "ema50": 100.0}
    return pd.DataFrame([row] * len(idx), index=idx)


def test_last_closed_bar_excludes_an_in_progress_bar() -> None:
    """The core look-ahead guard: a bar that OPENS exactly at `at` (or later) has not
    closed and must never be returned, even though its ts <= at would pass a naive check."""
    df = _bars(["2026-09-14 09:15", "2026-09-14 09:30"], "up")
    at = pd.Timestamp("2026-09-14 09:31", tz="Asia/Kolkata")
    # the 09:30 bar (15-minute) closes at 09:45, which is AFTER `at` - not yet available
    row = mtf.last_closed_bar(df, Interval.M15, at)
    assert row is not None
    assert row.name == pd.Timestamp("2026-09-14 09:15", tz="Asia/Kolkata")


def test_last_closed_bar_becomes_available_the_instant_it_closes() -> None:
    df = _bars(["2026-09-14 09:15", "2026-09-14 09:30"], "up")
    closes_at = pd.Timestamp("2026-09-14 09:45", tz="Asia/Kolkata")  # 09:30 + 15min
    just_before = closes_at - pd.Timedelta(seconds=1)
    assert mtf.last_closed_bar(df, Interval.M15, just_before).name == pd.Timestamp(
        "2026-09-14 09:15", tz="Asia/Kolkata"
    )
    assert mtf.last_closed_bar(df, Interval.M15, closes_at).name == pd.Timestamp(
        "2026-09-14 09:30", tz="Asia/Kolkata"
    )


def test_last_closed_bar_returns_none_before_any_bar_has_closed() -> None:
    df = _bars(["2026-09-14 09:15"], "up")
    too_early = pd.Timestamp("2026-09-14 09:20", tz="Asia/Kolkata")  # closes at 09:30
    assert mtf.last_closed_bar(df, Interval.M15, too_early) is None


def test_direction_requires_a_clean_ema_stack() -> None:
    up = _bars(["2026-09-14 09:15"], "up").iloc[0]
    down = _bars(["2026-09-14 09:15"], "down").iloc[0]
    flat = _bars(["2026-09-14 09:15"], "flat").iloc[0]
    assert mtf._direction(up) is mtf.Direction.BULLISH
    assert mtf._direction(down) is mtf.Direction.BEARISH
    assert mtf._direction(flat) is mtf.Direction.NEUTRAL


def test_missing_timeframe_is_unknown_not_neutral() -> None:
    """A caller must be able to tell "never had this timeframe" apart from "checked it and
    it disagrees" - collapsing both into NEUTRAL would hide the difference."""
    at = pd.Timestamp("2026-09-14 11:00", tz="Asia/Kolkata")  # all HIERARCHY bars closed by now
    frames = {Interval.M15: _bars(["2026-09-14 09:15"], "up")}
    a = mtf.align(frames, at)
    assert a.directions[Interval.H1] is mtf.Direction.UNKNOWN
    assert a.directions[Interval.M1] is mtf.Direction.UNKNOWN
    assert a.directions[Interval.M15] is mtf.Direction.BULLISH


def test_all_timeframes_agreeing_has_no_conflict_and_full_agreement() -> None:
    at = pd.Timestamp("2026-09-14 11:00", tz="Asia/Kolkata")  # all HIERARCHY bars closed by now
    frames = {iv: _bars(["2026-09-14 09:15"], "up") for iv in mtf.HIERARCHY}
    a = mtf.align(frames, at)
    assert not a.conflict
    assert a.agreement == pytest.approx(1.0)
    assert a.reasons == ()


def test_conflicting_timeframes_are_flagged_with_reasons() -> None:
    at = pd.Timestamp("2026-09-14 11:00", tz="Asia/Kolkata")  # all HIERARCHY bars closed by now
    frames = {iv: _bars(["2026-09-14 09:15"], "up") for iv in mtf.HIERARCHY}
    frames[Interval.M1] = _bars(["2026-09-14 09:15"], "down")  # the odd one out
    a = mtf.align(frames, at)
    assert a.conflict
    assert a.agreement == pytest.approx(4 / 5)
    assert a.reasons and "1minute" in a.reasons[0]


def test_neutral_timeframes_do_not_count_toward_agreement_or_conflict() -> None:
    """A flat/neutral timeframe is neither evidence for nor against - it should not dilute
    agreement or manufacture a conflict on its own."""
    at = pd.Timestamp("2026-09-14 11:00", tz="Asia/Kolkata")  # all HIERARCHY bars closed by now
    frames = {iv: _bars(["2026-09-14 09:15"], "up") for iv in mtf.HIERARCHY}
    frames[Interval.M3] = _bars(["2026-09-14 09:15"], "flat")
    a = mtf.align(frames, at)
    assert not a.conflict
    assert a.agreement == pytest.approx(1.0)
    assert a.directions[Interval.M3] is mtf.Direction.NEUTRAL


def test_no_defined_direction_anywhere_is_a_conflict_not_silently_fine() -> None:
    at = pd.Timestamp("2026-09-14 11:00", tz="Asia/Kolkata")  # all HIERARCHY bars closed by now
    frames = {iv: _bars(["2026-09-14 09:15"], "flat") for iv in mtf.HIERARCHY}
    a = mtf.align(frames, at)
    assert a.conflict
    assert a.agreement == 0.0
    assert a.reasons
