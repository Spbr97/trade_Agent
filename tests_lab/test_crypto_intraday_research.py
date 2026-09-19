"""Crypto adaptation of test_intraday_research.py's coverage shape: causal geometry,
executable fills bounded by MAX_HOLD_BARS (not a same-session cap), and the capped
portfolio/null-baseline replay - against synthetic H1 crypto-shaped fixtures."""

from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest
from tradedesk_lab.crypto_intraday_research import (
    BAR,
    MAX_HOLD_BARS,
    CryptoResearchSignal,
    CryptoSetupKind,
    ResolvedCandidate,
    _size_and_cost,
    _stop_loss,
    fill_candidate,
    matched_random,
    portfolio_replay,
    research_registry,
    run_research,
)

from tradedesk.broker.indstocks.models import Candle, Interval
from tradedesk.config.models import RiskConfig
from tradedesk.data.candle_store import CandleStore
from tradedesk.engine.intraday_engine import IntradaySnapshot, scan_bar
from tradedesk.markets.market import crypto_market
from tradedesk.setups.intraday import INTRADAY_REGISTRY


def _frame(kind: CryptoSetupKind) -> pd.DataFrame:
    n = 45
    # Baseline bars are flat/down-closing so MomentumContinuation's "N consecutive
    # up-closes" cannot be satisfied by accident - only the crafted tail bars are up.
    rows = [
        dict(
            open=100.6, high=101.0, low=100.0, close=100.4, volume=1000,
            atr14=1.0, vol_ratio20=0.9, rsi14=50.0,
        )
        for _ in range(n)
    ]  # fmt: skip
    if kind == CryptoSetupKind.BREAKOUT_RETEST:
        rows[-2].update(open=100.8, high=102.2, low=100.8, close=102.0, vol_ratio20=1.5)
        rows[-1].update(open=101.3, high=101.9, low=100.95, close=101.8)
    else:
        for k in (-3, -2, -1):
            rows[k].update(
                open=100.0 + (5 + k) * 0.5,
                high=100.6 + (5 + k) * 0.5,
                low=99.9 + (5 + k) * 0.5,
                close=100.5 + (5 + k) * 0.5,
                vol_ratio20=1.6,
            )
    return pd.DataFrame(
        rows,
        index=pd.date_range("2026-09-16 00:00", periods=n, freq="1h", tz="Asia/Kolkata"),
    )


def _scan(frame: pd.DataFrame, kind: CryptoSetupKind, at=None):
    snapshot = IntradaySnapshot(
        at=at if at is not None else frame.index[-1] + BAR,
        arming_interval=Interval.H1,
        features={"CDX_TESTINR": frame},
        symbols={"CDX_TESTINR": "TEST"},
    )
    return scan_bar(snapshot, [kind], {}, max_hold_bars=MAX_HOLD_BARS)


@pytest.mark.parametrize("kind", list(CryptoSetupKind))
def test_future_and_forming_bars_cannot_change_signal(kind):
    frame = _frame(kind)
    future = frame.iloc[-2:].copy()
    future.index = pd.date_range(frame.index[-1] + BAR, periods=2, freq="1h")
    future.loc[:, ["open", "high", "low", "close", "atr14", "vol_ratio20"]] = 0.001
    with research_registry():
        normal = _scan(frame, kind)
        poisoned = _scan(pd.concat([frame, future]), kind, frame.index[-1] + BAR)
        assert len(normal) == 1
        assert normal[0].model_dump() == poisoned[0].model_dump()
        assert normal[0].armed_at == frame.index[-1] + BAR
        assert _scan(frame, kind, frame.index[-1] + BAR - pd.Timedelta(seconds=1)) == []


def test_breakout_requires_a_distinct_later_retest():
    frame = _frame(CryptoSetupKind.BREAKOUT_RETEST)
    with research_registry():
        assert _scan(frame.iloc[:-1], CryptoSetupKind.BREAKOUT_RETEST) == []
        assert len(_scan(frame, CryptoSetupKind.BREAKOUT_RETEST)) == 1
        frame.loc[frame.index[-1], "low"] = 101.5
        assert _scan(frame, CryptoSetupKind.BREAKOUT_RETEST) == []


def test_momentum_rejects_when_already_overextended_or_exhausted():
    frame = _frame(CryptoSetupKind.MOMENTUM_CONTINUATION)
    with research_registry():
        assert len(_scan(frame, CryptoSetupKind.MOMENTUM_CONTINUATION)) == 1
        overbought = frame.copy()
        overbought.loc[overbought.index[-1], "rsi14"] = 85.0
        assert _scan(overbought, CryptoSetupKind.MOMENTUM_CONTINUATION) == []
        quiet = frame.copy()
        quiet.loc[quiet.index[-1], "vol_ratio20"] = 0.8
        assert _scan(quiet, CryptoSetupKind.MOMENTUM_CONTINUATION) == []


