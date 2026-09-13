"""Step 7: the observation session runs scan_bar end to end and evaluates each setup's
eligibility from measured evidence, but must never be ABLE to alert - checked structurally,
not just behaviourally."""

from __future__ import annotations

import ast
import inspect

import pandas as pd
import pytest

from tradedesk.broker.indstocks.models import Interval
from tradedesk.engine.intraday_signals import IntradaySetupKind
from tradedesk.live import intraday_observation as obs

# Every row also needs the columns classify_intraday_regime and mtf._direction read
# (atr_pct, adx14, range_contraction, ema9/20/50). EMAs are stacked BULLISH (99>98>97, below
# the reclaim bar's close of 101) so M5's own direction is BULLISH - otherwise every
# timeframe reads NEUTRAL/UNKNOWN, align() correctly reports "no defined direction" as a
# conflict, and VwapReclaim correctly (this is real production behaviour, not a fixture
# bug) rejects every signal before eligibility is ever reached - discovered by this fixture
# initially using flat EMAs and every signal vanishing, not assumed.
_EXTRA_COLS = dict(atr_pct=1.0, adx14=10.0, range_contraction=1.0, ema9=99.0, ema20=98.0, ema50=97.0)  # noqa: E501


def _reclaim_frame(n_sessions: int = 1) -> pd.DataFrame:
    """One clean VWAP-reclaim setup per session (5-minute bars), each session 3 bars."""
    rows, idx = [], []
    for d in range(n_sessions):
        day = pd.Timestamp("2026-09-07", tz="Asia/Kolkata") + pd.Timedelta(days=d)
        session_rows = [
            dict(open=100.0, close=100.0, high=100.5, low=99.5, vwap=100.2, atr14=1.0, vol_ratio20=1.0),  # noqa: E501
            dict(close=98.0, vwap=99.0, high=98.5, low=97.5, atr14=1.0, vol_ratio20=1.5),
            dict(close=101.0, vwap=99.5, high=101.2, low=99.0, atr14=1.0, vol_ratio20=1.5),
        ]
        for k, r in enumerate(session_rows):
            r.setdefault("open", r["close"])
            r.update(_EXTRA_COLS)
            rows.append(r)
            idx.append(day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5 * k))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _feature_frames(n_sessions: int = 1) -> dict[Interval, dict[str, pd.DataFrame]]:
    f = _reclaim_frame(n_sessions)
    return {iv: {"NSE_TEST": f} for iv in [Interval.M5]}


# ---------------------------------------------------------------- structural guard


def test_module_has_no_alert_capability() -> None:
    """"Cannot alert" must be true of the module's DEPENDENCY GRAPH, not merely its current
    behaviour on one input - so this parses the module's own AST and asserts nothing in it
    ever imports anything alert-shaped. A future edit that adds `from tradedesk.alerts...`
    fails this test immediately, before it could ever run against real signals."""
    source = inspect.getsource(obs)
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("alert" in name.lower() for name in imported), imported


# ---------------------------------------------------------------- eligibility bridge


def test_a_setup_with_no_evidence_is_no_trade_for_every_signal() -> None:
    summary = obs.observe(
        _feature_frames(), ["NSE_TEST"], Interval.M5, [IntradaySetupKind.VWAP_RECLAIM], {}, {},
    )
    assert summary.signals  # the setup did arm
    assert summary.would_have_alerted == 0
    assert all(not s.eligible for s in summary.signals)
    assert all(s.reasons for s in summary.signals)


def test_a_setup_matching_measured_failing_evidence_is_no_trade() -> None:
    """Reproduces the real, measured VWAP Reclaim result (2026-09-13): a real trade count,
    a real win rate, negative expectancy, and a random baseline it does not beat."""
    evidence = {
        IntradaySetupKind.VWAP_RECLAIM: obs.SetupEvidence(
            trades=1419, oos_trades=0, win_rate=0.3756,
            expectancy_r=-0.0051, random_baseline_r=-0.0088,
        )
    }
    summary = obs.observe(
        _feature_frames(), ["NSE_TEST"], Interval.M5,
        [IntradaySetupKind.VWAP_RECLAIM], {}, evidence,
    )
    assert summary.signals
    assert summary.would_have_alerted == 0
    # 1,419 trades clears min_trades; it fails on out-of-sample count and on not beating
    # random by enough (edge is only +0.0037R, need +0.10R) - these are the REAL reasons a
    # human would need to see, not a generic rejection.
    reasons = [r for s in summary.signals for r in s.reasons]
    assert any("out-of-sample" in r for r in reasons)
    assert any("beats random by only" in r for r in reasons)


def test_a_setup_that_clears_every_threshold_is_marked_eligible() -> None:
    """The bridge must work in BOTH directions - this proves it is not hardwired to always
    say no, only that VwapReclaim's real numbers happen to fail it."""
    evidence = {
        IntradaySetupKind.VWAP_RECLAIM: obs.SetupEvidence(
            trades=600, oos_trades=150, win_rate=0.85,
            expectancy_r=0.40, random_baseline_r=0.10,
        )
    }
    summary = obs.observe(
        _feature_frames(), ["NSE_TEST"], Interval.M5,
        [IntradaySetupKind.VWAP_RECLAIM], {}, evidence,
    )
    assert summary.signals
    assert summary.would_have_alerted == len(summary.signals)
    assert all(s.eligible and s.reasons == () for s in summary.signals)


def test_multiple_sessions_are_all_scanned() -> None:
    summary = obs.observe(
        _feature_frames(n_sessions=3), ["NSE_TEST"], Interval.M5,
        [IntradaySetupKind.VWAP_RECLAIM], {}, {},
    )
    assert len(summary.signals) == 3  # one clean reclaim per session
    assert summary.bars_scanned >= 9


def test_summary_text_never_mentions_sending_or_routing_an_alert() -> None:
    summary = obs.observe(
        _feature_frames(), ["NSE_TEST"], Interval.M5, [IntradaySetupKind.VWAP_RECLAIM], {}, {},
    )
    text = summary.text()
    assert "sent" not in text.lower() and "routed" not in text.lower()


@pytest.mark.parametrize("codes", [[], ["NSE_NOTHING_HERE"]])
def test_handles_no_data_without_crashing(codes: list[str]) -> None:
    summary = obs.observe({}, codes, Interval.M5, [IntradaySetupKind.VWAP_RECLAIM], {}, {})
    assert summary.bars_scanned == 0
    assert summary.signals == []