def test_registry_restored_after_error_and_nested_use_rejected():
    before = INTRADAY_REGISTRY.copy()
    with pytest.raises(ValueError, match="test"):
        with research_registry():
            with pytest.raises(RuntimeError, match="already active"):
                with research_registry():
                    pass
            raise ValueError("test")
    assert INTRADAY_REGISTRY == before


def _fill_fixture():
    frame = _frame(CryptoSetupKind.BREAKOUT_RETEST)
    with research_registry():
        sig = _scan(frame, CryptoSetupKind.BREAKOUT_RETEST)[0]
    later = pd.DataFrame(
        [
            dict(open=101.9, high=1000.0, low=101.5, close=102.2),
            dict(open=102.2, high=102.4, low=101.4, close=102.1),
            dict(open=102.1, high=102.3, low=101.5, close=102.2),
        ],
        index=pd.date_range(frame.index[-1] + BAR, periods=3, freq="1h"),
    )
    return pd.concat([frame, later]), sig


def test_fill_after_confirmation_never_counts_preentry_target():
    frame, sig = _fill_fixture()
    arm = 44
    trade, why = fill_candidate(sig, frame, arm, 0.0005)
    assert why == "filled" and trade is not None
    assert trade.entry_bar == arm + 2
    assert pd.Timestamp(trade.entry_at) > pd.Timestamp(trade.decision_at)
    assert trade.entry == pytest.approx(102.2 * 1.0005)
    assert not trade.target_hit
    assert trade.exit_reason == "session_close"  # reused label: end of the observed window


def test_no_execution_if_target_is_below_actual_fill_or_no_next_bar():
    frame, sig = _fill_fixture()
    arm = 44
    entry_idx = arm + 2
    frame.loc[frame.index[entry_idx], "open"] = sig.t1 + 1
    trade, why = fill_candidate(sig, frame, arm, 0)
    assert trade is None and why == "invalid_target_or_stop_at_fill"
    trade, why = fill_candidate(sig, frame.iloc[:entry_idx], arm, 0)
    assert trade is None and why == "no_next_bar"


def test_missing_entry_bar_is_not_filled_at_a_later_open():
    frame, sig = _fill_fixture()
    arm = 44
    entry_idx = arm + 2
    frame = frame.drop(frame.index[entry_idx])
    trade, why = fill_candidate(sig, frame, arm, 0)
    assert trade is None and why == "missing_entry_bar"


def test_max_hold_bounds_the_exit_instead_of_a_same_day_session():
    """A move can span a calendar-day boundary: the exit search must reach past midnight,
    unlike the NSE harness's session-bounded `resolve_barriers` call."""
    frame, sig = _fill_fixture()
    # No bar ever hits the stop or target; resolution should stop at entry + MAX_HOLD_BARS,
    # not at the end of the entry bar's own calendar day.
    tail = pd.DataFrame(
        {"open": 102.15, "high": 102.3, "low": 102.0, "close": 102.15},
        index=pd.date_range(frame.index[-1] + BAR, periods=MAX_HOLD_BARS + 10, freq="1h"),
    )
    frame = pd.concat([frame, tail])
    trade, why = fill_candidate(sig, frame, 44, 0)
    assert why == "filled" and trade is not None
    assert trade.window_end - trade.entry_bar == MAX_HOLD_BARS
    assert pd.Timestamp(trade.exit_at).date() > pd.Timestamp(trade.entry_at).date()


def _candidate(code="CDX_TESTINR", *, entry_at="2026-09-16T10:00:00+05:30", exit_price=113.0):
    # Crypto's heavier costs (1% TDS + fees) need real gross-R margin to clear the
    # configured net-RR gate - a 5R nominal target (as the NSE fixture used) nets under
    # 2.0R here; 11R clears it comfortably. See CLAUDE.md's crypto cost findings.
    sig = CryptoResearchSignal(
        id=f"test:{code}",
        scrip_code=code,
        symbol=code,
        setup=CryptoSetupKind.BREAKOUT_RETEST,
        interval=Interval.H1,
        armed_at=pd.Timestamp("2026-09-16 09:00", tz="Asia/Kolkata"),
        trigger=102,
        stop=100,
        t1=112,
        t2=112,
        atr=1,
    )
    return ResolvedCandidate(
        sig,
        "2026-09-16",
        sig.armed_at.isoformat(),
        entry_at,
        "2026-09-17T10:00:00+05:30",
        1,
        0,
        MAX_HOLD_BARS,
        101,
        exit_price,
        100,
        112,
        (exit_price - 101),
        exit_price >= 112,
        "target" if exit_price >= 112 else "stop",
    )


def _crypto_risk_and_costs():
    from tradedesk.config import load_config

    settings = load_config(".")
    market = crypto_market(settings)
    risk = RiskConfig(trading_capital=Decimal("100000"))
    return risk, market.costs, market.qty_step, market.min_notional_inr


def test_sizing_includes_fees_stop_slippage_and_respects_min_notional():
    risk, costs, qty_step, min_notional = _crypto_risk_and_costs()
    trade = _candidate()
    qty, fees, _ = _size_and_cost(trade, 100000, risk, costs, qty_step, min_notional)
    assert qty > 0
    assert qty * trade.entry >= min_notional
    assert fees > 0
    assert _stop_loss(trade, qty, costs) <= 100000 * float(risk.max_risk_per_trade_pct) + 1e-6


def test_portfolio_daily_caps_and_future_outcome_not_available_at_entry():
    risk, costs, qty_step, min_notional = _crypto_risk_and_costs()
    trades = [_candidate(f"CDX_{i}INR") for i in range(5)]
    result = portfolio_replay(trades, risk, costs, qty_step, min_notional, ["2026-09-16"])
    assert result["trades"] == min(result["trades"], 5)
    assert result["trades"] > 0
    damaged = [replace(trades[0], exit=50, gross_r=-51, target_hit=False), *trades[1:]]
    alternate = portfolio_replay(damaged, risk, costs, qty_step, min_notional, ["2026-09-16"])
    assert alternate["ending_capital"] <= result["ending_capital"]
    # No sector mapping exists for crypto - the sector cap never fires (adaptation 4).
    assert not any(r.startswith("sector cap") for r in result["rejections"])


def test_null_uses_same_next_open_barriers_and_max_hold_not_session_end():
    n = MAX_HOLD_BARS + 5
    frame = pd.DataFrame(
        {"open": [101] * n, "high": [110] * n, "low": [100.5] * n, "close": [109] * n},
        index=pd.date_range("2026-09-16 00:00", periods=n, freq="1h", tz="Asia/Kolkata"),
    )
    slip = 0.0005
    entry = 101 * (1 + slip)
    stop, target = entry - 1, entry + 2
    gross = target * (1 - slip) - entry
    trade = replace(
        _candidate(),
        entry=entry,
        stop=stop,
        target=target,
        gross_r=gross,
        window_end=n - 1,
        exit=target * (1 - slip),
    )
    result = matched_random([trade], {"CDX_TESTINR": frame}, n_cohorts=20, slippage=slip)
    assert result["gross_r_lift"] == pytest.approx(0, abs=1e-10)
    assert result["null_mean_gross_r"] == pytest.approx(gross)


def test_offline_report_uses_own_namespace_and_crypto_cost_model(tmp_path, monkeypatch):
    from tradedesk_lab import crypto_intraday_research as research

    from tradedesk.config import load_config as real_load_config

    (tmp_path / "data").mkdir()
    # Only the H1 candle store needs to live under tmp_path (isolating this test's writes);
    # config is read from the real project root, same as run_research's own default `root`
    # would for the risk/crypto-market settings this module actually needs.
    monkeypatch.setattr(research, "load_config", lambda root: real_load_config("."))

    idx = pd.date_range("2026-09-15 00:00", periods=60, freq="1h", tz="Asia/Kolkata")
    with CandleStore(tmp_path / "data/crypto.duckdb") as store:
        candles = [
            Candle(
                scrip_code="CDX_BTCINR", interval=Interval.H1, ts=stamp,
                open=100, high=101, low=99, close=100.5, volume=1000,
            )  # fmt: skip
            for stamp in idx
        ]
        store.upsert_candles(candles)
    output = tmp_path / "data/research"
    output.mkdir()
    primary = output / "latest.json"
    primary.write_text('{"protected": true}', encoding="utf-8")
    report = research.run_research(tmp_path, output, bars=60, n_cohorts=20)
    assert report["status"] == "historical_diagnostic_only"
    assert report["eligible_for_live"] is False
    assert report["market"] == "crypto"
    assert report["interval"] == Interval.H1.value
    assert (output / "crypto_intraday_research" / report["run_id"] / "report.json").exists()
    assert (output / "crypto_intraday_research/latest.json").exists()
    assert primary.read_text(encoding="utf-8") == '{"protected": true}'
    assert not any(k in INTRADAY_REGISTRY for k in CryptoSetupKind)


def test_run_research_raises_on_missing_history(tmp_path):
    (tmp_path / "data").mkdir()
    output = tmp_path / "data/research"
    with pytest.raises(RuntimeError, match="no crypto H1 history"):
        run_research(tmp_path, output)
